"""I9 Independent voters: a quorum's requester has no spawn or assign path to any voter
role, and the quorum can be reached at all."""

from ..graph import Graph, reach, union
from .base import Finding

ID = "I9"


def check(g: Graph) -> list[Finding]:
    m = g.swarm.model
    out: list[Finding] = []
    control = union(g.spawn, g.assign)
    for i, c in enumerate(m.collective):
        q = c.requires_quorum
        if q.of not in m.roles or c.requested_by not in m.roles:
            continue  # I10 reports it
        if q.min > m.roles[q.of].max_instances:
            out.append(Finding(ID, f"quorum for {c.action!r} needs {q.min} {q.of!r} voters but "
                               f"max_instances is {m.roles[q.of].max_instances}",
                               ("collective", i, "requires_quorum")))
        if not q.independent:
            continue
        if c.requested_by == q.of:
            out.append(Finding(ID, f"{c.requested_by!r} requests {c.action!r} and is also the "
                               "voter role", ("collective", i, "requested_by")))
            continue
        path = reach(control, c.requested_by).get(q.of)
        if path:
            out.append(Finding(ID, f"requester {c.requested_by!r} controls voter role {q.of!r} "
                               f"via {' -> '.join(path)} (Sybil quorum)",
                               ("collective", i, "requested_by")))
    return out
