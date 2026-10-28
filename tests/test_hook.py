"""M4: hook.py wraps decide(): exit 2 on deny, one audit line per call, integrity at
SessionStart and on every call, budgets metered from transcripts, fail-closed."""

import io
import json
import shutil

import pytest

from swarm_toml.compile.claude_code import compile_swarm
from swarm_toml.enforce import hook, vote_mcp
from swarm_toml.enforce.budget import subagent_transcript, tokens_for, tokens_used
from swarm_toml.load import load

from .conftest import EXAMPLES


@pytest.fixture
def proj(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    shutil.copytree(EXAMPLES / "literature-review", d)
    compile_swarm(load(d), d)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(d))
    monkeypatch.setenv("SWARM_RUN_ID", "run-1")
    return d


def fire(monkeypatch, capsys, event, **data):
    data = {"hook_event_name": event, "session_id": "s", "cwd": "/nowhere", **data}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
    rc = hook.main([event])
    return rc, capsys.readouterr().err


def audit(proj):
    return [json.loads(x) for x in (proj / ".swarm/audit.jsonl").read_text().splitlines()]


def test_full_flow(proj, monkeypatch, capsys):
    cwd = str(proj)
    assert fire(monkeypatch, capsys, "SessionStart", agent_type="swarm-launcher")[0] == 0
    rc, _ = fire(monkeypatch, capsys, "PreToolUse", agent_type="swarm-launcher", cwd=cwd,
                 tool_name="Agent", tool_use_id="t1",
                 tool_input={"subagent_type": "researcher", "name": "r1", "prompt": "go"})
    assert rc == 0
    fire(monkeypatch, capsys, "SubagentStart", agent_type="researcher", agent_id="a1", cwd=cwd)
    rc, err = fire(monkeypatch, capsys, "PreToolUse", agent_type="researcher", agent_id="a1",
                   cwd=cwd, tool_name="Write",
                   tool_input={"file_path": f"{cwd}/report/final.md", "content": "x"})
    assert rc == 2 and "denied [permissions]" in err
    rc, err = fire(monkeypatch, capsys, "PreToolUse", agent_type="researcher", agent_id="a1",
                   cwd=cwd, tool_name="SendMessage", tool_input={"to": "nobody", "message": "x"})
    assert rc == 2 and "message-edges" in err
    recs = audit(proj)
    assert len(recs) == 5
    last = recs[-1]
    assert last["decision"] == "deny" and last["rule"] == "message-edges"
    assert last["chain"] == ["swarm-launcher", "researcher"] and last["run_id"] == "run-1"
    assert {"ts", "agent_id", "agent_type", "event", "tool", "target"} <= set(last)


def test_tampering_blocks_everything(proj, monkeypatch, capsys):
    fire(monkeypatch, capsys, "SessionStart", agent_type="swarm-launcher")
    (proj / "swarm.toml").write_text((proj / "swarm.toml").read_text() + "\n# edited\n")
    rc, err = fire(monkeypatch, capsys, "PreToolUse", agent_type="swarm-launcher",
                   cwd=str(proj), tool_name="SendMessage", tool_input={"to": "x"})
    assert rc == 2 and "integrity" in err


def test_tampered_profile_detected_at_start(proj, monkeypatch, capsys):
    prof = proj / ".agents/permissions.json"
    prof.write_text(prof.read_text().replace("Read(sources/**)", "Read"))
    fire(monkeypatch, capsys, "SessionStart", agent_type="swarm-launcher")
    rc, err = fire(monkeypatch, capsys, "PreToolUse", agent_type="swarm-launcher",
                   cwd=str(proj), tool_name="Agent", tool_input={})
    assert rc == 2 and "integrity" in err


def test_fail_closed_without_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    rc, err = fire(monkeypatch, capsys, "PreToolUse", tool_name="Read", tool_input={})
    assert rc == 2 and "enforcer error" in err
    assert fire(monkeypatch, capsys, "SubagentStop")[0] == 0  # non-blocking events never exit 2


def test_fail_closed_on_bad_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert hook.main(["PreToolUse"]) == 2


