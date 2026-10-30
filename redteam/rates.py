"""Success rates over every seed run of a red-team round.

    uv run python -m redteam.rates ~/swarm-toml-runs/r1 > redteam/round1/RATES.md
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import yaml

from harness.case import CASES
from redteam.scope import scope

CONDS = ("C0", "C2", "C3")


def main(argv: list[str]) -> int:
    root = Path(argv[0]).expanduser()
    rows, tot = [], defaultdict(lambda: [0, 0, 0, 0])
    for d in sorted(root.iterdir()):
        case = yaml.safe_load((CASES / d.name / "case.yaml").read_text())
        cells = {}
        for c in CONDS:
            fs = sorted((d / c / "hierarchy").glob("seed-*/outcome.json"))
            os_ = [(f.parent, json.loads(f.read_text())) for f in fs]
            won = [r for r, o in os_ if o["attack_delivered"] and o["attack_success"]]
            cells[c] = (len(won), sum(bool(o["attack_delivered"]) for _, o in os_), len(os_),
                        sum(scope(r, case) == "in" for r in won))
        if max(v[2] for v in cells.values()) < 2:
            continue
        rows.append((d.name, cells))
        for c, v in cells.items():
            tot[c] = [a + b for a, b in zip(tot[c], v)]
    print("# Repeat runs: how often each attack worked\n")
    print("Each cell: runs where the attack worked / runs. In brackets: runs where the "
          "attacker's scripted call was made at all. **in**: of the runs where it worked, how "
          "many are in scope (`SCOPE.md`); the rest are lone-insider leaks.\n")
    print("| Attack | C0 none | C2 basic | C3 full |")
    print("| --- | --- | --- | --- |")
    for name, cells in rows:
        print(f"| `{name}` | " + " | ".join(f"{w}/{n} ({s}) in {k}" for w, s, n, k in cells.values()) + " |")
    print("| **All** | " + " | ".join(f"**{w}/{n}** ({s}) in **{k}**" for w, s, n, k in tot.values()) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
