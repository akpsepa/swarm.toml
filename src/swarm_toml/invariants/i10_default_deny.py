"""I10 Default deny: anything undeclared is refused. Statically: `default` is "deny", and
every role, label and action the file names is declared."""

from ..graph import Graph
from ..model import ABSTRACT_CAPS
from ..perms import Rule
from .base import Finding

ID = "I10"


def check(g: Graph) -> list[Finding]:
    m = g.swarm.model
    roles, actions = set(m.roles), set(m.actions)
    out: list[Finding] = []

    def role_refs(names, where, what):
        for n in names:
            if n not in roles:
                out.append(Finding(ID, f"{what} names undeclared role {n!r}", where))

    if m.swarm.default != "deny":
        out.append(Finding(ID, 'default must be "deny" in v0.1', ("swarm", "default")))
    if m.swarm.root:
        role_refs([m.swarm.root], ("swarm", "root"), "root")
    for name, r in m.roles.items():
        for attr in ("can_spawn", "can_assign", "can_message"):
            role_refs(getattr(r, attr), ("roles", name, attr), f"{name}.{attr}")
        for cap in r.capabilities or []:
            if cap not in ABSTRACT_CAPS and cap not in actions:
                out.append(Finding(ID, f"{name}.capabilities names {cap!r}, which is neither "
                                   "an abstract capability nor defined in [actions]",
                                   ("roles", name, "capabilities")))
    for i, c in enumerate(m.channel):
        role_refs(c.writers, ("channel", i, "writers"), f"channel {c.name!r} writers")
        role_refs(c.readers, ("channel", i, "readers"), f"channel {c.name!r} readers")
    for i, a in enumerate(m.aggregation):
        role_refs([a.merge_by], ("aggregation", i, "merge_by"), "aggregation merge_by")
        for lab in a.labels:
            if lab not in m.labels:
                out.append(Finding(ID, f"aggregation names undeclared label {lab!r}",
                                   ("aggregation", i, "labels")))
    for name, lab in m.labels.items():
        for t in lab.tools:
            if t in actions:
                continue
            try:
                Rule.parse(t)
                if "(" not in t and "." in t:
                    raise ValueError  # looks like an action that [actions] doesn't define
            except ValueError:
                out.append(Finding(ID, f"label {name!r} names {t!r}, which is neither an "
                                   "action in [actions] nor a tool rule", ("labels", name)))
    for x in m.delegation.non_delegable:
        if x not in actions and x not in ABSTRACT_CAPS:
            out.append(Finding(ID, f"non_delegable action {x!r} is not defined in [actions], "
                               "so no tool call can be matched to it",
                               ("delegation", "non_delegable")))
    for i, c in enumerate(m.collective):
        role_refs([c.requested_by], ("collective", i, "requested_by"), "collective requested_by")
        role_refs([c.requires_quorum.of], ("collective", i, "requires_quorum"), "quorum of")
        if c.action not in actions:
            out.append(Finding(ID, f"collective action {c.action!r} is not defined in "
                               "[actions], so it can never be gated", ("collective", i, "action")))
    return out
