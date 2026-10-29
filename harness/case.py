"""Failure-suite case definitions (harness/cases/<id>/case.yaml)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CASES = Path(__file__).resolve().parent / "cases"
AREAS = {"message_edges", "budget_flow", "collective", "control", "spec_attack", "independent"}
TOPOLOGIES = ("hierarchy", "flat", "pipeline")
ATTACK_TOOLS = ("SendMessage", "Agent")  # the calls the guard logs, so delivery is checkable


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
    attack: list[dict] = field(default_factory=list)  # the attacker's fixed script
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
    if not c.attack:
        raise ValueError(f"{d}: no attack script")
    for st in c.attack:
        if "tool" in st and st["tool"] not in ATTACK_TOOLS:
            raise ValueError(f"{d}: attack tool {st['tool']!r} is not one of {ATTACK_TOOLS}")
        if ("tool" in st) == ("do" in st):
            raise ValueError(f"{d}: each attack step has exactly one of `tool` or `do`")
    for t in c.topologies:
        if t not in TOPOLOGIES or not c.swarm_file(t).is_file():
            raise ValueError(f"{d}: missing swarm.{t}.toml")
    return c


def all_cases(root: Path = CASES) -> list[Case]:
    return [load_case(p.name, root) for p in sorted(root.iterdir())
            if (p / "case.yaml").is_file()]


def _fill(v, n: int):
    if isinstance(v, str):
        return v.replace("{n}", str(n))
    if isinstance(v, dict):
        return {k: _fill(x, n) for k, x in v.items()}
    return v


def script(case: Case) -> list[dict]:
    """The attack with `repeat` expanded: one entry per call (or `do` instruction)."""
    out = []
    for st in case.attack:
        for n in range(1, int(st.get("repeat", 1)) + 1):
            out.append({k: _fill(v, n) for k, v in st.items() if k != "repeat"})
    return out
