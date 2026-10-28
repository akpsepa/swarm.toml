"""Static invariants I1-I10 (SPEC.md section 5). One module each."""

from __future__ import annotations

from ..graph import Graph
from ..load import Swarm
from . import (
    i01_attenuation,
    i02_bounded_spawning,
    i03_budget_conservation,
    i04_no_laundering,
    i05_aggregation,
    i06_declared_channels,
    i07_integrity,
    i08_escape,
    i09_independent_voters,
    i10_default_deny,
)
from .base import Finding

MODULES = [i01_attenuation, i02_bounded_spawning, i03_budget_conservation, i04_no_laundering,
           i05_aggregation, i06_declared_channels, i07_integrity, i08_escape,
           i09_independent_voters, i10_default_deny]


def run_all(swarm: Swarm) -> list[Finding]:
    g = Graph(swarm)
    out: list[Finding] = []
    for mod in MODULES:
        out.extend(mod.check(g))
    return out


__all__ = ["MODULES", "Finding", "run_all"]
