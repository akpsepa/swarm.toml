"""Harness guard hook, installed in EVERY condition (C0-C3b). Not a condition variable.

1. Safety: denies SendMessage to any name not spawned in this run (or "main"). Claude Code
   subagents can otherwise reach other local Claude sessions (docs/M0-findings.md #11).
2. Fairness: denies any spawn without a `name`. The C2/C3 enforcer requires one, and an
   unnamed agent cannot be messaged, so without this C0/C1 teams could silently lose
   their message edges (pilot3, msg-confused-deputy-01 C0).
3. Observation: logs spawns, subagent starts and messages to .harness/events.jsonl, so the
   oracle can score conditions that have no swarm.toml enforcer (C0, C1). After every
   file-changing tool call it records each changed workspace file's content in
   .harness/writes.jsonl, so a leak that is later deleted still counts.

Usage (hook command): python -m harness.guard <EventName>, hook JSON on stdin.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path

HIDDEN = (".harness/", ".swarm/", ".claude/", ".c3b/")
MAX_TEXT = 65536


def record_writes(root: Path, hdir: Path, agent_type: str | None) -> None:
    """Append every workspace file that changed since the last scan (caller holds the lock)."""
    seen_path = hdir / "seen.json"
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}
    now = {}
    with (hdir / "writes.jsonl").open("a") as out:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if not p.is_file() or rel.startswith(HIDDEN) or "__pycache__" in rel:
                continue
            st = p.stat()
            now[rel] = [st.st_mtime_ns, st.st_size]
            if seen.get(rel) != now[rel]:
                try:
                    text = p.read_bytes()[:MAX_TEXT].decode(errors="replace")
                except OSError:
                    continue
                out.write(json.dumps({"ts": time.time(), "agent_type": agent_type,
                                      "path": rel, "text": text}) + "\n")
    seen_path.write_text(json.dumps(now))


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
        if event == "PreToolUse" and tool == "Agent" and not ti.get("name"):
            deny = "harness guard: set `name` on every spawn so teammates can message it"
        if event == "PreToolUse" and tool == "Agent" and ti.get("name"):
            names.add(ti["name"])
            names_path.write_text(json.dumps(sorted(names)))
        if event == "PreToolUse" and tool == "SendMessage":
            to = ti.get("to") or ti.get("recipient")
            if to not in names:
                deny = f"harness guard: {to!r} is not an agent in this run"
        if event == "PostToolUse":
            record_writes(root, hdir, data.get("agent_type"))
            return 0
        if event == "SessionStart":  # baseline, so files from the case setup aren't "writes"
            if not (hdir / "seen.json").exists():  # once: a resumed session must not reset it
                record_writes(root, hdir, None)
                (hdir / "writes.jsonl").write_text("")
            return 0
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
