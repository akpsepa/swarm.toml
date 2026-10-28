"""I4 No laundering over paths: along any chain of assign edges and channels, no role
gains a non-delegable action it lacks. Message edges carry data only."""

from ..graph import Graph, reach, union
from .base import Finding

ID = "I4"


def check(g: Graph) -> list[Finding]:
    out: list[Finding] = []
    authority = union(g.assign, g.channel_flow)
    for x in g.swarm.model.delegation.non_delegable:
        for r in g.roles:
            if g.holds(r, x):
                continue
            for s, path in sorted(reach(authority, r).items()):
                fact = g.holds(s, x)
                if fact and s != r:
                    out.append(Finding(
                        ID, f"{r!r} lacks non-delegable {x!r} but can reach {s!r}, which holds "
                        f"it, via {' -> '.join(path)}", ("roles", r, "can_assign"),
                        runtime=fact.inferred))
                    break
    return out
