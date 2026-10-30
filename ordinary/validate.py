"""Mechanical checks for ordinary tasks (ordinary/PROTOCOL.md), then import accepted tasks as
harness cases ord-<team>-<writer>-<id> (area "ordinary", no attacker).

    uv run python -m ordinary.validate ~/swarm-toml-runs/ordinary/w
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from harness import oracle
from harness.case import CASES
from redteam.validate import OK_HOSTS, SECRETS, TEAMS, URL

HERE = Path(__file__).resolve().parent
REQUIRED = ("id", "title", "mast", "task", "oracle")
MAST = re.compile(r"FM-[123]\.[1-6]")
KINDS = ("file_contains", "file_absent")


def check(doc, base: Path) -> str | None:
    if not isinstance(doc, dict) or any(k not in doc for k in REQUIRED):
        return "R1 missing a required field"
    if not isinstance(doc["mast"], list) or not all(MAST.fullmatch(str(m)) for m in doc["mast"]):
        return "R1 `mast` is not a list of FM codes"
    specs = [doc["oracle"]] if isinstance(doc["oracle"], str) else doc["oracle"]
    if not specs or any(s.partition(":")[0].strip() not in KINDS for s in specs):
        return "R3 oracle kind is not file_contains / file_absent"
    text = yaml.safe_dump(doc)
    for host in URL.findall(text):
        if not host.lower().endswith(OK_HOSTS):
            return f"R4 real-looking network endpoint {host!r}"
    if SECRETS.search(text):
        return "R4 real-looking credential"
    extra = doc.get("extra_files") or {}
    for p in extra:
        q = Path(p)
        if q.is_absolute() or ".." in q.parts or p.startswith((".harness", ".swarm", ".claude")):
            return f"R4 extra file {p!r} is outside the run's workspace"
    try:
        with tempfile.TemporaryDirectory() as t:
            w = Path(t)
            shutil.copytree(base, w, dirs_exist_ok=True)
            for p, c in extra.items():
                (w / p).parent.mkdir(parents=True, exist_ok=True)
                (w / p).write_text(str(c))
            if oracle.check(doc["oracle"], w):
                return "R5 oracle is already true before the run"
    except (ValueError, IndexError) as e:
        return f"R3 {e}"
    return None


def main(argv: list[str]) -> int:
    src = Path(argv[0]).expanduser().resolve()
    raw_dir = HERE / "raw"
    report = ["# Ordinary tasks: mechanical check", "",
              "| Case | Writer | MAST | Verdict |", "| --- | --- | --- | --- |"]
    accepted = 0
    for wdir in sorted(src.iterdir()):
        if not (wdir / "done.json").exists():
            continue
        team, writer = wdir.name.split("-")
        base_case = CASES / TEAMS[team]
        dest = raw_dir / wdir.name
        shutil.copytree(wdir / "workspace" / "tasks", dest, dirs_exist_ok=True)
        shutil.copy(wdir / "workspace" / ".fence.jsonl", dest / "fence.jsonl")
        for f in sorted((wdir / "workspace" / "tasks").glob("*.yaml")):
            try:
                doc = yaml.safe_load(f.read_text())
            except yaml.YAMLError as e:
                doc, why = None, f"R1 YAML error: {str(e).splitlines()[0]}"
            else:
                why = check(doc, base_case / "workspace")
            cid = f"ord-{team}-{writer}-{(doc or {}).get('id') or f.stem}".lower()
            mast = ", ".join(map(str, (doc or {}).get("mast") or [])) if isinstance(doc, dict) else "?"
            report.append(f"| `{cid}` | {writer} | {mast} | "
                          f"{'accepted' if why is None else 'rejected: ' + why} |")
            if why is None:
                _import(cid, doc, base_case)
                accepted += 1
    report += ["", f"Accepted {accepted}. Edits made: none.", ""]
    (HERE / "REPORT.md").write_text("\n".join(report))
    print("\n".join(report))
    return 0


def _import(cid: str, doc: dict, base_case: Path) -> None:
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
        "id": cid, "area": "ordinary", "topologies": ["hierarchy"],
        "attacker_role": "none", "goal": "", "canary": "", "oracle": [],
        "benign_task": " ".join(str(doc["task"]).split()),
        "benign_oracle": doc["oracle"], "roles": base["roles"],
        "notes": f"Ordinary task. {doc['title']}. {doc.get('notes', '')}".strip(),
        "mast": doc["mast"], "base_case": base_case.name,
    }
    (d / "case.yaml").write_text(yaml.safe_dump(case, sort_keys=False, allow_unicode=True))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
