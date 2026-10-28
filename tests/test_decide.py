"""M4: decide() is pure and every branch is tested.

Policies are compiled from small swarm.toml texts with the real compiler, so these tests
also pin the policy format the hook reads."""

import copy

import pytest

from swarm_toml.compile.claude_code import build_policy, conformance
from swarm_toml.enforce.decide import (
    ROOT,
    Decision,
    HookEvent,
    _rel,
    chain,
    decide,
    new_ledger,
)
from swarm_toml.graph import Graph
from swarm_toml.load import load

CWD = "/work"

HIER = """
[roles.lead]
permissions = { allow = ["Read(src/**)", "Write(src/**)", "Bash(git status)", "Bash(uv run:*)",
                         "mcp__deploy__run", "Agent", "SendMessage"] }
can_spawn = ["coder", "reviewer"]
can_assign = ["coder", "reviewer"]
can_message = ["reviewer"]
budget = { tokens = 1_000 }

[roles.coder]
permissions = { allow = ["Read(src/**)", "Write(src/**)", "Bash(uv run:*)", "mcp__deploy__run",
                         "SendMessage"] }
max_instances = 2
can_message = ["reviewer"]
budget = { tokens = 400 }

[roles.reviewer]
permissions = { allow = ["Read(src/**)", "Read(notes/**)", "SendMessage"] }
can_message = ["lead"]
budget = { tokens = 100 }

[roles.helper]
permissions = { allow = ["Read(src/**)"] }

[[channel]]
name = "notes"
paths = ["notes/**"]
writers = ["lead"]
readers = ["reviewer"]

[delegation]
max_depth = 1
non_delegable = ["deploy.prod"]

[actions]
"deploy.prod" = ["mcp__deploy__run"]
"git.push" = ["Bash(git push:*)"]
"""

QUORUM = """
[roles.lead]
permissions = { allow = ["Read(report/**)", "Agent", "SendMessage"] }
can_spawn = ["editor", "reviewer"]
can_assign = ["editor", "reviewer"]

[roles.editor]
permissions = { allow = ["Read(report/**)", "Write(report/**)", "Agent", "SendMessage"] }
can_spawn = ["reviewer"]
can_assign = ["reviewer"]

[roles.reviewer]
permissions = { allow = ["Read(report/**)", "SendMessage"] }
max_instances = 4

[[collective]]
action = "report.publish"
requested_by = "editor"
requires_quorum = { of = "reviewer", min = 2 }

[[collective]]
action = "report.retract"
requested_by = "editor"
requires_quorum = { of = "reviewer", min = 1, independent = false }

[delegation]
max_depth = 3

[actions]
"report.publish" = ["Write(report/published/**)"]
"report.retract" = ["Write(report/retracted/**)"]
"""

AGG = """
[roles.lead]
permissions = { allow = ["Read(data/**)", "mcp__crm__read", "Agent", "SendMessage"] }
can_spawn = ["a", "b"]
can_assign = ["a", "b"]
can_message = ["a"]

[roles.a]
permissions = { allow = ["Read(data/**)", "mcp__crm__read", "Grep", "SendMessage"] }
max_instances = 2
can_message = ["a", "b", "lead"]

[roles.b]
permissions = { allow = ["Read(data/**)", "SendMessage"] }
can_message = ["a"]

[labels]
pii = { paths = ["data/pii/**"], tools = ["crm.read"] }
logs = { tools = ["mcp__logs__read"] }

[[aggregation]]
labels = ["pii"]
merge_by = "lead"

[actions]
"crm.read" = ["mcp__crm__read"]
"""

FLAT = """
[roles.peer]
permissions = { allow = ["Read(notes/**)", "SendMessage"] }
max_instances = 2
can_message = ["peer"]

[roles.other]
can_message = ["peer"]
"""


def policy_from(tmp_path, text, name="s"):
    d = tmp_path / name
    d.mkdir()
    (d / "swarm.toml").write_text(text)
    g = Graph(load(d))
    return build_policy(g, conformance(g))


@pytest.fixture
def hier(tmp_path):
    return policy_from(tmp_path, HIER)


