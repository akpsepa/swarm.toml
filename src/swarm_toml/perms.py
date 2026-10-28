"""Resolve a role's `permissions` reference into a PermSet the invariants can reason about.

Three reference forms are supported:

- inline:       permissions = { allow = ["Read(notes/**)", "Bash(uv run:*)"], deny = [...] }
- agent-perms:  permissions = "agent-perms:profiles.<name>"  (from .agents/permissions.json)
- claude:       permissions = "claude:.claude/agents/<role>.md"  (subagent `tools:` frontmatter)

Every fact carries a provenance: *declared* (stated by a rule) or *inferred* (implied by
a tool the validator cannot see inside, chiefly Bash, or by a missing profile). Findings
that rest only on inferred facts are runtime-dependent (see SPEC.md, "Severity").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import globs
from .model import ABSTRACT_CAPS, InlinePermissions, Role

READ_TOOLS = {"Read", "Grep", "Glob", "LS"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
NET_TOOLS = {"WebFetch", "WebSearch"}
SPAWN_TOOLS = {"Agent", "Task"}
ALL_TOOLS = READ_TOOLS | WRITE_TOOLS | NET_TOOLS | SPAWN_TOOLS | {"Bash", "SendMessage"}

_RULE = re.compile(r"^(?P<tool>[A-Za-z_][\w-]*)(?:\((?P<spec>.*)\))?$")


@dataclass(frozen=True)
class Rule:
    tool: str
    spec: str | None = None  # None = any input

    @classmethod
    def parse(cls, s: str) -> Rule:
        m = _RULE.match(s.strip())
        if not m:
            raise ValueError(f"bad permission rule: {s!r}")
        spec = m.group("spec")
        return cls(m.group("tool"), spec if spec not in (None, "", "*", "**") else None)

    def __str__(self) -> str:
        return self.tool if self.spec is None else f"{self.tool}({self.spec})"


def _bash_prefix(spec: str) -> str:
    return spec[:-2] if spec.endswith(":*") else spec.rstrip("*")


def rules_overlap(a: Rule, b: Rule) -> bool:
    """True if some single call could match both rules."""
    same = a.tool == b.tool or {a.tool, b.tool} <= SPAWN_TOOLS
    if not same:
        return False
    if a.spec is None or b.spec is None:
        return True
    if a.tool in READ_TOOLS | WRITE_TOOLS:
        return globs.overlaps(a.spec, b.spec)
    if a.tool == "Bash":
        pa, pb = _bash_prefix(a.spec), _bash_prefix(b.spec)
        return pa.startswith(pb) or pb.startswith(pa)
    return a.spec == b.spec


@dataclass(frozen=True)
class Fact:
    value: str
    inferred: bool


@dataclass
class PermSet:
    source: str  # human-readable origin
    resolved: bool = True  # False when the reference could not be read
    allow: list[Rule] = field(default_factory=list)
    reads: list[Fact] = field(default_factory=list)  # globs
    writes: list[Fact] = field(default_factory=list)  # globs
    caps: dict[str, bool] = field(default_factory=dict)  # name -> inferred?
    files: list[Path] = field(default_factory=list)  # files this resolution read
    error: str | None = None

    @property
    def tools(self) -> set[str]:
        return {r.tool for r in self.allow}

    def has_cap(self, cap: str) -> Fact | None:
        if cap in self.caps:
            return Fact(cap, self.caps[cap])
        return None

    def _add_cap(self, cap: str, inferred: bool) -> None:
        # declared beats inferred
        self.caps[cap] = self.caps.get(cap, True) and inferred


def _from_rules(source: str, allow: list[str], deny: list[str]) -> PermSet:
    ps = PermSet(source=source)
    denied_tools = {r.tool for r in map(Rule.parse, deny) if r.spec is None}
    for r in map(Rule.parse, allow):
        if r.tool in denied_tools:
            continue
        ps.allow.append(r)
        if r.tool in READ_TOOLS:
            ps.reads.append(Fact(globs.norm(r.spec or "**"), False))
        elif r.tool in WRITE_TOOLS:
            ps.writes.append(Fact(globs.norm(r.spec or "**"), False))
            if r.tool != "Write":  # Edit returns what it edits; Write only replaces
                ps.reads.append(Fact(globs.norm(r.spec or "**"), False))
        elif r.tool == "Bash":
            ps._add_cap("code.exec", False)
            # A shell can read, write and connect anywhere the sandbox allows.
            ps._add_cap("network.egress", True)
            ps.reads.append(Fact("**", True))
            ps.writes.append(Fact("**", True))
        elif r.tool in NET_TOOLS:
            ps._add_cap("network.egress", False)
        elif r.tool in SPAWN_TOOLS:
            ps._add_cap("agent.spawn", False)
    return ps


def _top(source: str, why: str) -> PermSet:
    """No usable profile: assume every tool, all inferred."""
    ps = _from_rules(source, sorted(ALL_TOOLS), [])
    ps.reads = [Fact(f.value, True) for f in ps.reads]
    ps.writes = [Fact(f.value, True) for f in ps.writes]
    ps.caps = {c: True for c in ps.caps}
    ps.resolved = False
    ps.error = why
    return ps


def _find_up(start: Path, rel: str) -> Path | None:
    for d in (start, *start.parents):
        p = d / rel
        if p.is_file():
            return p
    return None


def _agent_perms(ref: str, base: Path) -> PermSet:
    name = ref.removeprefix("profiles.")
    path = _find_up(base, ".agents/permissions.json")
    if path is None:
        return _top(f"agent-perms:{ref}", "no .agents/permissions.json found")
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return _top(f"agent-perms:{ref}", f"cannot read {path}: {e}")
    profiles = doc.get("profiles", {})

    def collect(n: str, seen: tuple[str, ...]) -> tuple[list[str], list[str]] | None:
        if n not in profiles or n in seen:
            return None
        p = profiles[n]
        allow, deny = list(p.get("allow", [])), list(p.get("deny", []))
        for parent in p.get("extends", []):
            got = collect(parent, (*seen, n))
            if got is None:
                return None
            allow += got[0]
            deny += got[1]
        return allow, deny

    got = collect(name, ())
    if got is None:
        return _top(f"agent-perms:{ref}", f"profile {name!r} not found in {path}")
    ps = _from_rules(f"agent-perms:{ref}", *got)
    ps.files.append(path)
    return ps


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _claude(ref: str, base: Path) -> PermSet:
    path = (base / ref).resolve()
    if not path.is_file():
        return _top(f"claude:{ref}", f"{ref} not found")
    fm = _frontmatter(path.read_text())
    tools = fm.get("tools")
    if not tools:
        ps = _top(f"claude:{ref}", f"{ref} sets no `tools`, so it inherits every tool")
        ps.files.append(path)
        return ps
    allow = [t.strip().strip("\"'") for t in tools.strip("[]").split(",") if t.strip()]
    deny = [t.strip() for t in fm.get("disallowedTools", "").strip("[]").split(",") if t.strip()]
    ps = _from_rules(f"claude:{ref}", allow, deny)
    ps.files.append(path)
    return ps


def resolve(role: Role, base: Path) -> PermSet:
    p = role.permissions
    if p is None:
        ps = _top("(none)", "no `permissions` reference")
    elif isinstance(p, InlinePermissions):
        ps = _from_rules("inline", p.allow, p.deny)
    elif p.startswith("agent-perms:"):
        ps = _agent_perms(p.removeprefix("agent-perms:"), base)
    else:
        ps = _claude(p.removeprefix("claude:"), base)

    if role.capabilities is not None:
        # A declared list is complete for abstract capabilities: what it omits, the
        # target must take away (e.g. a sandbox without network for Bash).
        listed = set(role.capabilities)
        for cap in list(ps.caps):
            if cap in ABSTRACT_CAPS and cap not in listed:
                del ps.caps[cap]
        for cap in listed:
            ps.caps[cap] = False
    return ps
