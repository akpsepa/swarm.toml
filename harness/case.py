"""Failure-suite case definitions (harness/cases/<id>/case.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CASES = Path(__file__).resolve().parent / "cases"
AREAS = {"message_edges", "budget_flow", "collective", "control", "spec_attack"}
TOPOLOGIES = ("hierarchy", "flat", "pipeline")


@dataclass
class Case:
    id: str
    dir: Path
    area: str
    topologies: list[str]
    attacker_role: str
    goal: str
    benign_task: str
    canary: str
    oracle: str | list[str]
    benign_oracle: str | list[str]
    roles: dict[str, str]  # role -> standing task given at spawn
    setup: str = "./workspace/"
    notes: str = ""
    extra: dict = field(default_factory=dict)

    def swarm_file(self, topology: str) -> Path:
        return self.dir / f"swarm.{topology}.toml"

    @property
    def workspace(self) -> Path:
        return (self.dir / self.setup).resolve()


def load_case(case_id: str, root: Path = CASES) -> Case:
    d = root / case_id
    data = yaml.safe_load((d / "case.yaml").read_text())
    known = set(Case.__dataclass_fields__) - {"dir", "extra"}
    c = Case(dir=d, **{k: v for k, v in data.items() if k in known},
             extra={k: v for k, v in data.items() if k not in known})
    if c.id != case_id:
        raise ValueError(f"{d}: id {c.id!r} does not match its folder")
    if c.area not in AREAS:
        raise ValueError(f"{d}: unknown area {c.area!r}")
    for t in c.topologies:
        if t not in TOPOLOGIES or not c.swarm_file(t).is_file():
            raise ValueError(f"{d}: missing swarm.{t}.toml")
    return c


def all_cases(root: Path = CASES) -> list[Case]:
    return [load_case(p.name, root) for p in sorted(root.iterdir())
            if (p / "case.yaml").is_file()]
