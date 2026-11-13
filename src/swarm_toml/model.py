"""Pydantic models for swarm.toml v0.1, with defaults applied.

The models check shape only. Cross-references (undeclared roles, labels, actions) and
every policy property are checked by the invariants, so they can be reported with a line.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SPEC_VERSION = "0.1"

Level = Literal["L1", "L2", "L3", "L4"]
LEVELS: tuple[Level, ...] = ("L1", "L2", "L3", "L4")

RoleName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$", max_length=64)]
# Dotted action names, e.g. "git.push.main", "report.publish", "code.exec".
ActionName = Annotated[
    str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*(\.[a-z0-9_-]+)+$", max_length=128)
]
# A permission rule in Claude Code / agent-perms syntax: "Tool" or "Tool(spec)".
RuleString = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][\w-]*(\(.*\))?$")]

# Abstract capabilities the validator reasons about. Anything else in
# `capabilities` is a project action, which must be defined under [actions].
ABSTRACT_CAPS = frozenset({"code.exec", "network.egress", "agent.launch", "model.call"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SwarmMeta(_Strict):
    name: str | None = Field(None, description="Swarm name. Defaults to the directory name.")
    default: Literal["deny", "allow"] = Field(
        "deny", description="Decision for anything undeclared. Only 'deny' passes I10."
    )
    root: RoleName | None = Field(
        None, description="Entry role. Defaults to every role no other role can spawn."
    )


class Identity(_Strict):
    issuer: Literal["enforcer", "spiffe"] = "enforcer"
    trust_domain: str | None = Field(None, description="SPIFFE trust domain (issuer = spiffe).")


class Conformance(_Strict):
    required: Level = "L1"


class Budget(_Strict):
    tokens: int = Field(gt=0, description="Input + output + cache-creation tokens per instance.")


class InlinePermissions(_Strict):
    allow: list[RuleString] = []
    deny: list[RuleString] = []


PermissionRef = Annotated[
    str, StringConstraints(pattern=r"^(agent-perms|claude):\S+$")
]


class Role(_Strict):
    permissions: PermissionRef | InlinePermissions | None = None
    max_instances: int = Field(1, ge=1, description="Instances per run, counted cumulatively.")
    can_spawn: list[RoleName] = []
    can_assign: list[RoleName] = []
    can_message: list[RoleName] = []
    budget: Budget | None = None
    capabilities: list[ActionName] | None = Field(
        None, description="If set, the complete list of abstract capabilities, plus actions."
    )
    allow_escape: bool = False
    escape_reason: str | None = None


class Delegation(_Strict):
    attenuate: bool = True
    max_depth: int | None = Field(None, ge=0)
    non_delegable: list[ActionName] = []


class Channel(_Strict):
    name: str
    paths: list[str] = Field(min_length=1)
    writers: list[RoleName] = []
    readers: list[RoleName] = []


class Label(_Strict):
    tools: list[str] = []
    paths: list[str] = []


class Aggregation(_Strict):
    labels: list[str] = Field(min_length=1)
    merge_by: RoleName


class Quorum(_Strict):
    of: RoleName
    min: int = Field(ge=1)
    independent: bool = True


class Collective(_Strict):
    action: ActionName
    requested_by: RoleName
    requires_quorum: Quorum


class Audit(_Strict):
    sequences: bool = False
    path: str = ".swarm/audit.jsonl"


class SwarmFile(_Strict):
    swarm: SwarmMeta = SwarmMeta()
    identity: Identity = Identity()
    conformance: Conformance = Conformance()
    roles: dict[RoleName, Role] = Field(min_length=1)
    delegation: Delegation = Delegation()
    channel: list[Channel] = []
    labels: dict[str, Label] = {}
    aggregation: list[Aggregation] = []
    collective: list[Collective] = []
    audit: Audit = Audit()
    actions: dict[ActionName, list[RuleString]] = {}

    @model_validator(mode="after")
    def _unique_channel_names(self) -> SwarmFile:
        names = [c.name for c in self.channel]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate channel names: {sorted(dupes)}")
        return self


def json_schema() -> dict:
    schema = SwarmFile.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://github.com/assemble-labs/swarm-toml/spec/swarm.schema.json"
    schema["title"] = f"swarm.toml v{SPEC_VERSION}"
    return schema