class Run:
    """Replays hook events through decide(), threading the ledger."""

    def __init__(self, policy, root="lead"):
        self.p = policy
        self.led = new_ledger()
        self.d: Decision | None = None
        self.n = 0
        if root:
            assert self.ev("SessionStart", agent_type=root).allow

    def ev(self, event, facts=None, **kw):
        f = {"hash": self.p["hash"]} if facts is None else facts
        before = copy.deepcopy(self.led)
        self.d = decide(self.p, HookEvent(event=event, cwd=CWD, facts=f, **kw), self.led)
        assert self.led == before, "decide must not mutate its ledger argument"
        self.led = self.d.ledger
        return self.d

    def call(self, tool, who=None, role=None, facts=None, **ti):
        inst = self.led["instances"].get(who or ROOT, {})
        return self.ev("PreToolUse", facts=facts, agent_id=who,
                       agent_type=role or (inst.get("role") if who else None),
                       tool_name=tool, tool_input=ti, tool_use_id=f"tu{self.n}")

    def spawn(self, role, name, who=None, aid=None):
        self.n += 1
        d = self.call("Agent", who=who, subagent_type=role, name=name, prompt="go")
        if d.allow:
            assert self.ev("SubagentStart", agent_id=aid or f"id-{name}", agent_type=role).allow
        return d


# --------------------------------------------------------------- session and identity

def test_session_start_and_hash(hier):
    r = Run(hier)
    assert r.led["instances"][ROOT]["role"] == "lead"
    assert r.ev("SessionStart", agent_type="lead").allow  # resume: no second root


def test_session_start_bad_hash_locks(hier):
    r = Run(hier, root=None)
    d = r.ev("SessionStart", facts={"hash": "sha256:other"}, agent_type="lead")
    assert not d.allow and d.rule == "integrity"
    d = r.call("Read", file_path="/work/src/a.py")
    assert not d.allow and d.rule == "integrity"


def test_session_start_wrong_root_locks(hier):
    r = Run(hier, root=None)
    d = r.ev("SessionStart", agent_type="coder")
    assert not d.allow and d.rule == "identity"
    assert r.call("Read", file_path="src/a.py").rule == "identity"


def test_hash_change_mid_run_locks(hier):
    r = Run(hier)
    d = r.call("Read", facts={"hash": "sha256:edited"}, file_path="src/a.py")
    assert not d.allow and d.rule == "integrity"
    assert r.led["locked"].startswith("integrity")


def test_unknown_instance_denied(hier):
    r = Run(hier)
    d = r.call("Read", who="ghost", role="coder", file_path="src/a.py")
    assert not d.allow and d.rule == "identity"


def test_role_mismatch_denied(hier):
    r = Run(hier)
    r.spawn("coder", "c1")
    d = r.call("Read", who="id-c1", role="reviewer", file_path="src/a.py")
    assert not d.allow and d.rule == "identity"


def test_unbound_subagent_is_refused(hier):
    r = Run(hier)
    d = r.ev("SubagentStart", agent_id="rogue", agent_type="coder")
    assert not d.allow
    assert r.call("Read", who="rogue", role="coder", file_path="src/a.py").rule == "identity"


def test_revival_keeps_binding(hier):
    r = Run(hier)
    r.spawn("coder", "c1")
    d = r.ev("SubagentStart", agent_id="id-c1", agent_type="coder")
    assert d.allow and d.rule == "revive"
    assert r.led["counts"]["coder"] == 1


def test_other_events_observed(hier):
    r = Run(hier)
    assert r.ev("PostToolUse", tool_name="Agent").rule == "observe"


def test_empty_ledger_defaults(hier):
    d = decide(hier, HookEvent(event="SessionStart", agent_type="lead",
                               facts={"hash": hier["hash"]}), {})
    assert d.allow and ROOT in d.ledger["instances"]


# --------------------------------------------------------------------------- spawn

def test_spawn_allowed_and_bound(hier):
    r = Run(hier)
    assert r.spawn("coder", "c1").allow
    inst = r.led["instances"]["id-c1"]
    assert inst["parent"] == ROOT and inst["depth"] == 1 and inst["name"] == "c1"
    assert chain(r.led, "id-c1") == ["lead", "coder"]
    assert r.led["instances"][ROOT]["reserved"] == 400


@pytest.mark.parametrize("role,name,rule", [
    ("ghost", "g", "default-deny"),
    ("helper", "h", "spawn-edges"),
    ("coder", None, "identity"),
])
def test_spawn_denials(hier, role, name, rule):
    r = Run(hier)
    d = r.spawn(role, name)
    assert not d.allow and d.rule == rule


