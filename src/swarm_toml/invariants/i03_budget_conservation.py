"""I3 Budget conservation: children's budgets, multiplied by max_instances, add up to no
more than the spawner's budget. A spawned role with no budget under a budgeted spawner
is runtime-dependent (a "missing budget")."""

from ..graph import Graph
from .base import Finding

ID = "I3"


def check(g: Graph) -> list[Finding]:
    roles = g.swarm.model.roles
    out: list[Finding] = []
    for a in g.roles:
        ba = roles[a].budget
        if ba is None:
            continue
        kids = sorted(b for b in g.spawn[a] if b != a)
        total = 0
        for b in kids:
            bb = roles[b].budget
            if bb is None:
                out.append(Finding(ID, f"{b!r} has no budget but its spawner {a!r} does, so "
                                   "conservation cannot hold", ("roles", b), runtime=True))
                continue
            total += bb.tokens * roles[b].max_instances
        if total > ba.tokens:
            parts = " + ".join(f"{b}={roles[b].budget.tokens:,}x{roles[b].max_instances}"
                               for b in kids if roles[b].budget)
            out.append(Finding(ID, f"children of {a!r} can use {total:,} tokens ({parts}), "
                               f"more than its budget of {ba.tokens:,}", ("roles", a, "budget")))
    return out
