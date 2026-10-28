"""The effective graph of a swarm: spawn, assign, message and channel edges, plus the
permission helpers the invariants share."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import globs
from .load import Swarm
from .model import LEVELS
from .perms import READ_TOOLS, SPAWN_TOOLS, WRITE_TOOLS, Fact, PermSet, Rule, rules_overlap

Edges = dict[str, set[str]]


def _edges(roles, attr: str) -> Edges:
    return {name: set(getattr(r, attr)) & set(roles) for name, r in roles.items()}


def reach(edges: Edges, src: str) -> dict[str, list[str]]:
    """Every role reachable from `src` in one or more hops, with one shortest path."""
    out: dict[str, list[str]] = {}
    q = deque([(src, [src])])
    while q:
        node, path = q.popleft()
        for nxt in sorted(edges.get(node, ())):
            if nxt not in out:
                out[nxt] = [*path, nxt]
                q.append((nxt, [*path, nxt]))
    return out


def union(*es: Edges) -> Edges:
    out: Edges = {}
    for e in es:
        for k, v in e.items():
            out.setdefault(k, set()).update(v)
    return out


def rule_contains(outer: Rule, inner: Rule) -> bool:
    """True if every call matching `inner` also matches `outer`."""
    same = outer.tool == inner.tool or {outer.tool, inner.tool} <= SPAWN_TOOLS
    if not same:
        return False
    if outer.spec is None:
        return True
    if inner.spec is None:
        return False
    if outer.tool in READ_TOOLS | WRITE_TOOLS:
        return globs.contains(outer.spec, inner.spec)
    if outer.tool == "Bash":
        strip = lambda s: s[:-2] if s.endswith(":*") else s.rstrip("*")
        return strip(inner.spec).startswith(strip(outer.spec))
    return outer.spec == inner.spec


@dataclass
class Graph:
    swarm: Swarm
    spawn: Edges = field(init=False)
    assign: Edges = field(init=False)
    message: Edges = field(init=False)
    channel_flow: Edges = field(init=False)

    def __post_init__(self) -> None:
        roles = self.swarm.model.roles
        self.spawn = _edges(roles, "can_spawn")
        self.assign = _edges(roles, "can_assign")
        self.message = _edges(roles, "can_message")
        self.channel_flow = {r: set() for r in roles}
        for ch in self.swarm.model.channel:
            for w in ch.writers:
                if w in roles:
                    self.channel_flow[w].update(r for r in ch.readers if r in roles)

    # --- structure -------------------------------------------------------------
    @property
    def roles(self) -> list[str]:
        return list(self.swarm.model.roles)

    @property
    def roots(self) -> list[str]:
        if self.swarm.model.swarm.root:
            return [self.swarm.model.swarm.root]
        spawned = {b for a, bs in self.spawn.items() for b in bs if b != a}
        return [r for r in self.roles if r not in spawned]

    def spawn_cycle(self) -> list[str] | None:
        for r in self.roles:
            back = reach(self.spawn, r)
            if r in back:
                return back[r]
        return None

    def declared_depth(self) -> int:
        """Longest spawn chain from a root, counting hops (0 = roots only)."""
        if self.spawn_cycle():
            return 0
        memo: dict[str, int] = {}

        def longest(r: str) -> int:
            if r not in memo:
                memo[r] = max((1 + longest(c) for c in self.spawn[r]), default=0)
            return memo[r]

        return max((longest(r) for r in self.roots), default=0)

    def max_depth(self) -> int:
        md = self.swarm.model.delegation.max_depth
        return md if md is not None else self.declared_depth()

    # --- permissions -----------------------------------------------------------
    def perms(self, role: str) -> PermSet:
        return self.swarm.perms[role]

    def holds(self, role: str, action: str) -> Fact | None:
        """Can `role` perform `action`? Declared beats inferred."""
        ps = self.perms(role)
        if action in ps.caps:
            return Fact(action, ps.caps[action])
        inferred_hit = False
        for spec in self.swarm.model.actions.get(action, []):
            ar = Rule.parse(spec)
            if any(rules_overlap(r, ar) for r in ps.allow):
                if ps.resolved:
                    return Fact(action, False)
                inferred_hit = True
            if ar.tool == "Bash" and "code.exec" in ps.caps:
                inferred_hit = True  # an unparsed shell may run it
        return Fact(action, True) if inferred_hit else None

    def reads_source(self, role: str, kind: str, src: str) -> Fact | None:
        ps = self.perms(role)
        if kind == "path":
            hits = [f for f in ps.reads if globs.overlaps(f.value, src)]
            if not hits:
                return None
            return Fact(src, all(f.inferred for f in hits))
        if src in self.swarm.model.actions:
            return self.holds(role, src)
        try:
            tr = Rule.parse(src)
        except ValueError:
            return None
        hits = [r for r in ps.allow if rules_overlap(r, tr)]
        return Fact(src, not ps.resolved) if hits else None

    # --- severity --------------------------------------------------------------
    @property
    def level(self) -> str:
        return self.swarm.model.conformance.required

    def runtime_is_error(self) -> bool:
        return LEVELS.index(self.level) >= LEVELS.index("L3")