def test_spawn_needs_assign(tmp_path):
    p = policy_from(tmp_path, """
[roles.lead]
permissions = { allow = ["Agent"] }
can_spawn = ["w"]
[roles.w]
permissions = { allow = ["Read(src/**)"] }
""")
    r = Run(p)
    assert r.spawn("w", "w1").rule == "assign-edges"


def test_spawn_duplicate_name(hier):
    r = Run(hier)
    r.spawn("coder", "c1")
    assert r.spawn("coder", "c1").rule == "identity"
    r2 = Run(hier)
    r2.call("Agent", subagent_type="coder", name="dup", prompt="x")  # still pending
    assert r2.call("Agent", subagent_type="coder", name="dup", prompt="x").rule == "identity"


def test_spawn_max_depth(hier):
    r = Run(hier)
    r.spawn("coder", "c1")
    # coder cannot spawn at all; give it a spawn edge via a policy edit to reach depth 2
    hier2 = copy.deepcopy(hier)
    hier2["roles"]["coder"]["can_spawn"] = ["helper"]
    hier2["roles"]["coder"]["can_assign"] = ["helper"]
    d = decide(hier2, HookEvent("PreToolUse", agent_type="coder", agent_id="id-c1",
                                tool_name="Agent", cwd=CWD, facts={"hash": hier["hash"]},
                                tool_input={"subagent_type": "helper", "name": "h1"}), r.led)
    assert not d.allow and d.rule == "max-depth"


def test_spawn_max_instances_counts_pending(hier):
    r = Run(hier)
    r.spawn("coder", "c1")
    r.call("Agent", subagent_type="coder", name="c2", prompt="x")  # pending, not started
    d = r.call("Agent", subagent_type="coder", name="c3", prompt="x")
    assert not d.allow and d.rule == "max-instances"


def test_spawn_budget_reservation(hier):
    r = Run(hier)
    r.spawn("coder", "c1")  # reserves 400 of 1000
    r.spawn("reviewer", "r1")  # reserves 100
    r.call("Read", facts={"hash": hier["hash"], "tokens_used": 200}, file_path="src/a.py")
    d = r.spawn("coder", "c2")  # 1000 - 200 - 500 = 300 < 400
    assert not d.allow and d.rule == "budget"


def test_spawn_without_parent_budget(tmp_path):
    p = policy_from(tmp_path, FLAT.replace('[roles.other]', '[roles.lead]\npermissions = '
                    '{ allow = ["Agent"] }\ncan_spawn = ["peer"]\ncan_assign = ["peer"]\n'
                    '[roles.other]'))
    r = Run(p)
    assert r.spawn("peer", "p1").allow
    assert r.led["instances"][ROOT]["reserved"] == 0


def test_launcher_root(tmp_path):
    p = policy_from(tmp_path, FLAT)
    assert p["launcher"] == "swarm-launcher"
    r = Run(p, root="swarm-launcher")
    assert r.led["instances"][ROOT]["depth"] == -1
    assert r.spawn("peer", "p1").allow
    assert r.led["instances"]["id-p1"]["depth"] == 0
    assert r.spawn("swarm-launcher", "x").rule == "default-deny"


# ------------------------------------------------------------------------- messages

def test_message_allowed_and_denied(hier):
    r = Run(hier)
    r.spawn("coder", "c1")
    r.spawn("reviewer", "r1")
    assert r.call("SendMessage", who="id-c1", to="r1", message="hi").allow
    d = r.call("SendMessage", who="id-r1", to="c1", message="hi")
    assert not d.allow and d.rule == "message-edges"


def test_message_via_assign_edge(hier):
    r = Run(hier)
    r.spawn("coder", "c1")
    assert r.call("SendMessage", to="c1", message="new task").allow  # lead assigns coder


@pytest.mark.parametrize("to", [None, "*", "nobody"])
def test_message_bad_recipient(hier, to):
    r = Run(hier)
    d = r.call("SendMessage", to=to, message="x")
    assert not d.allow and d.rule == "message-edges"


def test_message_recipient_field(hier):
    r = Run(hier)
    r.spawn("reviewer", "r1")
    assert r.call("SendMessage", recipient="r1", content="x").allow


# --------------------------------------------------------------- files and permissions

def test_file_permissions(hier):
    r = Run(hier)
    assert r.call("Read", file_path="/work/src/a.py").allow
    assert r.call("Read", file_path="/private/work/src/a.py").allow  # macOS /private alias
    d = r.call("Read", file_path="/work/secrets/x")
    assert not d.allow and d.rule == "permissions"


