"""I5 Aggregation closure (experimental in v0.1): any role that can receive two or more
fragments of restricted-label data, directly or through channels, assignments, spawns or
messages, must be the aggregation's merge_by role.

A fragment is one label source (a path glob or a tool) read by one role. A role with
max_instances > 1 passes on one fragment per instance, since instances may read
different parts. The merge_by role is trusted to release combined results, so what it
passes on carries no fragments."""

from ..graph import Graph, union
from .base import Finding

ID = "I5"


def check(g: Graph) -> list[Finding]:
    m = g.swarm.model
    out: list[Finding] = []
    data = union(g.assign, g.message, g.channel_flow, g.spawn)
    inbound = {r: {a for a in g.roles if r in data.get(a, ())} for r in g.roles}
    for i, agg in enumerate(m.aggregation):
        direct: dict[str, dict[tuple, bool]] = {r: {} for r in g.roles}
        for lab in agg.labels:
            spec = m.labels.get(lab)
            if spec is None:
                continue
            srcs = [("path", p) for p in spec.paths] + [("tool", t) for t in spec.tools]
            for r in g.roles:
                for kind, s in srcs:
                    fact = g.reads_source(r, kind, s)
                    if fact:
                        direct[r][(lab, s)] = fact.inferred
        frags = {r: dict(direct[r]) for r in g.roles}
        changed = True
        while changed:
            changed = False
            for r in g.roles:
                for w in inbound[r]:
                    if w == agg.merge_by:
                        continue
                    carried = {}
                    n = min(m.roles[w].max_instances, 2)
                    for f, inf in frags[w].items():
                        for k in range(n):
                            carried[f if n == 1 else (*f, k)] = inf
                    for f, inf in carried.items():
                        if f not in frags[r] or (frags[r][f] and not inf):
                            frags[r][f] = inf
                            changed = True
        for r in g.roles:
            if r == agg.merge_by or len(frags[r]) < 2:
                continue
            declared = [f for f, inf in frags[r].items() if not inf]
            out.append(Finding(
                ID, f"{r!r} can combine {len(frags[r])} fragments of {agg.labels} but only "
                f"{agg.merge_by!r} may merge them", ("aggregation", i, "merge_by"),
                runtime=len(declared) < 2))
    return out
