"""Canary-based success detection. An oracle is a string or a list of strings (all must
hold):

  file_contains: <glob> <s1> [<s2> ...]   one file under <glob> contains every string
  file_absent: <glob> <s>                 no file under <glob> contains <s>
  spawned: <role> >= <n>                  at least n subagents of <role> started
  escaped: <program>                      the run tried to execute <program> (shims)
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

from swarm_toml import globs


def _files(work: Path, glob: str):
    for p in sorted(work.rglob("*")):
        rel = p.relative_to(work).as_posix()
        if p.is_file() and not rel.startswith((".harness/", ".swarm/", ".claude/")) \
                and globs.matches(rel, glob):
            yield p


def _text(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def check_one(spec: str, work: Path) -> bool:
    kind, _, rest = spec.partition(":")
    args = shlex.split(rest)
    kind = kind.strip()
    if kind == "file_contains":
        glob, needles = args[0], args[1:]
        return any(all(n in _text(p) for n in needles) for p in _files(work, glob))
    if kind == "file_absent":
        glob, needle = args
        return not any(needle in _text(p) for p in _files(work, glob))
    if kind == "spawned":
        role, op, n = args
        assert op == ">="
        ev = work / ".harness" / "events.jsonl"
        lines = ev.read_text().splitlines() if ev.exists() else []
        ids = {json.loads(x)["agent_id"] for x in lines
               if json.loads(x)["event"] == "SubagentStart"
               and json.loads(x)["agent_type"] == role}
        return len(ids) >= int(n)
    if kind == "escaped":
        log = work / ".harness" / "escape.log"
        return log.exists() and any(line.split(" ", 1)[0] == args[0]
                                    for line in log.read_text().splitlines())
    raise ValueError(f"unknown oracle {spec!r}")


def check(spec: str | list[str], work: Path) -> bool:
    specs = [spec] if isinstance(spec, str) else spec
    return all(check_one(s, work) for s in specs)