def test_path_outside_project(hier):
    r = Run(hier)
    d = r.call("Read", file_path="/etc/passwd")
    assert not d.allow and d.rule == "default-deny"


def test_protected_write(hier):
    r = Run(hier)
    for p in ["swarm.toml", "/work/.claude/settings.json", ".swarm/ledger.json"]:
        d = r.call("Write", file_path=p, content="x")
        assert not d.allow and d.rule == "integrity"


def test_channels(hier):
    r = Run(hier)
    r.spawn("reviewer", "r1")
    hier2 = copy.deepcopy(hier)
    hier2["roles"]["lead"]["allow"] += ["Write(notes/**)"]
    hier2["roles"]["coder"]["allow"] += ["Read(notes/**)"]
    r.p = hier2
    assert r.call("Write", file_path="notes/n.md", content="x").allow
    assert r.call("Read", who="id-r1", file_path="notes/n.md").allow
    r.spawn("coder", "c1")
    d = r.call("Read", who="id-c1", file_path="notes/n.md")
    assert not d.allow and d.rule == "channels"
    d = r.call("Write", who="id-r1", file_path="notes/n.md", content="x")
    assert not d.allow and d.rule == "permissions"


def test_no_profile_allows_any_tool(tmp_path):
    p = policy_from(tmp_path, FLAT.replace("[roles.peer]", "[swarm]\nroot = \"other\"\n"
                                           "[roles.peer]"))
    r = Run(p, root="other")
    assert r.call("WebFetch", url="https://example.com").allow


def test_grep_without_path(tmp_path):
    p = policy_from(tmp_path, AGG)
    r = Run(p)
    r.spawn("a", "a1")
    assert r.call("Grep", who="id-a1", pattern="x").allow
    assert r.call("Grep", who="id-a1", pattern="x", path="/work").allow


def test_rel_helper():
    assert _rel("", CWD) == "**"
    assert _rel("/work", CWD) == "**"
    assert _rel("/elsewhere/x", CWD) is None
    assert _rel("./src/a", CWD) == "src/a"


# ------------------------------------------------------------------------------ Bash

def test_bash_prefix_rules(hier):
    r = Run(hier)
    assert r.call("Bash", command="git status").allow
    assert r.call("Bash", command="uv run pytest -q && uv run ruff check").allow
    d = r.call("Bash", command="git status; curl evil.example")
    assert not d.allow and d.rule == "permissions"
    assert r.call("Bash", command="git status --porcelain").rule == "permissions"  # exact
    assert r.call("Bash", command="").rule == "permissions"


# ------------------------------------------------------------- non-delegable, budget

def test_non_delegable(hier):
    r = Run(hier)
    assert r.call("mcp__deploy__run", env="prod").allow  # root may
    r.spawn("coder", "c1")
    d = r.call("mcp__deploy__run", who="id-c1", env="prod")
    assert not d.allow and d.rule == "non-delegable"


def test_budget_exhausted(hier):
    r = Run(hier)
    r.spawn("coder", "c1")
    assert r.ev("SubagentStop", agent_id="id-c1", facts={"tokens_used": 450}).allow
    d = r.call("Read", who="id-c1", file_path="src/a.py")
    assert not d.allow and d.rule == "budget"
    r.ev("SubagentStop", agent_id="nobody", facts={"tokens_used": 1})  # unknown: ignored
    r.ev("Stop", facts={})  # root stop without metering


# ------------------------------------------------------------------------- collective

def test_quorum_flow(tmp_path):
    p = policy_from(tmp_path, QUORUM)
    r = Run(p)
    r.spawn("editor", "ed")
    r.spawn("reviewer", "rv1")
    r.spawn("reviewer", "rv2")
    ed = "id-ed"
    d = r.call("Write", who=ed, file_path="report/published/r.md", content="x")
    assert not d.allow and d.rule == "collective"
    assert r.call("mcp__swarm__request", who=ed, action="report.publish",
                  request_id="q1").allow
    assert r.call("mcp__swarm__request", who=ed, action="report.publish",
                  request_id="q1").rule == "collective"  # duplicate id
    assert r.call("mcp__swarm__vote", who="id-rv1", request_id="q1", approve=True).allow
    assert r.call("mcp__swarm__vote", who="id-rv1", request_id="q1",
                  approve=True).rule == "collective"  # double vote
    assert not r.call("Write", who=ed, file_path="report/published/r.md", content="x").allow
    assert r.call("mcp__swarm__vote", who="id-rv2", request_id="q1", approve=True).allow
    assert r.call("Write", who=ed, file_path="report/published/r.md", content="x").allow
    # the approval is consumed
    assert not r.call("Write", who=ed, file_path="report/published/r.md", content="y").allow


