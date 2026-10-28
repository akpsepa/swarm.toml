"""`swarm compile --target claude-code`: swarm.toml -> Claude Code files.

Writes, under the output directory:
  .claude/agents/<role>.md   subagent definitions (`tools` from the resolved profile)
  .claude/settings.json      session permissions (union of roles) + enforcement hooks
  .swarm/policy.json         the compiled policy the hook reads on every call
  .mcp.json                  the swarm.vote server, when the file has collective rules

Claude Code applies settings.json permissions to the whole session, so per-role rules are
enforced by the hook (decide.py), not by settings.json. The conformance report lists every
clause and how this target enforces it; compiling refuses when a clause at or below the
file's required level is unenforced.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ..graph import Graph
from ..load import InvalidFile, Swarm, load
from ..model import LEVELS, SPEC_VERSION
from ..perms import ALL_TOOLS

TARGET = "claude-code"
CC_VERSION = "2.1.312"
LAUNCHER = "swarm-launcher"
HOOK_EVENTS = ["SessionStart", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop"]
VOTE_TOOLS = ["mcp__swarm__request", "mcp__swarm__vote"]


@dataclass(frozen=True)
class Clause:
    id: str
    level: str
    status: str  # enforced | approximate | unenforced | n/a
    how: str


class CompileRefused(Exception):
    pass


def _lvl(level: str) -> int:
    return LEVELS.index(level)


def conformance(g: Graph) -> list[Clause]:
    m = g.swarm.model
    roles = m.roles
    execs = [r for r in g.roles if "code.exec" in g.perms(r).caps]
    c: list[Clause] = [
        Clause("static-validation", "L1", "enforced", "swarm check passes before compiling"),
        Clause("permissions", "L2", "enforced",
               "hook checks every call against the role's allow rules; Bash rules match "
               "each subcommand by prefix"),
        Clause("identity", "L3", "enforced",
               "role from hook agent_type/agent_id; root from --agent; spawn ledger keeps "
               "names and parents"),
        Clause("spawn-edges", "L3", "enforced", "PreToolUse on Agent: caller role vs can_spawn"),
        Clause("max-depth", "L3", "enforced", "depth from the spawn ledger at PreToolUse Agent"),
        Clause("max-instances", "L3", "enforced",
               "cumulative count of distinct agent_ids per role in the ledger"),
        Clause("assign-edges", "L3", "approximate",
               "no structured task API in 2.1.312: the Agent prompt and SendMessage to an "
               "instance the sender spawned are treated as assignments"),
        Clause("message-edges", "L3", "enforced",
               "PreToolUse on SendMessage: recipient name -> role via the ledger; unknown "
               "names and broadcasts denied"),
        Clause("default-deny", "L3", "enforced", "tools, roles and recipients not declared are denied"),
    ]
    if m.channel:
        c.append(Clause("channels", "L3", "enforced",
                        "file-tool paths checked against channel writers/readers"))
    for r in execs:
        c.append(Clause(f"channels:{r}", "L3", "unenforced",
                        f"{r!r} has Bash, whose paths the hook cannot see"))
    c.append(Clause("integrity", "L3", "approximate" if execs else "enforced",
                    "Write/Edit on protected paths denied; policy hash re-checked at "
                    "SessionStart and every call" + (", so a Bash edit blocks every later "
                                                     "call but is not prevented" if execs else "")))
    for r in execs:
        ps = g.perms(r)
        if roles[r].allow_escape:
            continue
        if roles[r].capabilities is not None and "network.egress" not in ps.caps:
            c.append(Clause(f"escape:{r}", "L3", "unenforced",
                            f"{r!r} must run Bash without network, but this target has no "
                            "per-agent sandbox"))
    budgeted = [r for r in g.roles if roles[r].budget]
    if budgeted:
        c.append(Clause("budget", "L4", "approximate",
                        "metered from transcripts at SubagentStop (needs session persistence); "
                        "spawns reserve budget; calls refused once metered use exceeds it"))
    if m.aggregation:
        c.append(Clause("aggregation", "L4", "approximate" if execs else "enforced",
                        "fragments tracked per instance on file-tool reads, labelled tools and "
                        "messages" + ("; Bash reads are not seen" if execs else "")))
    if m.collective:
        c.append(Clause("collective", "L4", "enforced",
                        "votes recorded by the hook on mcp__swarm__vote with the voter's "
                        "verified identity; the gated action's tool calls check the ledger"))
    if m.audit.sequences:
        c.append(Clause("audit-sequences", "L1", "enforced", "one JSON line per hook call"))
    if len(g.roots) > 1:
        c.append(Clause("topology", "L3", "enforced",
                        f"several roots: a synthetic {LAUNCHER!r} root may only spawn them"))
    return c


def blocking(clauses: list[Clause], required: str) -> list[Clause]:
    return [c for c in clauses if c.status == "unenforced" and _lvl(c.level) <= _lvl(required)]


def build_policy(g: Graph, clauses: list[Clause]) -> dict:
    s, m = g.swarm, g.swarm.model
    roles = {}
    for name, r in m.roles.items():
        ps = g.perms(name)
        roles[name] = {
            "can_spawn": sorted(g.spawn[name]),
            "can_assign": sorted(g.assign[name]),
            "can_message": sorted(g.message[name]),
            "budget": r.budget.model_dump() if r.budget else None,
            "max_instances": r.max_instances,
            "allow": [str(x) for x in ps.allow] if ps.resolved else None,
            "caps": sorted(ps.caps),
        }
    roots = g.roots
    launcher = None
    if len(roots) > 1:
        launcher = LAUNCHER
        roles[LAUNCHER] = {"can_spawn": roots, "can_assign": roots, "can_message": [],
                           "budget": None, "max_instances": 1,
                           "allow": ["Agent", "SendMessage"], "caps": ["agent.spawn"]}
    return {
        "spec": SPEC_VERSION,
        "target": TARGET,
        "swarm": s.name,
        "hash": s.digest(),
        "level": m.conformance.required,
        "roots": roots,
        "launcher": launcher,
        "max_depth": g.max_depth(),
        "roles": roles,
        "channels": [c.model_dump() for c in m.channel],
        "non_delegable": list(m.delegation.non_delegable),
        "actions": {k: list(v) for k, v in m.actions.items()},
        "labels": {k: v.model_dump() for k, v in m.labels.items()},
        "aggregation": [a.model_dump() for a in m.aggregation],
        "collective": [{"action": c.action, "requested_by": c.requested_by,
                        "of": c.requires_quorum.of, "min": c.requires_quorum.min,
                        "independent": c.requires_quorum.independent} for c in m.collective],
        "protected": s.protected(),
        "referenced": [s.rel(p) for p in s.referenced],
        "audit": m.audit.model_dump(),
        "unenforced": [c.id for c in clauses if c.status == "unenforced"],
    }


def _agent_tools(g: Graph, role: str, policy: dict) -> list[str] | None:
    pr = policy["roles"][role]
    if pr["allow"] is None:
        return None  # inherit every tool; the hook still enforces edges
    tools = {x.split("(", 1)[0] for x in pr["allow"]}
    tools.discard("Task")
    tools.discard("Agent")
    if pr["can_spawn"]:
        tools.add("Agent")
    if pr["can_message"] or pr["can_assign"]:
        tools.add("SendMessage")
    else:
        tools.discard("SendMessage")
    for c in policy["collective"]:
        if role == c["requested_by"]:
            tools.add("mcp__swarm__request")
        if role == c["of"]:
            tools.add("mcp__swarm__vote")
    return sorted(tools)


def agent_markdown(g: Graph, role: str, policy: dict) -> str:
    tools = _agent_tools(g, role, policy)
    pr = policy["roles"][role]
    lines = ["---", f"name: {role}",
             f"description: The {role} role of the {policy['swarm']} swarm (compiled from "
             "swarm.toml; do not edit)"]
    if tools is not None:
        lines.append(f"tools: {', '.join(tools)}")
    lines += ["---", "",
              f"You are an instance of the `{role}` role in the `{policy['swarm']}` swarm.",
              "Every tool call you make is checked against the swarm's policy. A denied call "
              "returns the rule and the reason; do not try to work around a denial."]
    peers = []
    if pr["can_spawn"]:
        peers.append(f"You may spawn: {', '.join(pr['can_spawn'])}. Always give each spawned "
                     "agent a `name`.")
    if pr["can_message"]:
        peers.append(f"You may message: {', '.join(pr['can_message'])}.")
    if peers:
        lines += [""] + peers
    return "\n".join(lines) + "\n"


def settings_json(g: Graph, policy: dict, hook_cmd: str) -> dict:
    # Session-wide allows are bare tool names: per-role paths and patterns are enforced by
    # the hook, so settings.json never refuses a call the policy allows.
    allow: set[str] = set()
    for name, pr in policy["roles"].items():
        rules = pr["allow"] if pr["allow"] is not None else sorted(ALL_TOOLS)
        allow.update(r.split("(", 1)[0] for r in rules)
        if pr["can_spawn"]:
            allow.add("Agent")
        if pr["can_message"] or pr["can_assign"]:
            allow.add("SendMessage")
    if policy["collective"]:
        allow.update(VOTE_TOOLS)
    allow.discard("Task")
    deny = []
    for p in policy["protected"]:
        deny += [f"Write(./{p})", f"Edit(./{p})"]

    def hook(ev: str) -> dict:
        return {"type": "command", "command": f"{hook_cmd} {ev}", "timeout": 30}

    hooks = {}
    for ev in HOOK_EVENTS:
        entry = {"hooks": [hook(ev)]}
        if ev == "PreToolUse":
            entry = {"matcher": "*", **entry}
        elif ev == "PostToolUse":
            entry = {"matcher": "Agent|SendMessage", **entry}
        hooks[ev] = [entry]
    return {"permissions": {"allow": sorted(allow), "deny": sorted(deny)}, "hooks": hooks}


def mcp_json(vote_cmd: str) -> dict:
    return {"mcpServers": {"swarm": {"type": "stdio", "command": vote_cmd, "args": []}}}


def report_text(s: Swarm, clauses: list[Clause]) -> str:
    req = s.model.conformance.required
    rows = [f"Conformance report: {s.name} -> {TARGET} (Claude Code {CC_VERSION}), "
            f"required {req}", ""]
    w = max(len(c.id) for c in clauses)
    for c in sorted(clauses, key=lambda c: (_lvl(c.level), c.id)):
        mark = "BLOCKS" if c in blocking(clauses, req) else ""
        rows.append(f"  {c.level}  {c.id:<{w}}  {c.status:<11} {c.how} {mark}".rstrip())
    return "\n".join(rows)


def compile_swarm(s: Swarm, out: Path, hook_cmd: str = "swarm-hook",
                  vote_cmd: str = "swarm-vote") -> tuple[list[Clause], list[Path]]:
    from ..check import findings_for

    errors = [f for f in findings_for(s) if f["severity"] == "error"]
    if errors:
        raise CompileRefused(f"swarm check reports {len(errors)} error(s); run `swarm check`")
    g = Graph(s)
    clauses = conformance(g)
    block = blocking(clauses, s.model.conformance.required)
    if block:
        raise CompileRefused(
            f"target {TARGET} cannot enforce {', '.join(c.id for c in block)}, required at "
            f"{s.model.conformance.required}")
    policy = build_policy(g, clauses)
    written: list[Path] = []

    def put(rel: str, text: str) -> None:
        p = out / rel
        if p.resolve() in {r.resolve() for r in s.referenced}:
            return  # never overwrite a file the policy references (it is hashed)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        written.append(p)

    for role in policy["roles"]:
        put(f".claude/agents/{role}.md", agent_markdown(g, role, policy))
    put(".claude/settings.json", json.dumps(settings_json(g, policy, hook_cmd), indent=2) + "\n")
    put(".swarm/policy.json", json.dumps(policy, indent=2) + "\n")
    put(".swarm/conformance.json",
        json.dumps({"target": TARGET, "claude_code": CC_VERSION,
                    "required": s.model.conformance.required,
                    "clauses": [asdict(c) for c in clauses]}, indent=2) + "\n")
    if policy["collective"]:
        put(".mcp.json", json.dumps(mcp_json(vote_cmd), indent=2) + "\n")
    return clauses, written


def compile_cli(args: argparse.Namespace) -> int:
    try:
        s = load(args.path)
    except InvalidFile as e:
        for line, msg in e.problems:
            print(f"{e.path}:{line}: invalid: {msg}", file=sys.stderr)
        return 2
    g = Graph(s)
    clauses = conformance(g)
    print(report_text(s, clauses))
    if args.report:
        return 1 if blocking(clauses, s.model.conformance.required) else 0
    out = Path(args.out) if args.out else s.root_dir
    try:
        _, written = compile_swarm(s, out)
    except CompileRefused as e:
        print(f"\nrefused: {e}", file=sys.stderr)
        return 1
    print("\nwrote:")
    for p in written:
        print(f"  {p}")
    return 0
