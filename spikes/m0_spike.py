"""M0 spike: 3-agent toy swarm under `claude -p`, every hook input logged.

Usage: uv run python spikes/m0_spike.py <out_dir> [--model sonnet]

Runs in a fresh temp directory outside the repo. Permissions are an explicit allowlist
(acceptEdits + --permission-prompts none: anything that would prompt is denied);
no permission bypass. MCP is limited to an empty strict config.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOGGER = REPO / "spikes" / "hook_logger.py"
EVENTS = ["PreToolUse", "PostToolUse", "SessionStart", "SubagentStart", "SubagentStop",
          "Stop", "UserPromptSubmit"]
ALLOW = ["Read", "Write", "Edit", "Bash(echo:*)", "Task", "Agent", "SendMessage",
         "ToolSearch", "TaskCreate", "TaskUpdate", "TaskList", "ListAgents"]

AGENTS = {
    "lead": """---
name: lead
description: Swarm lead. Coordinates alpha and beta.
---
You are the lead of a three-agent test swarm. Follow the user's instructions exactly.
""",
    "alpha": """---
name: alpha
description: Worker alpha. Performs file operations and messages beta.
---
You are worker alpha in a test swarm. Do exactly the steps you are given, in order,
and report each tool result verbatim (including any errors).
""",
    "beta": """---
name: beta
description: Worker beta. Receives messages and records them.
---
You are worker beta in a test swarm. Do exactly the steps you are given.
""",
}

PROMPT = """This is an infrastructure test. Do these steps in order and do not skip any.

1. Spawn a subagent of type `beta` and give it the name "beta". Run it in the background.
   Its task: "Wait until you receive a message from alpha. When you do, write its exact text to
   beta_inbox.txt using the Write tool. If you get no message, write NO-MESSAGE to beta_inbox.txt."
2. Spawn a subagent of type `alpha` and give it the name "alpha". Its task, step by step:
   a. Read seed.txt with the Read tool.
   b. Run `echo alpha-bash-ok` with the Bash tool.
   c. Write the text "alpha-was-here" to alpha.txt with the Write tool.
   d. Edit alpha.txt with the Edit tool, replacing "was" with "is".
   e. Try to Write the text "x" to BLOCKED.txt. Report the exact error you get.
   f. If a task-creation tool (TaskCreate or similar) is available, possibly via ToolSearch,
      create a task titled "spike-task". Otherwise say it is not available.
   g. Use the SendMessage tool to send beta (the agent named "beta") the message "PING-M0-4471".
   h. Report results for every step.
3. Wait for both agents to finish, then report: alpha's full report, whether beta_inbox.txt
   exists and its contents, and the full list of tool names you yourself have available.
"""

NESTED_PROMPT = """This is an infrastructure test. Spawn a subagent of type `alpha` named "alpha"
(foreground, not background). Its task: "Spawn a subagent of type `beta` named "beta" with the
task: write the text NESTED-OK to nested.txt. Wait for it, then report whether you had the Agent
tool and what happened." Then report alpha's answer verbatim."""
SCENARIOS = {"peer": PROMPT, "nested": NESTED_PROMPT}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--model", default="claude-sonnet-5-5")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="peer")
    ap.add_argument("--persist", action="store_true",
                    help="keep session transcripts (needed for transcript_path metering)")
    args = ap.parse_args()
    out = Path(args.out_dir).resolve()
    if REPO in out.parents or out == REPO:
        sys.exit("refusing to write run output inside the repo")
    out.mkdir(parents=True, exist_ok=True)

    work = Path(tempfile.mkdtemp(prefix="swarm-m0-"))
    (work / ".claude" / "agents").mkdir(parents=True)
    for name, body in AGENTS.items():
        (work / ".claude" / "agents" / f"{name}.md").write_text(body)
    (work / "seed.txt").write_text("seed-content-91\n")
    hook = {"type": "command", "command": f"{sys.executable} {LOGGER}"}
    hooks = {ev: [{"matcher": "*", "hooks": [hook]} if ev in ("PreToolUse", "PostToolUse")
                  else {"hooks": [hook]}] for ev in EVENTS}
    settings = {"permissions": {"allow": ALLOW}, "hooks": hooks}
    (work / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2))

    log = out / "hooks.jsonl"
    env = {**os.environ, "SPIKE_LOG": str(log)}
    cmd = ["claude", "-p", SCENARIOS[args.scenario], "--agent", "lead", "--model", args.model,
           "--output-format", "stream-json", "--verbose", "--setting-sources", "project",
           "--permission-mode", "acceptEdits", "--permission-prompts", "none",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           *([] if args.persist else ["--no-session-persistence"])]
    with open(out / "stream.jsonl", "w") as so, open(out / "stderr.txt", "w") as se:
        try:
            rc = subprocess.run(cmd, cwd=work, env=env, stdout=so, stderr=se,
                                timeout=args.timeout).returncode
        except subprocess.TimeoutExpired:
            rc = "timeout"
    shutil.copytree(work, out / "workspace", dirs_exist_ok=True)
    print(f"rc={rc} work={work} out={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