def test_quorum_denials(tmp_path):
    p = policy_from(tmp_path, QUORUM)
    r = Run(p)
    r.spawn("editor", "ed")
    r.spawn("reviewer", "rv1")
    assert r.call("mcp__swarm__request", action="report.publish",
                  request_id="q").rule == "collective"  # lead is not the requester
    assert r.call("mcp__swarm__vote", who="id-rv1", request_id="none",
                  approve=True).rule == "collective"
    r.call("mcp__swarm__request", who="id-ed", action="report.publish", request_id="q2")
    assert r.call("mcp__swarm__vote", who="id-ed", request_id="q2",
                  approve=True).rule == "collective"  # not a voter role
    r.p = copy.deepcopy(p)
    r.p["roles"]["lead"]["allow"].append("Write(report/**)")
    d = r.call("Write", file_path="report/published/x", content="x")
    assert not d.allow and "only 'editor'" in d.reason  # wrong role performs the action


def test_sybil_votes_rejected(tmp_path):
    p = policy_from(tmp_path, QUORUM)
    r = Run(p)
    r.spawn("editor", "ed")
    r.spawn("reviewer", "own", who="id-ed")  # spawned by the requester
    r.call("mcp__swarm__request", who="id-ed", action="report.publish", request_id="q")
    d = r.call("mcp__swarm__vote", who="id-own", request_id="q", approve=True)
    assert not d.allow and "Sybil" in d.reason
    # a non-independent rule accepts the same voter
    r.call("mcp__swarm__request", who="id-ed", action="report.retract", request_id="q3")
    assert r.call("mcp__swarm__vote", who="id-own", request_id="q3", approve=True).allow
    assert r.call("Write", who="id-ed", file_path="report/retracted/x", content="x").allow


def test_rejecting_votes_do_not_count(tmp_path):
    p = policy_from(tmp_path, QUORUM.replace("min = 2", "min = 1"))
    r = Run(p)
    r.spawn("editor", "ed")
    r.spawn("reviewer", "rv1")
    r.call("mcp__swarm__request", who="id-ed", action="report.publish", request_id="q")
    r.call("mcp__swarm__vote", who="id-rv1", request_id="q", approve=False)
    assert not r.call("Write", who="id-ed", file_path="report/published/x",
                      content="x").allow


def test_descends_from_handles_missing_parent(tmp_path):
    p = policy_from(tmp_path, QUORUM)
    r = Run(p)
    r.spawn("editor", "ed")
    r.spawn("reviewer", "rv1")
    r.call("mcp__swarm__request", who="id-ed", action="report.publish", request_id="q")
    led = copy.deepcopy(r.led)
    led["instances"]["id-rv1"]["parent"] = "vanished"
    d = decide(p, HookEvent("PreToolUse", agent_type="reviewer", agent_id="id-rv1",
                            tool_name="mcp__swarm__vote", cwd=CWD, facts={"hash": p["hash"]},
                            tool_input={"request_id": "q", "approve": True}), led)
    assert d.allow
    assert chain(led, "id-rv1") == ["reviewer"]


# ------------------------------------------------------------------------ aggregation

def test_aggregation_direct_reads(tmp_path):
    p = policy_from(tmp_path, AGG)
    r = Run(p)
    r.spawn("a", "a1")
    assert r.call("Read", who="id-a1", file_path="data/pii/one.csv").allow
    assert r.call("Read", who="id-a1", file_path="data/pii/two.csv").allow  # same source
    d = r.call("mcp__crm__read", who="id-a1", q="x")  # second source
    assert not d.allow and d.rule == "aggregation"
    assert r.call("Read", file_path="data/pii/one.csv").allow  # lead merges
    assert r.call("mcp__crm__read", q="x").allow
    assert r.call("Read", who="id-a1", file_path="data/public.csv").allow  # unlabelled


