"""`swarm` CLI: `swarm check [path]` and `swarm compile --target claude-code [path]`.

Exit codes for check: 0 = no errors (warnings allowed), 1 = errors found, 2 = invalid file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .invariants import run_all
from .load import InvalidFile, Swarm, load


def findings_for(swarm: Swarm) -> list[dict]:
    from .graph import Graph

    as_error = Graph(swarm).runtime_is_error()
    out = []
    for f in run_all(swarm):
        out.append({"rule": f.rule, "severity": f.severity(as_error), "message": f.message,
                    "line": swarm.locator.line(f.where), "runtime": f.runtime})
    out.sort(key=lambda d: (d["line"], d["rule"], d["message"]))
    return out


def cmd_check(args: argparse.Namespace) -> int:
    try:
        swarm = load(args.path)
    except InvalidFile as e:
        for line, msg in e.problems:
            print(f"{e.path}:{line}: invalid: {msg}", file=sys.stderr)
        return 2
    found = findings_for(swarm)
    rel = swarm.path
    try:
        rel = swarm.path.relative_to(Path.cwd())
    except ValueError:
        pass
    if args.json:
        print(json.dumps({"file": str(rel), "level": swarm.model.conformance.required,
                          "hash": swarm.digest(), "findings": found}, indent=2))
    else:
        for d in found:
            print(f"{rel}:{d['line']}: {d['severity']} {d['rule']}: {d['message']}")
        errs = sum(d["severity"] == "error" for d in found)
        warns = len(found) - errs
        print(f"{rel}: {errs} error(s), {warns} warning(s) at "
              f"{swarm.model.conformance.required}", file=sys.stderr)
    return 1 if any(d["severity"] == "error" for d in found) else 0


def cmd_compile(args: argparse.Namespace) -> int:
    from .compile.claude_code import compile_cli

    return compile_cli(args)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="swarm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="validate swarm.toml (invariants I1-I10)")
    c.add_argument("path", nargs="?", default=".")
    c.add_argument("--json", action="store_true")
    c.set_defaults(fn=cmd_check)
    k = sub.add_parser("compile", help="compile swarm.toml to a runtime target")
    k.add_argument("path", nargs="?", default=".")
    k.add_argument("--target", required=True, choices=["claude-code"])
    k.add_argument("--out", help="output directory (default: the file's directory)")
    k.add_argument("--report", action="store_true", help="print the conformance report only")
    k.set_defaults(fn=cmd_compile)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())


def check_main() -> int:
    """`swarm-check [path]`: the one-line CI entry point."""
    return main(["check", *sys.argv[1:]])
