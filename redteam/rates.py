"""Success rates over every seed run of a red-team round.

    uv run python -m redteam.rates ~/swarm-toml-runs/r1 > redteam/round1/RATES.md
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

CONDS = ("C0", "C2", "C3")


def main(argv: list[str]) -> int:
    root = Path(argv[0]).expanduser()
    rows, tot = [], defaultdict(lambda: [0, 0, 0])
    for d in sorted(root.iterdir()):
        cells = {}
        for c in CONDS:
            os_ = [json.loads(f.read_text())
                   for f in sorted((d / c / "hierarchy").glob("seed-*/outcome.json"))]
            cells[c] = (sum(o["attack_delivered"] and o["attack_success"] for o in os_),
                        sum(bool(o["attack_delivered"]) for o in os_), len(os_))
        if max(n for _, _, n in cells.values()) < 2:
            continue
        rows.append((d.name, cells))
        for c, v in cells.items():
            tot[c] = [a + b for a, b in zip(tot[c], v)]
    print("# Repeat runs: how often each attack worked\n")
    print("Each cell: runs where the attack worked / runs. In brackets: runs where the "
          "attacker's scripted call was made at all.\n")
    print("| Attack | C0 none | C2 basic | C3 full |")
    print("| --- | --- | --- | --- |")
    for name, cells in rows:
        print(f"| `{name}` | " + " | ".join(f"{w}/{n} ({s})" for w, s, n in cells.values()) + " |")
    print("| **All** | " + " | ".join(f"**{w}/{n}** ({s})" for w, s, n in tot.values()) + " |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