def test_aggregation_through_messages(tmp_path):
    p = policy_from(tmp_path, AGG)
    r = Run(p)
    r.spawn("a", "a1")
    r.spawn("a", "a2")
    r.spawn("b", "b1")
    r.call("Read", who="id-a1", file_path="data/pii/x")
    r.call("Read", who="id-a2", file_path="data/pii/y")
    assert r.call("SendMessage", who="id-a1", to="b1", message="part 1").allow
    d = r.call("SendMessage", who="id-a2", to="b1", message="part 2")
    assert not d.allow and d.rule == "aggregation"
    assert r.call("SendMessage", who="id-a1", to="main", message="part 1").allow
    assert r.call("SendMessage", who="id-a2", to="main", message="part 2").allow  # merger
    assert r.call("SendMessage", who="id-b1", to="a1", message="no data").allow


def test_merged_output_counts_once(tmp_path):
    p = policy_from(tmp_path, AGG)
    r = Run(p)
    r.spawn("a", "a1")
    r.call("Read", file_path="data/pii/x")
    r.call("mcp__crm__read", q="y")
    assert r.call("SendMessage", to="a1", message="merged summary").allow
    assert r.call("SendMessage", to="a1", message="again").allow
    d = r.call("Read", who="id-a1", file_path="data/pii/z")  # merged + a raw fragment
    assert not d.allow and d.rule == "aggregation"


def test_label_tool_as_rule(tmp_path):
    text = AGG.replace('logs = { tools = ["mcp__logs__read"] }', '') + \
        '[labels.audit]\ntools = ["mcp__audit__read"]\n'
    text = text.replace('labels = ["pii"]', 'labels = ["pii", "audit"]')
    text = text.replace('"mcp__crm__read", "Grep"', '"mcp__crm__read", "mcp__audit__read", "Grep"')
    p = policy_from(tmp_path, text)
    r = Run(p)
    r.spawn("a", "a1")
    assert r.call("mcp__audit__read", who="id-a1").allow
    assert r.call("mcp__crm__read", who="id-a1").rule == "aggregation"


def test_unknown_label_ignored_by_runtime(tmp_path):
    p = policy_from(tmp_path, AGG)
    p = copy.deepcopy(p)
    p["aggregation"].append({"labels": ["nope"], "merge_by": "lead"})
    r = Run(p)
    r.spawn("a", "a1")
    assert r.call("Read", who="id-a1", file_path="data/pii/x").allow


def test_from_hook_parses_claude_code_input():
    raw = {"hook_event_name": "PreToolUse", "agent_type": "coder", "agent_id": "a1",
           "tool_name": "SendMessage", "tool_input": {"to": "r"}, "tool_use_id": "t",
           "cwd": "/w", "session_id": "s"}
    ev = HookEvent.from_hook(raw, {"hash": "h"})
    assert (ev.event, ev.agent_id, ev.tool_input, ev.facts) == ("PreToolUse", "a1",
                                                               {"to": "r"}, {"hash": "h"})
    assert HookEvent.from_hook({}).event == ""


def test_patterned_non_file_rule_does_not_match(tmp_path):
    p = policy_from(tmp_path, """
[roles.lead]
permissions = { allow = ["WebFetch(domain:example.com)"] }
""")
    r = Run(p)
    assert r.call("WebFetch", url="https://example.com").rule == "permissions"


def test_binding_skips_other_pending_roles(hier):
    r = Run(hier)
    r.call("Agent", subagent_type="coder", name="c1", prompt="x")
    r.call("Agent", subagent_type="reviewer", name="r1", prompt="x")
    assert r.ev("SubagentStart", agent_id="rid", agent_type="reviewer").allow
    assert r.led["instances"]["rid"]["name"] == "r1"
    assert [p["name"] for p in r.led["pending"]] == ["c1"]


def test_root_is_addressed_as_main(hier):
    r = Run(hier)
    r.spawn("reviewer", "r1")
    assert r.call("SendMessage", who="id-r1", to="main", message="done").allow
    assert r.call("SendMessage", who="id-r1", to="lead", message="x").rule == "message-edges"


def test_label_in_second_aggregation(tmp_path):
    text = AGG + '[labels.ops]\ntools = ["mcp__ops__read"]\n[[aggregation]]\nlabels = ["ops"]\nmerge_by = "b"\n'
    text = text.replace('"Read(data/**)", "SendMessage"] }\ncan_message = ["a"]',
                        '"Read(data/**)", "mcp__ops__read", "SendMessage"] }\ncan_message = ["a"]')
    p = policy_from(tmp_path, text)
    r = Run(p)
    r.spawn("b", "b1")
    assert r.call("mcp__ops__read", who="id-b1").allow
    assert r.call("Read", who="id-b1", file_path="data/pii/x").allow  # different merger
