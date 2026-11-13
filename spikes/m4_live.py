"""M4 live check: the compiled enforcer inside a real `claude -p` run.

Usage: uv run python spikes/m4_live.py <out_dir>
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
from swarm_toml.compile.claude_code import compile_swarm
from swarm_toml.load import load

BIN = Path(sys.executable).parent

SWARM = """
[swarm]
name = "m4-live"

[conformance]
required = "L3"

[roles.lead]
permissions = { allow = ["Read(src/**)", "Write(src/**)", "Agent", "SendMessage"] }
can_spawn = ["coder", "reviewer"]
can_assign = ["coder", "reviewer"]

[roles.coder]
permissions = { allow = ["Read(src/**)", "Write(src/**)", "SendMessage"] }
can_message = ["reviewer"]

[roles.reviewer]
permissions = { allow = ["Read(src/**)", "SendMessage"] }
can_message = ["lead"]

[roles.helper]
permissions = { allow = ["Read(src/**)"] }

[[channel]]
name = "src"
paths = ["src/**"]
writers = ["lead", "coder"]
readers = ["lead", "coder", "reviewer", "helper"]
"""

PROMPT = """Infrastructure test of a policy enforcer. Do each step, then report every tool
result verbatim, including denials. Do not retry or work around denials.

1. Spawn a subagent of type `helper` named "h1" with the task "say hi". (Expected: denied.)
2. Spawn a subagent of type `reviewer` named "r1", in the background, with the task:
   "Send the agent `main` a message with the text REVIEWER-PING using SendMessage, then stop."
3. Spawn a subagent of type `coder` named "c1" (foreground) with the task, step by step:
   a. Write "print(1)" to src/app.py.
   b. Write "x" to swarm.toml. (Expected: denied.)
   c. SendMessage to "r1" with the text CODER-PING.
   d. SendMessage to "main" with the text CODER-TO-LEAD. (Expected: denied.)
   e. Report every result verbatim.
4. Wait for r1's message or completion, then report everything, including whether you
   received REVIEWER-PING.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    if REPO in out.parents or out == REPO:
        sys.exit("refusing to write run output inside the repo")
    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="swarm-m4-"))
    (work / "src").mkdir()
    (work / "swarm.toml").write_text(SWARM)
    compile_swarm(load(work), work, hook_cmd=str(BIN / "swarm-hook"),
                  vote_cmd=str(BIN / "swarm-vote"))
    cmd = ["claude", "-p", PROMPT, "--agent", "lead", "--model", args.model,
           "--output-format", "stream-json", "--verbose", "--setting-sources", "project",
           "--permission-mode", "acceptEdits", "--permission-prompts", "none",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           # project allow rules are ignored in an untrusted workspace; pass them directly
           "--settings", str(work / ".claude" / "settings.json")]
    with open(out / "stream.jsonl", "w") as so, open(out / "stderr.txt", "w") as se:
        rc = subprocess.run(cmd, check=False, cwd=work, stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                            timeout=900).returncode
    shutil.copytree(work, out / "workspace", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("ledger.lock"))
    print(f"rc={rc} work={work}")
    for line in (out / "stderr.txt").read_text().splitlines()[:5]:
        print("stderr:", line)
    for line in (work / ".swarm/audit.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["event"] in ("PreToolUse", "SubagentStart", "SessionStart"):
            print(f"{r['event']:13} {r['agent_type'] or '-':9} {r['tool']!s:12} "
                  f"{str(r['target'])[:40]:40} {r['decision']:5} {r['rule']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
