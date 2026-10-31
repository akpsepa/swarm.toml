"""C3b (C2 plus hand-written hooks) against C3 (full compile) on the red-team attacks, seed 1.

    uv run python -m c3b.compare ~/swarm-toml-runs/r1 ~/swarm-toml-runs/r2 > c3b/RESULTS.md
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import yaml

from harness.case import CASES
from redteam.scope import scope

CONDS = ("C0", "C2", "C3", "C3b")


def _cell(run: Path, case: dict) -> str:
    f = run / "outcome.json"
    if not f.exists():
        return "–"
    o = json.loads(f.read_text())
    if not o["attack_delivered"]:
        return "not sent"
    if not o["attack_success"]:
        return "stopped"
    return f"**worked** ({scope(run, case)})"


def main(argv: list[str]) -> int:
    rows, tot, benign = [], {c: Counter() for c in CONDS}, Counter()
    for root in (Path(a).expanduser() for a in argv):
        for d in sorted(root.iterdir()):
            case = yaml.safe_load((CASES / d.name / "case.yaml").read_text())
            runs = {c: d / c / "hierarchy" / "seed-1" for c in CONDS}
            if not (runs["C3b"] / "outcome.json").exists():
                continue
            cells = {c: _cell(r, case) for c, r in runs.items()}
            rows.append((d.name, case.get("team") or d.name.split("-")[1], cells))
            for c, v in cells.items():
                tot[c][v] += 1
                o = runs[c] / "outcome.json"
                if o.exists():
                    benign[c] += bool(json.loads(o.read_text())["benign_success"])
    p = print
    p("# Hand-written hooks (C3b) against swarm.toml (C3)\n")
    p(f"{len(rows)} red-team attacks (rounds 1 and 2), seed 1, hierarchy. C3b is C2 plus the "
      "hooks a blind writer hand-wrote for the C3-only rules (tag `c3b-v1`, `REPORT.md`). "
      "Cells: **worked** (in / out of scope, `redteam/SCOPE.md`), stopped (sent but did not "
      "work) or not sent (the attacker's scripted call was never made).\n")
    p("## Totals\n")
    p("| Outcome | " + " | ".join(CONDS) + " |")
    p("| --- | " + " | ".join("---" for _ in CONDS) + " |")
    for k in ("**worked** (in)", "**worked** (out)", "stopped", "not sent"):
        p(f"| {k} | " + " | ".join(str(tot[c][k]) for c in CONDS) + " |")
    p("| ordinary task done | " + " | ".join(f"{benign[c]}/{len(rows)}" for c in CONDS) + " |")
    differ = [r for r in rows if r[2]["C3"] != r[2]["C3b"]]
    p(f"\n## Attacks where C3 and C3b differ ({len(differ)})\n")
    p("| Attack | " + " | ".join(CONDS) + " |")
    p("| --- | " + " | ".join("---" for _ in CONDS) + " |")
    for name, _, cells in differ:
        p(f"| `{name}` | " + " | ".join(cells[c] for c in CONDS) + " |")
    p("\nOne seed each: a single differing cell can be run-to-run variation (round-2 repeats "
      "show the same attack working in some seeds and not others).\n")
    p("## All attacks\n")
    p("| Attack | " + " | ".join(CONDS) + " |")
    p("| --- | " + " | ".join("---" for _ in CONDS) + " |")
    for name, _, cells in rows:
        p(f"| `{name}` | " + " | ".join(cells[c] for c in CONDS) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
