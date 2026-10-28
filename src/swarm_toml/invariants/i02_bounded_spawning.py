"""I2 Bounded spawning: the spawn graph is acyclic, or its cycles are capped by an
explicit max_depth."""

from ..graph import Graph
from .base import Finding

ID = "I2"


def check(g: Graph) -> list[Finding]:
    cyc = g.spawn_cycle()
    if cyc and g.swarm.model.delegation.max_depth is None:
        return [Finding(ID, f"spawn cycle {' -> '.join(cyc)} with no [delegation] max_depth "
                        "to cap it", ("roles", cyc[0], "can_spawn"))]
    if not g.roots:
        return [Finding(ID, "every role can be spawned by another, so there is no root; "
                        "set [swarm] root", ("swarm",))]
    return []
