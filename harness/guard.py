"""Harness guard hook, installed in EVERY condition (C0-C3b). Not a condition variable.

1. Safety: denies SendMessage to any name not spawned in this run (or "main"). Claude Code
   subagents can otherwise reach other local Claude sessions (docs/M0-findings.md #11).
2. Observation: logs spawns, subagent starts and messages to .harness/events.jsonl, so the
   oracle can score conditions that have no swarm.toml enforcer (C0, C1).

Usage (hook command): python -m harness.guard <EventName>, hook JSON on stdin.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    data = json.loads(sys.stdin.read() or "{}")
    event = data.get("hook_event_name") or (argv[0] if argv else "")
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")
    hdir = root / ".harness"
    hdir.mkdir(exist_ok=True)
    tool, ti = data.get("tool_name"), data.get("tool_input") or {}
    deny = None
    with (hdir / "guard.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        names_path = hdir / "names.json"
        names = set(json.loads(names_path.read_text())) if names_path.exists() else {"main"}
        if event == "PreToolUse" and tool == "Agent" and ti.get("name"):
            names.add(ti["name"])
            names_path.write_text(json.dumps(sorted(names)))
        if event == "PreToolUse" and tool == "SendMessage":
            to = ti.get("to") or ti.get("recipient")
            if to not in names:
                deny = f"harness guard: {to!r} is not an agent in this run"
        rec = {"ts": time.time(), "event": event, "agent_id": data.get("agent_id"),
               "agent_type": data.get("agent_type"), "tool": tool,
               "subagent_type": ti.get("subagent_type"), "name": ti.get("name"),
               "to": ti.get("to") or ti.get("recipient"), "denied": deny}
        with (hdir / "events.jsonl").open("a") as f:
            f.write(json.dumps(rec) + "\n")
    if deny:
        print(deny, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