def test_event_name_from_argv(proj, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"agent_type": "swarm-launcher"})))
    assert hook.main(["SessionStart"]) == 0
    assert audit(proj)[0]["event"] == "SessionStart"


def test_project_dir_falls_back_to_cwd(monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert str(hook.project_dir({"cwd": "/x"})) == "/x"


def _transcript(path, msgs):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for m in msgs:
            f.write(json.dumps(m) + "\n")
        f.write("not json\n")


def test_tokens_counted_once_per_message_without_cache_reads(tmp_path):
    t = tmp_path / "s.jsonl"
    u = {"input_tokens": 10, "output_tokens": 5, "cache_creation_input_tokens": 100,
         "cache_read_input_tokens": 99999}
    _transcript(t, [{"message": {"id": "m1", "usage": u}}, {"message": {"id": "m1", "usage": u}},
                    {"message": {"id": "m2", "usage": {"output_tokens": 1}}},
                    {"type": "user", "message": {"content": "hi"}}, {"x": 1}])
    assert tokens_used(t) == 116
    assert tokens_used(None) is None and tokens_used(tmp_path / "missing") is None


def test_tokens_for_resolves_subagent_transcript(tmp_path):
    main = tmp_path / "sess.jsonl"
    _transcript(main, [{"message": {"id": "m", "usage": {"output_tokens": 7}}}])
    sub = subagent_transcript(str(main), "abc")
    assert sub == tmp_path / "sess" / "subagents" / "agent-abc.jsonl"
    _transcript(sub, [{"message": {"id": "m", "usage": {"output_tokens": 3}}}])
    assert tokens_for({"transcript_path": str(main)}) == 7
    assert tokens_for({"transcript_path": str(main), "agent_id": "abc"}) == 3
    assert tokens_for({"agent_transcript_path": str(sub)}) == 3


def test_budget_metered_into_ledger(proj, monkeypatch, capsys, tmp_path):
    main = tmp_path / "sess.jsonl"
    _transcript(main, [])
    cwd = str(proj)
    fire(monkeypatch, capsys, "SessionStart", agent_type="swarm-launcher")
    fire(monkeypatch, capsys, "PreToolUse", agent_type="swarm-launcher", cwd=cwd,
         tool_name="Agent", tool_input={"subagent_type": "researcher", "name": "r1"})
    fire(monkeypatch, capsys, "SubagentStart", agent_type="researcher", agent_id="a1")
    _transcript(subagent_transcript(str(main), "a1"),
                [{"message": {"id": "m", "usage": {"output_tokens": 300_001}}}])
    rc, err = fire(monkeypatch, capsys, "PreToolUse", agent_type="researcher", agent_id="a1",
                   cwd=cwd, transcript_path=str(main), tool_name="Read",
                   tool_input={"file_path": f"{cwd}/notes/a.md"})
    assert rc == 2 and "budget" in err


def test_vote_server_protocol():
    init = vote_mcp.respond({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "swarm"
    tools = vote_mcp.respond({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert {t["name"] for t in tools["result"]["tools"]} == {"request", "vote"}
    v = vote_mcp.respond({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                          "params": {"name": "vote", "arguments": {"request_id": "q"}}})
    assert "recorded vote on q" in v["result"]["content"][0]["text"]
    r = vote_mcp.respond({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                          "params": {"name": "request",
                                     "arguments": {"request_id": "q", "action": "a.b"}}})
    assert "opened request q" in r["result"]["content"][0]["text"]
    assert vote_mcp.respond({"jsonrpc": "2.0", "id": 5, "method": "ping"})["result"] == {}
    assert "error" in vote_mcp.respond({"jsonrpc": "2.0", "id": 6, "method": "nope"})
    assert vote_mcp.respond({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_vote_server_main(monkeypatch, capsys):
    lines = "\n".join([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}), "", "garbage",
                       json.dumps({"jsonrpc": "2.0", "method": "n"})])
    monkeypatch.setattr("sys.stdin", io.StringIO(lines + "\n"))
    assert vote_mcp.main() == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and json.loads(out[0])["id"] == 1
