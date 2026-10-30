"""Run one C3b hand-writer: a separate Claude session that writes, by hand, the hooks for one
team's between-agent rules, from RULES.md and HOOKS.md only (no swarm.toml, compiler or
enforcer code).

    uv run python -m c3b.write --team release --out ~/swarm-toml-runs/c3b

Its folder (hooks, settings.json, tests), transcript and fence log go to <out>/<team>/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MODEL = "claude-opus-5-5"
PROMPT = """Read RULES.md and HOOKS.md in this folder. Write Claude Code hooks, in this folder,
that enforce every rule in RULES.md for the team, following HOOKS.md: a settings.json and
the scripts it runs. Test them with your own sample events. Work as a careful engineer would
for a real deployment: wrong decisions either let a rule be broken or block the team's
normal work. When you are done, reply with a short summary of what you built and any rule
you could not enforce exactly."""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="c3b.write")
    ap.add_argument("--team", required=True, choices=["release", "counting", "orders"])
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    out = a.out.resolve() / a.team
    if out == REPO or REPO in out.parents:
        sys.exit("refusing: --out is inside the repo")
    if (out / "done.json").exists():
        print(f"{out} already done")
        return 0
    work = Path(tempfile.mkdtemp(prefix=f"c3b-{a.team}-"))
    shutil.copy(HERE / "rules" / f"{a.team}.md", work / "RULES.md")
    shutil.copy(HERE / "HOOKS.md", work / "HOOKS.md")
    (work / ".claude").mkdir()
    fence = REPO / "redteam" / "fence.py"
    settings = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [
        {"type": "command", "command": f"{sys.executable} {fence}"}]}]}}
    (work / ".claude" / "settings.json").write_text(json.dumps(settings))
    claude = shutil.which("claude") or sys.exit("claude not found")
    cmd = [claude, "-p", PROMPT, "--model", MODEL, "--output-format", "stream-json",
           "--verbose", "--setting-sources", "project",
           "--settings", str(work / ".claude" / "settings.json"),
           "--tools", "Read,Write,Edit,Glob,Bash",
           "--permission-mode", "acceptEdits", "--permission-prompts", "none",
           "--allowedTools", "Bash(python3:*)",
           "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']
    env = {**os.environ, "FENCE_ALLOW_PY": "1",
           "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"}
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "stream.jsonl", "w") as so, open(out / "stderr.txt", "w") as se:
        rc = subprocess.run(cmd, cwd=work, env=env, stdout=so, stderr=se,
                            stdin=subprocess.DEVNULL, timeout=2400, check=False).returncode
    shutil.copytree(work, out / "workspace", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.lock"))
    (out / "done.json").write_text(json.dumps({"rc": rc, "model": MODEL}, indent=2))
    print(json.dumps({"team": a.team, "rc": rc}))
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
