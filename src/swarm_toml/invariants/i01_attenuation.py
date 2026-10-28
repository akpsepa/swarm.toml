"""I1 Attenuation: a spawned role's resolved permissions are a subset of its spawner's,
and no spawned role holds a non-delegable action."""

from ..graph import Graph, rule_contains
from .base import Finding

ID = "I1"


def check(g: Graph) -> list[Finding]:
    m = g.swarm.model
    out: list[Finding] = []
    if not m.delegation.attenuate:
        return [Finding(ID, "attenuate = false: spawned roles may exceed their spawner",
                        ("delegation", "attenuate"), force="warning")]
    for a in g.roles:
        pa = g.perms(a)
        for b in sorted(g.spawn[a]):
            if b == a:
                continue
            pb = g.perms(b)
            runtime = not (pa.resolved and pb.resolved)
            extra = [str(r) for r in pb.allow if not any(rule_contains(o, r) for o in pa.allow)]
            if extra:
                out.append(Finding(
                    ID, f"{b!r} (spawned by {a!r}) is allowed {', '.join(extra)}, "
                    f"which {a!r} is not", ("roles", a, "can_spawn"), runtime=runtime))
            caps = sorted(c for c in pb.caps if c not in pa.caps)
            if caps:
                inferred = all(pb.caps[c] for c in caps)
                out.append(Finding(
                    ID, f"{b!r} (spawned by {a!r}) has capabilities {caps} that {a!r} lacks",
                    ("roles", a, "can_spawn"), runtime=runtime or inferred))
    spawned = {b for a in g.roles for b in g.spawn[a] if b != a}
    for b in sorted(spawned):
        for x in m.delegation.non_delegable:
            fact = g.holds(b, x)
            if fact:
                out.append(Finding(
                    ID, f"spawned role {b!r} can perform non-delegable action {x!r}"
                    + (" (via an unparsed shell or unknown profile)" if fact.inferred else ""),
                    ("roles", b, "permissions"), runtime=fact.inferred))
    return out
