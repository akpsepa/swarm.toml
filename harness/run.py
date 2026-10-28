"""Run one case x condition x topology x seed headless, in a fresh temp directory.

    uv run python -m harness.run --case <id> --condition C3 --topology flat --seed 1

Stores stream.jsonl (the transcript stream), audit.jsonl, events.jsonl, the final
workspace and outcome.json under --out/<case>/<condition>/<topology>/seed-<n>/. A run whose
outcome.json exists is skipped, so an interrupted matrix resumes where it stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from . import oracle
from .case import load_case
from .conditions import CONDITIONS, setup

REPO = Path(__file__).resolve().parents[1]
PINNED_CLAUDE = "2.1.312"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_OUT = Path.home() / "swarm-toml-runs"


def _inside_repo(p: Path) -> bool:
    p = p.resolve()
    return p == REPO or REPO in p.parents


def claude_version(claude: str) -> str:
    out = subprocess.run([claude, "--version"], check=False, capture_output=True,
                         text=True).stdout
    return out.split()[0] if out else ""


def usage_from_stream(stream: Path) -> dict:
    per_agent: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    seen: set[str] = set()
    limits = []
    cost = 0.0
    for line in stream.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "assistant":
            m = e.get("message", {})
            if m.get("id") in seen:
                continue
            seen.add(m.get("id"))
            who = e.get("parent_tool_use_id") or "root"
            for k, v in (m.get("usage") or {}).items():
                if isinstance(v, int):
                    per_agent[who][k] += v
        elif e.get("type") == "rate_limit_event":
            limits.append(e.get("rate_limit_info", {}).get("unifiedWindows", {}))
        elif e.get("type") == "result":
            cost = max(cost, float(e.get("total_cost_usd") or 0))
    counted = ("input_tokens", "output_tokens", "cache_creation_input_tokens")
    return {
        "tokens_counted": sum(a[k] for a in per_agent.values() for k in counted),
        "tokens_with_cache_reads": sum(sum(a.values()) for a in per_agent.values()),
        "agents": len(per_agent),
        "cost_usd_list_price": round(cost, 4),
        "utilization_first": limits[0] if limits else None,
        "utilization_last": limits[-1] if limits else None,
    }


def run_one(case_id: str, cond: str, topology: str, seed: int, out_root: Path,
            model: str = DEFAULT_MODEL, timeout: int = 1200, claude: str | None = None,
            keep: bool = False, allow_version_mismatch: bool = False) -> dict:
    if _inside_repo(out_root):
        raise SystemExit("refusing: --out is inside the repo")
    case = load_case(case_id)
    if topology not in case.topologies:
        raise SystemExit(f"{case_id} does not run on {topology}")
    out = out_root / case_id / cond / topology / f"seed-{seed}"
    if (out / "outcome.json").exists():
        return json.loads((out / "outcome.json").read_text())
    claude = claude or shutil.which("claude") or "claude"
    version = claude_version(claude)
    if version != PINNED_CLAUDE and not allow_version_mismatch:
        raise SystemExit(f"Claude Code {version} is not the pinned {PINNED_CLAUDE}")

    work = Path(tempfile.mkdtemp(prefix=f"swarm-{case_id}-{cond}-"))
    if _inside_repo(work):
        raise SystemExit("refusing: temp directory is inside the repo")
    rs = setup(cond, case, topology, work)
    run_id = f"{case_id}.{cond}.{topology}.{seed}"
    env = {**os.environ, **rs.env, "SWARM_RUN_ID": run_id}
    cmd = [claude, "-p", rs.prompt, "--agent", rs.root_role, "--model", model,
           "--output-format", "stream-json", "--verbose", "--setting-sources", "project",
           "--permission-mode", "acceptEdits", "--permission-prompts", "none", *rs.args]
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    timed_out = False
    with open(out / "stream.jsonl", "w") as so, open(out / "stderr.txt", "w") as se:
        try:
            rc = subprocess.run(cmd, check=False, cwd=work, env=env, stdout=so, stderr=se,
                                stdin=subprocess.DEVNULL, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            rc, timed_out = None, True
    duration = time.time() - t0

    for name in (".swarm/audit.jsonl", ".harness/events.jsonl", ".harness/escape.log"):
        if (work / name).exists():
            shutil.copy(work / name, out / Path(name).name)
    outcome = {
        "run_id": run_id, "case": case_id, "area": case.area, "condition": cond,
        "topology": topology, "seed": seed, "model": model, "claude_code": version,
        "rc": rc, "timed_out": timed_out, "duration_s": round(duration, 1),
        "attack_success": oracle.check(case.oracle, work),
        "benign_success": oracle.check(case.benign_oracle, work),
        "denials": sum(1 for x in (out / "audit.jsonl").read_text().splitlines()
                       if json.loads(x)["decision"] == "deny")
        if (out / "audit.jsonl").exists() else None,
        **usage_from_stream(out / "stream.jsonl"),
    }
    shutil.copytree(work, out / "workspace", dirs_exist_ok=True, symlinks=True,
                    ignore=shutil.ignore_patterns("*.lock"))
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    (out / "outcome.json").write_text(json.dumps(outcome, indent=2))
    return outcome


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harness.run")
    ap.add_argument("--case", required=True)
    ap.add_argument("--condition", required=True, choices=CONDITIONS)
    ap.add_argument("--topology", required=True, choices=["hierarchy", "flat", "pipeline"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--keep", action="store_true", help="keep the temp workspace")
    ap.add_argument("--allow-version-mismatch", action="store_true")
    a = ap.parse_args(argv)
    o = run_one(a.case, a.condition, a.topology, a.seed, a.out.resolve(), a.model, a.timeout,
                keep=a.keep, allow_version_mismatch=a.allow_version_mismatch)
    print(json.dumps(o, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
