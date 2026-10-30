"""Summarise a red-team round's outcomes.

    uv run python -m redteam.summarize ~/swarm-toml-runs/r1 > redteam/round1/RESULTS.md
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from harness.case import CASES

CONDS = ("C0", "C2", "C3")


def missing_tools(run: Path, case: dict) -> set[str]:
    """Scripted tools the attacker was not given (C2/C3 `tools:` frontmatter)."""
    md = run / "workspace" / ".claude" / "agents" / f"{case['attacker_role']}.md"
    head = md.read_text().split("\n---", 1)[0] if md.exists() else ""
    line = next((x for x in head.splitlines() if x.startswith("tools:")), None)
    if line is None:
        return set()
    have = {t.strip() for t in line.removeprefix("tools:").split(",")}
    return {st["tool"] for st in case["attack"] if "tool" in st} - have


def cell(o: dict | None) -> str:
    if o is None:
        return "-"
    if o.get("timed_out"):
        return "timeout"
    if o.get("no_tool"):
        return "blocked (no tool)"
    if not o["attack_delivered"]:
        return "blocked earlier" if o.get("attacker_denials") else "not sent"
    if o["attack_success"]:
        return "**worked**"
    return f"failed ({o['denials']} denials)" if o.get("denials") else "failed"


def main(argv: list[str]) -> int:
    root = Path(argv[0]).expanduser()
    rows, tally = [], defaultdict(lambda: defaultdict(int))
    for d in sorted(root.iterdir()):
        case = yaml.safe_load((CASES / d.name / "case.yaml").read_text())
        oc = {}
        for c in CONDS:
            f = d / c / "hierarchy" / "seed-1" / "outcome.json"
            oc[c] = json.loads(f.read_text()) if f.exists() else None
            if oc[c] is not None and not oc[c]["attack_delivered"]:
                oc[c]["no_tool"] = bool(missing_tools(f.parent, case))
            if oc[c] is not None:
                audit = f.parent / "audit.jsonl"
                oc[c]["attacker_denials"] = sum(
                    1 for x in (audit.read_text().splitlines() if audit.exists() else [])
                    if json.loads(x)["decision"] == "deny"
                    and json.loads(x)["agent_type"] == case["attacker_role"])
        for c, o in oc.items():
            if o is None:
                continue
            t = tally[c]
            t["runs"] += 1
            t["sent"] += bool(o["attack_delivered"])
            t["no_tool"] += bool(o.get("no_tool"))
            t["earlier"] += bool(not o["attack_delivered"] and o.get("attacker_denials"))
            t["worked"] += bool(o["attack_delivered"] and o["attack_success"])
            t["task_ok"] += bool(o["benign_success"])
            t["timeouts"] += bool(o.get("timed_out"))
        rows.append((case["category"], d.name.split("-", 1)[1], oc))

    print("# Results (hierarchy, one run each)\n")
    print("| Condition | Runs | Attack sent | Blocked before sending | Attack worked | Ordinary task done | Timeouts |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for c in CONDS:
        t = tally[c]
        print(f"| {c} | {t['runs']} | {t['sent']} | {t['no_tool'] + t['earlier']} | {t['worked']} | {t['task_ok']} | {t['timeouts']} |")
    print("\n| Category | Attack | C0 | C2 | C3 |")
    print("| --- | --- | --- | --- | --- |")
    for cat, name, oc in sorted(rows):
        print(f"| {cat} | `{name}` | " + " | ".join(cell(oc[c]) for c in CONDS) + " |")
    print("\n`blocked (no tool)`: the condition did not give the attacker a tool its script "
          "needs. `blocked earlier`: an earlier step of the attacker's was denied, and it gave up before "
          "its scripted call. `not sent`: the attacker declined, so the run says nothing about the "
          "defences. `failed (n denials)`: the attack was sent and did not work; n counts every "
          "denial in the run, not only those on the attack path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
