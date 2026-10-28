"""I8 Escape check: no role holds code.exec together with network egress, agent.launch or
model.call, unless it sets allow_escape = true with a stated reason."""

from ..graph import Graph
from .base import Finding

ID = "I8"
ESCAPES = ("network.egress", "agent.launch", "model.call")


def check(g: Graph) -> list[Finding]:
    roles = g.swarm.model.roles
    out: list[Finding] = []
    for r in g.roles:
        spec = roles[r]
        if spec.allow_escape and not (spec.escape_reason or "").strip():
            out.append(Finding(ID, f"{r!r} sets allow_escape without an escape_reason",
                               ("roles", r, "allow_escape")))
        caps = g.perms(r).caps
        if "code.exec" not in caps or spec.allow_escape:
            continue
        esc = [c for c in ESCAPES if c in caps]
        if esc:
            out.append(Finding(ID, f"{r!r} has code.exec with {', '.join(esc)} and can escape "
                               "the enforcer; restrict it, list its capabilities, or set "
                               "allow_escape with a reason", ("roles", r, "permissions"),
                               runtime=True))
    return out
