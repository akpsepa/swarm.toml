"""Mechanical checks for a red-team round (redteam/PROTOCOL.md), then import accepted
attacks as harness cases r<round>-<team>-<writer>-<id>.

    uv run python -m redteam.validate ~/swarm-toml-runs/redteam/r1
    uv run python -m redteam.validate ~/swarm-toml-runs/redteam/r2 2
"""

from __future__ import annotations

import re
import shlex
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from harness import oracle
from harness.case import ATTACK_TOOLS, CASES
from swarm_toml.load import load

HERE = Path(__file__).resolve().parent
TEAMS = {"release": "msg-confused-deputy-01", "counting": "budget-fanout-01",
         "orders": "collective-aggregation-01"}
REQUIRED = ("id", "category", "title", "attacker_role", "attack", "oracle")
URL = re.compile(r"https?://([^/\s\"']+)")
OK_HOSTS = (".invalid", ".example", ".test", "example.com", "example.org", "example.net")
SECRETS = re.compile(r"(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|BEGIN [A-Z ]*PRIVATE KEY)")


def _strings(spec) -> list[str]:
    out = []
    for s in [spec] if isinstance(spec, str) else spec:
        kind, _, rest = s.partition(":")
        if kind.strip() in ("file_contains", "file_absent"):
            out += shlex.split(rest)[1:]
    return out


def check(doc, base: Path, roles: set[str]) -> str | None:
    if not isinstance(doc, dict) or any(k not in doc for k in REQUIRED):
        return "R1 missing a required field"
    if doc["attacker_role"] not in roles or doc["attacker_role"] == "lead":
        return f"R2 attacker_role {doc['attacker_role']!r} is not a non-lead team role"
    steps = doc["attack"]
    if not isinstance(steps, list) or not steps:
        return "R1 attack is not a list of steps"
    for st in steps:
        if not isinstance(st, dict) or ("tool" in st) == ("do" in st):
            return "R1 each step needs exactly one of `tool` or `do`"
        if "tool" in st and st["tool"] not in ATTACK_TOOLS:
            return f"R2 scripted tool {st['tool']!r} is not SendMessage or Agent"
        if "tool" in st and not isinstance(st.get("input"), dict):
            return "R1 a tool step has no `input` mapping"
    if not any("tool" in st for st in steps):
        return "R1 no SendMessage or Agent step"
    text = yaml.safe_dump(doc)
    for host in URL.findall(text):
        if not host.lower().endswith(OK_HOSTS):
            return f"R4 real-looking network endpoint {host!r}"
    if SECRETS.search(text):
        return "R4 real-looking credential"
    extra = doc.get("extra_files") or {}
    for p in extra:
        q = Path(p)
        if q.is_absolute() or ".." in q.parts or p.startswith((".harness", ".swarm")):
            return f"R4 extra file {p!r} is outside the run's workspace"
    corpus = "".join(p.read_text(errors="replace") for p in base.rglob("*") if p.is_file())
    corpus += "".join(str(v) for v in extra.values())
    for s in _strings(doc["oracle"]):
        if s not in corpus:
            return f"R3 oracle string {s!r} appears in no workspace or extra file"
    try:
        with tempfile.TemporaryDirectory() as t:
            w = Path(t)
            shutil.copytree(base, w, dirs_exist_ok=True)
            for p, c in extra.items():
                (w / p).parent.mkdir(parents=True, exist_ok=True)
                (w / p).write_text(str(c))
            if oracle.check(doc["oracle"], w):
                return "R5 oracle is already true before the run"
    except ValueError as e:
        return f"R3 {e}"
    return None


def main(argv: list[str]) -> int:
    src = Path(argv[0]).expanduser().resolve()
    rnd = int(argv[1]) if len(argv) > 1 else 1
    raw_dir = HERE / f"round{rnd}" / "raw"
    report = [f"# Round {rnd}: mechanical check", "",
              "| Case | Writer | Category | Attacker | Verdict |", "| --- | --- | --- | --- | --- |"]
    accepted = 0
    for wdir in sorted(src.iterdir()):
        if not (wdir / "done.json").exists():
            continue
        team, writer = wdir.name.split("-")
        base_case = CASES / TEAMS[team]
        roles = set(load(_with_toml(base_case)).model.roles)
        dest = raw_dir / wdir.name
        shutil.copytree(wdir / "workspace" / "attacks", dest, dirs_exist_ok=True)
        shutil.copy(wdir / "workspace" / ".fence.jsonl", dest / "fence.jsonl")
        for f in sorted((wdir / "workspace" / "attacks").glob("*.yaml")):
            try:
                doc = yaml.safe_load(f.read_text())
            except yaml.YAMLError as e:
                doc, why = None, f"R1 YAML error: {str(e).splitlines()[0]}"
            else:
                why = check(doc, base_case / "workspace", roles)
            cid = f"r{rnd}-{team}-{writer}-{(doc or {}).get('id') or f.stem}".lower()
            cat = (doc or {}).get("category", "?") if isinstance(doc, dict) else "?"
            who = (doc or {}).get("attacker_role", "?") if isinstance(doc, dict) else "?"
            report.append(f"| `{cid}` | {writer} | {cat} | {who} | "
                          f"{'accepted' if why is None else 'rejected: ' + why} |")
            if why is None:
                _import(cid, doc, base_case, rnd)
                accepted += 1
    report += ["", f"Accepted {accepted}. Edits made: none.", ""]
    (HERE / f"round{rnd}" / "REPORT.md").write_text("\n".join(report))
    print("\n".join(report))
    return 0


def _with_toml(base_case: Path) -> Path:
    t = Path(tempfile.mkdtemp())
    shutil.copy(base_case / "swarm.hierarchy.toml", t / "swarm.toml")
    return t


def _import(cid: str, doc: dict, base_case: Path, rnd: int = 1) -> None:
    base = yaml.safe_load((base_case / "case.yaml").read_text())
    d = CASES / cid
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(base_case / "workspace", d / "workspace")
    for p, c in (doc.get("extra_files") or {}).items():
        (d / "workspace" / p).parent.mkdir(parents=True, exist_ok=True)
        (d / "workspace" / p).write_text(str(c))
    for t in ("hierarchy", "flat", "pipeline"):
        shutil.copy(base_case / f"swarm.{t}.toml", d / f"swarm.{t}.toml")
    case = {
        "id": cid, "area": "independent", "topologies": ["hierarchy"],
        "attacker_role": doc["attacker_role"],
        "goal": f"(Documentation only.) {doc['title']}",
        "benign_task": base["benign_task"], "canary": str(doc["oracle"]),
        "oracle": doc["oracle"], "benign_oracle": base["benign_oracle"],
        "roles": base["roles"], "attack": doc["attack"],
        "notes": f"Red team round {rnd}, {doc['category']}. {doc.get('rationale', '')}".strip(),
        "category": doc["category"], "base_case": base_case.name,
    }
    (d / "case.yaml").write_text(yaml.safe_dump(case, sort_keys=False, allow_unicode=True))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
