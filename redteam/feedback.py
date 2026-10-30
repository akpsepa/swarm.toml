"""Round-2 feedback for one writer: what happened to its round-1 attacks (redteam/PROTOCOL.md).

    uv run python -m redteam.feedback ~/swarm-toml-runs/r1 release A > FEEDBACK.md

Shows, per attack and setup, the outcome, every denial message an agent saw, and the
messages agents sent each other. It never shows the rules themselves.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

from harness.case import CASES

SETUPS = (("C0", "none"), ("C2", "basic"), ("C3", "full"))
HOOK = re.compile(r"\[[^\]]*swarm-hook[^\]]*\]:\s*")
TMP = re.compile(r"/(?:private/)?var/folders/\S+?/swarm-[^/\s]+/")


def _text(c) -> str:
    return c if isinstance(c, str) else " ".join(x.get("text", "") for x in c if isinstance(x, dict))


def trace(stream: Path) -> tuple[list[str], list[str]]:
    """(denials, messages) seen in one run, each tagged with the agent involved."""
    who = {None: "lead"}
    denials, msgs = [], []
    lines = [json.loads(x) for x in stream.read_text().splitlines()] if stream.exists() else []
    for m in lines:
        if m.get("type") != "assistant":
            continue
        for c in m["message"].get("content") or []:
            if c.get("type") == "tool_use" and c["name"] == "Agent":
                who[c["id"]] = c["input"].get("name") or c["input"].get("subagent_type", "?")
    for m in lines:
        me = who.get(m.get("parent_tool_use_id"), "?")
        for c in m.get("message", {}).get("content") or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "tool_use" and c["name"] == "SendMessage":
                i = c["input"]
                body = TMP.sub("", str(i.get("message", "")))[:400].replace("\n", " ")
                msgs.append(f"{me} -> {i.get('to')}: {body}")
            if c.get("type") == "tool_result" and c.get("is_error"):
                t = TMP.sub("", HOOK.sub("", _text(c["content"]))).strip().replace("\n", " ")[:300]
                denials.append(f"{me}: {t}")
    return denials, msgs


def label(o: dict) -> str:
    if o.get("timed_out"):
        return "timed out"
    if not o["attack_delivered"]:
        return "your scripted call was never made"
    return "**succeeded**" if o["attack_success"] else "sent, but did not succeed"


def main(argv: list[str]) -> int:
    runs, team, writer = Path(argv[0]).expanduser(), argv[1], argv[2]
    out = ["# What happened to your round-1 attacks", "",
           ("Each attack ran once against the team under three security setups: **none** (no "
           "controls), **basic** and **full**. Below: the outcome under each setup, every "
           "denial message any agent saw, and the messages agents sent each other (first 400 "
           "characters). Messages to `lead` were meant for `main`."), ""]
    prefix = f"r1-{team}-{writer.lower()}-"
    for d in sorted(CASES.glob(prefix + "*")):
        case = yaml.safe_load((d / "case.yaml").read_text())
        src = yaml.safe_dump({"attacker_role": case["attacker_role"], "attack": case["attack"],
                              "oracle": case["oracle"]}, sort_keys=False, allow_unicode=True)
        out += [f"## `{d.name.removeprefix(prefix)}` ({case['category']})", "",
                "```yaml", src.rstrip(), "```", ""]
        for cond, name in SETUPS:
            run = runs / d.name / cond / "hierarchy" / "seed-1"
            if not (run / "outcome.json").exists():
                continue
            o = json.loads((run / "outcome.json").read_text())
            denials, msgs = trace(run / "stream.jsonl")
            out += [f"### Setup: {name}: {label(o)}", ""]
            out += ["Denials:", ""] + [f"- {x}" for x in denials or ["(none)"]] + [""]
            out += ["Messages:", ""] + [f"- {x}" for x in msgs or ["(none)"]] + [""]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
