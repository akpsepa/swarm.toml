"""I7 Integrity: no role can write swarm.toml, any file it references, or the audit log."""

from .. import globs
from ..graph import Graph
from .base import Finding

ID = "I7"


def check(g: Graph) -> list[Finding]:
    protected = g.swarm.protected()
    out: list[Finding] = []
    for r in g.roles:
        for w in g.perms(r).writes:
            hits = [p for p in protected if globs.overlaps(w.value, p)]
            if hits:
                out.append(Finding(ID, f"{r!r} can write {w.value}, which covers protected "
                                   f"{', '.join(hits[:3])}"
                                   + (" (via an unparsed shell or unknown profile)"
                                      if w.inferred else ""),
                                   ("roles", r, "permissions"), runtime=w.inferred))
                break
    return out
