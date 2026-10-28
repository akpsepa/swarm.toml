"""Claude Code hook entry point: `swarm-hook <EventName>` with the hook JSON on stdin.

Wraps decide(): loads .swarm/policy.json, recomputes the policy hash, meters tokens,
takes an exclusive lock on the ledger (parallel subagents run hooks concurrently),
decides, writes the ledger and one audit line. Exit 2 with the reason on stderr denies
a PreToolUse call. Any failure to load or verify the policy denies (fail-closed).
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from pathlib import Path

from ..load import digest_files
from . import audit
from .budget import tokens_for
from .decide import Decision, HookEvent, decide, new_ledger

BLOCKING = {"PreToolUse"}


def project_dir(data: dict) -> Path:
    return Path(os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or ".")


def handle(data: dict, root: Path, run_id: str | None = None) -> Decision:
    swarm_dir = root / ".swarm"
    policy = json.loads((swarm_dir / "policy.json").read_text())
    facts: dict = {"hash": digest_files(root, policy.get("referenced", []))}
    tokens = tokens_for(data)
    if tokens is not None:
        facts["tokens_used"] = tokens
    ev = HookEvent.from_hook(data, facts)
    lock_path = swarm_dir / "ledger.lock"
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger_path = swarm_dir / "ledger.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else new_ledger()
        d = decide(policy, ev, ledger)
        tmp = ledger_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d.ledger))
        tmp.replace(ledger_path)
        rid = run_id or os.environ.get("SWARM_RUN_ID") or data.get("session_id", "")
        audit.append(root / policy["audit"]["path"], audit.record(rid, ev, d, d.ledger))
    return d


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print("swarm: unreadable hook input; refusing (fail-closed)", file=sys.stderr)
        return 2
    if argv and not data.get("hook_event_name"):
        data["hook_event_name"] = argv[0]
    event = data.get("hook_event_name")
    try:
        d = handle(data, project_dir(data))
    except Exception as e:  # noqa: BLE001 -- fail-closed: any enforcer error denies
        print(f"swarm: enforcer error ({type(e).__name__}: {e}); refusing", file=sys.stderr)
        return 2 if event in BLOCKING else 0
    if not d.allow and event in BLOCKING:
        print(f"swarm.toml denied [{d.rule}]: {d.reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
