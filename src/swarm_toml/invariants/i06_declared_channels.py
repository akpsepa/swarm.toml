"""I6 Declared channels: any path one role can write and another can read must be
declared as a channel with the writer and the reader. Always runtime-dependent."""

from .. import globs
from ..graph import Graph
from .base import Finding

ID = "I6"


def check(g: Graph) -> list[Finding]:
    m = g.swarm.model
    protected = g.swarm.protected()
    out: list[Finding] = []
    for a in g.roles:
        for b in g.roles:
            if a == b and m.roles[a].max_instances < 2:
                continue
            chans = [c for c in m.channel if a in c.writers and b in c.readers]
            hit = None
            for w in g.perms(a).writes:
                if any(globs.contains(p, w.value) for p in protected):
                    continue
                for r in g.perms(b).reads:
                    if not globs.overlaps(w.value, r.value):
                        continue
                    covered = any(globs.contains(p, w.value) or globs.contains(p, r.value)
                                  for c in chans for p in c.paths)
                    if not covered:
                        hit = (w.value, r.value)
                        break
                if hit:
                    break
            if hit:
                who = f"instances of {a!r}" if a == b else f"{a!r} -> {b!r}"
                out.append(Finding(ID, f"undeclared channel {who}: {a!r} can write {hit[0]} "
                                   f"and {b!r} can read {hit[1]}", ("roles", a, "permissions"),
                                   runtime=True))
    return out
