"""JSONL audit log: one line per hook call (SPEC 2.10)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from .decide import Decision, HookEvent, chain


def record(run_id: str, ev: HookEvent, d: Decision, ledger: dict) -> dict:
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z",
        "run_id": run_id,
        "agent_id": ev.agent_id,
        "agent_type": ev.agent_type,
        "event": ev.event,
        "tool": ev.tool_name,
        "target": d.target,
        "decision": "allow" if d.allow else "deny",
        "rule": d.rule,
        "reason": d.reason,
        "chain": chain(ledger, ev.agent_id),
    }


def append(path: Path, rec: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")
