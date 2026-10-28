"""The decision function: the only place swarm.toml rules are evaluated at runtime.

Pure: no I/O. The hook gathers every outside fact first (the policy hash recomputed from
disk, tokens metered from a transcript) and passes them in `event.facts`.

    decide(policy, event, ledger) -> Decision(allow, rule, reason, ledger)

`ledger` is the run's state (instances, spawn bindings, budgets, label fragments, quorum
requests). `decide` never mutates its argument; it returns the next ledger.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from .. import globs
from ..perms import READ_TOOLS, SPAWN_TOOLS, WRITE_TOOLS, Rule

ROOT = "root"
ROOT_ADDRESS = "main"  # how a Claude Code subagent addresses the root session
REQUEST_TOOL = "mcp__swarm__request"
VOTE_TOOL = "mcp__swarm__vote"
FILE_TOOLS = READ_TOOLS | WRITE_TOOLS
_SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


@dataclass(frozen=True)
class HookEvent:
    event: str  # SessionStart | PreToolUse | PostToolUse | SubagentStart | SubagentStop | ...
    agent_type: str | None = None
    agent_id: str | None = None
    tool_name: str | None = None
    tool_input: dict = field(default_factory=dict)
    tool_response: Any = None
    tool_use_id: str | None = None
    cwd: str = "."
    facts: dict = field(default_factory=dict)  # hash, tokens_used

    @classmethod
    def from_hook(cls, data: dict, facts: dict | None = None) -> HookEvent:
        return cls(event=data.get("hook_event_name", ""), agent_type=data.get("agent_type"),
                   agent_id=data.get("agent_id"), tool_name=data.get("tool_name"),
                   tool_input=data.get("tool_input") or {},
                   tool_response=data.get("tool_response"),
                   tool_use_id=data.get("tool_use_id"), cwd=data.get("cwd") or ".",
                   facts=facts or {})


@dataclass(frozen=True)
class Decision:
    allow: bool
    rule: str
    reason: str
    ledger: dict = field(default_factory=dict, compare=False, repr=False)
    target: str | None = None


def new_ledger() -> dict:
    return {"locked": None, "instances": {}, "names": {}, "pending": [], "counts": {},
            "requests": {}, "frags": {}}


# --------------------------------------------------------------------------- helpers

def _ok(led: dict, rule: str = "allow", reason: str = "", target: str | None = None) -> Decision:
    return Decision(True, rule, reason, led, target)


def _no(led: dict, rule: str, reason: str, target: str | None = None) -> Decision:
    return Decision(False, rule, reason, led, target)


def _rel(path: str, cwd: str) -> str | None:
    """Repo-relative form of a tool path, or None if it lies outside the project."""
    if not path:
        return "**"
    if path.startswith("/"):
        base = cwd.rstrip("/") + "/"
        if path == cwd.rstrip("/"):
            return "**"
        if not path.startswith(base):
            # macOS reports /private/var for /var; compare without that prefix too
            alt = path.removeprefix("/private")
            if not alt.startswith(base.removeprefix("/private")):
                return None
            path = alt[len(base.removeprefix("/private")):]
        else:
            path = path[len(base):]
    return globs.norm(path)


def _call_path(ev: HookEvent) -> str | None:
    ti = ev.tool_input
    raw = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
    return _rel(raw, ev.cwd)


def _call_matches(rule: Rule, tool: str, path: str | None, command: str | None) -> bool:
    if rule.tool != tool and not {rule.tool, tool} <= SPAWN_TOOLS:
        return False
    if rule.spec is None:
        return True
    if tool in FILE_TOOLS:
        return path is not None and globs.matches(path, rule.spec)
    if tool == "Bash":
        prefix = rule.spec[:-2] if rule.spec.endswith(":*") else rule.spec.rstrip("*")
        exact = not (rule.spec.endswith(":*") or rule.spec.endswith("*"))
        return command == prefix if exact else (command or "").startswith(prefix)
    return False


def _subcommands(cmd: str) -> list[str]:
    return [c for c in _SPLIT.split(cmd.strip()) if c]


def _role_allows(pol: dict, role: str, ev: HookEvent, path: str | None) -> bool:
    allow = pol["roles"][role]["allow"]
    if allow is None:
        return True
    rules = [Rule.parse(r) for r in allow]
    tool = ev.tool_name
    if tool == "Bash":
        cmds = _subcommands(ev.tool_input.get("command", ""))
        return bool(cmds) and all(any(_call_matches(r, tool, None, c) for r in rules)
                                  for c in cmds)
    return any(_call_matches(r, tool, path, None) for r in rules)


def _actions_for(pol: dict, ev: HookEvent, path: str | None) -> list[str]:
    out = []
    cmds = _subcommands(ev.tool_input.get("command", "")) if ev.tool_name == "Bash" else [None]
    for action, specs in pol["actions"].items():
        for spec in specs:
            r = Rule.parse(spec)
            if any(_call_matches(r, ev.tool_name, path, c) for c in cmds):
                out.append(action)
                break
    return out


def _chain(led: dict, iid: str) -> list[str]:
    out, seen = [], set()
    while iid and iid not in seen:
        seen.add(iid)
        inst = led["instances"].get(iid)
        if inst is None:
            break
        out.append(inst["role"])
        iid = inst["parent"]
    return out[::-1]


def chain(ledger: dict, agent_id: str | None) -> list[str]:
    return _chain(ledger, agent_id or ROOT)


def _descends_from(led: dict, iid: str, ancestor: str) -> bool:
    seen = set()
    while iid and iid not in seen:
        if iid == ancestor:
            return True
        seen.add(iid)
        inst = led["instances"].get(iid)
        iid = inst["parent"] if inst else None
    return False


def _budget_left(pol: dict, led: dict, iid: str) -> int | None:
    inst = led["instances"][iid]
    b = pol["roles"][inst["role"]]["budget"]
    if b is None:
        return None
    return b["tokens"] - inst["used"] - inst["reserved"]


def _label_sources(pol: dict, ev: HookEvent, path: str | None, actions: list[str]) -> list[str]:
    """Label fragments (label|source) this call reads, for labels under an aggregation."""
    gated = {lab for agg in pol["aggregation"] for lab in agg["labels"]}
    out = []
    for lab in sorted(gated):
        spec = pol["labels"].get(lab, {})
        if ev.tool_name in READ_TOOLS | WRITE_TOOLS and path is not None:
            for g in spec.get("paths", []):
                if globs.overlaps(path, g):
                    out.append(f"{lab}|{g}")
        for t in spec.get("tools", []):
            if t in actions or (t not in pol["actions"] and
                                _call_matches(Rule.parse(t), ev.tool_name, path, None)):
                out.append(f"{lab}|{t}")
    return out


def _would_aggregate(pol: dict, role: str, frags: set[str]) -> bool:
    """True if `role` would hold two or more fragments under an aggregation it may not merge.
    Fragments only come from labels under an aggregation, so every one has a merger."""
    merger: dict[str, str] = {}
    for agg in pol["aggregation"]:
        for lab in agg["labels"]:
            merger.setdefault(lab, agg["merge_by"])
    by_merger: dict[str, set[str]] = {}
    for f in frags:
        head, rest = f.split("|", 1)
        m = rest.split("|", 1)[0] if head == "merged" else merger[head]
        by_merger.setdefault(m, set()).add(f)
    return any(m != role and len(fs) >= 2 for m, fs in by_merger.items())


# --------------------------------------------------------------------------- events

def _session_start(pol: dict, ev: HookEvent, led: dict) -> Decision:
    if ev.facts.get("hash") != pol["hash"]:
        led["locked"] = "integrity: swarm.toml or a referenced file changed since compile"
        return _no(led, "integrity", led["locked"])
    role = ev.agent_type
    allowed = set(pol["roots"]) | ({pol["launcher"]} if pol.get("launcher") else set())
    if role not in allowed:
        led["locked"] = f"identity: root role {role!r} is not a declared root"
        return _no(led, "identity", led["locked"])
    if ROOT not in led["instances"]:
        depth = -1 if role == pol.get("launcher") else 0
        # The root has no spawn-time name; Claude Code subagents address it as "main".
        led["instances"][ROOT] = {"role": role, "name": ROOT_ADDRESS, "parent": None,
                                  "depth": depth, "used": 0, "reserved": 0}
        led["names"][ROOT_ADDRESS] = ROOT
        led["counts"][role] = led["counts"].get(role, 0) + 1
    return _ok(led, "session")


def _subagent_start(pol: dict, ev: HookEvent, led: dict) -> Decision:
    iid = ev.agent_id
    if iid in led["instances"]:
        return _ok(led, "revive", target=led["instances"][iid]["role"])
    for i, p in enumerate(led["pending"]):
        if p["role"] == ev.agent_type:
            led["pending"].pop(i)
            led["instances"][iid] = {"role": p["role"], "name": p["name"], "parent": p["parent"],
                                     "depth": p["depth"], "used": 0, "reserved": 0}
            led["names"][p["name"]] = iid
            led["counts"][p["role"]] = led["counts"].get(p["role"], 0) + 1
            return _ok(led, "bind", target=p["role"])
    led["instances"][iid] = {"role": None, "name": None, "parent": None, "depth": 0,
                             "used": 0, "reserved": 0}
    return _no(led, "identity", f"subagent {iid} ({ev.agent_type!r}) started without an "
               "allowed spawn; all its calls will be refused")


def _meter(ev: HookEvent, led: dict) -> None:
    inst = led["instances"].get(ev.agent_id or ROOT)
    if inst is not None and "tokens_used" in ev.facts:
        inst["used"] = max(inst["used"], int(ev.facts["tokens_used"]))


def _subagent_stop(pol: dict, ev: HookEvent, led: dict) -> Decision:
    _meter(ev, led)
    return _ok(led, "meter")


def _spawn(pol: dict, ev: HookEvent, led: dict, me: str, role: str) -> Decision:
    want = ev.tool_input.get("subagent_type")
    name = ev.tool_input.get("name")
    rp = pol["roles"][role]
    if want not in pol["roles"] or want == pol.get("launcher"):
        return _no(led, "default-deny", f"{want!r} is not a declared role", want)
    if want not in rp["can_spawn"]:
        return _no(led, "spawn-edges", f"{role!r} may not spawn {want!r}", want)
    if want not in rp["can_assign"]:
        return _no(led, "assign-edges", f"{role!r} may spawn {want!r} but not assign it a "
                   "task, and every spawn carries a prompt", want)
    if not name:
        return _no(led, "identity", "every spawn must set `name` so messages can be "
                   "attributed", want)
    if name in led["names"] or any(p["name"] == name for p in led["pending"]):
        return _no(led, "identity", f"an instance named {name!r} already exists", want)
    depth = led["instances"][me]["depth"] + 1
    if depth > pol["max_depth"]:
        return _no(led, "max-depth", f"spawning {want!r} would reach depth {depth}, above "
                   f"max_depth {pol['max_depth']}", want)
    used = led["counts"].get(want, 0) + sum(p["role"] == want for p in led["pending"])
    if used >= pol["roles"][want]["max_instances"]:
        return _no(led, "max-instances", f"{want!r} already has {used} of "
                   f"{pol['roles'][want]['max_instances']} instances this run", want)
    child_budget = (pol["roles"][want]["budget"] or {}).get("tokens", 0)
    left = _budget_left(pol, led, me)
    if left is not None and child_budget > left:
        return _no(led, "budget", f"spawning {want!r} reserves {child_budget:,} tokens but "
                   f"{role!r} has {left:,} left", want)
    if left is not None:
        led["instances"][me]["reserved"] += child_budget
    led["pending"].append({"role": want, "name": name, "parent": me, "depth": depth,
                           "tool_use_id": ev.tool_use_id})
    return _ok(led, "spawn-edges", target=want)


def _message(pol: dict, ev: HookEvent, led: dict, me: str, role: str) -> Decision:
    to = ev.tool_input.get("to") or ev.tool_input.get("recipient")
    if not to or to in ("*", "all"):
        return _no(led, "message-edges", "broadcast messages are not allowed", to)
    iid = led["names"].get(to)
    if iid is None:
        return _no(led, "message-edges", f"no instance named {to!r} in this swarm", to)
    their = led["instances"][iid]["role"]
    rp = pol["roles"][role]
    if their not in rp["can_message"] and their not in rp["can_assign"]:
        return _no(led, "message-edges", f"{role!r} may not message {their!r}", to)
    mine = set(led["frags"].get(me, []))
    if role in {a["merge_by"] for a in pol["aggregation"]} and mine:
        carried = {f"merged|{role}|{me}"}
    else:
        carried = mine  # fragments keep the instance that first read them
    theirs = set(led["frags"].get(iid, []))
    if carried and _would_aggregate(pol, their, theirs | carried):
        return _no(led, "aggregation", f"{their!r} would combine restricted fragments it may "
                   "not merge", to)
    if carried:
        led["frags"][iid] = sorted(theirs | carried)
    return _ok(led, "message-edges", target=to)


def _request(pol: dict, ev: HookEvent, led: dict, me: str, role: str) -> Decision:
    action = ev.tool_input.get("action")
    rid = ev.tool_input.get("request_id") or ev.tool_use_id
    if not any(c["action"] == action and c["requested_by"] == role for c in pol["collective"]):
        return _no(led, "collective", f"{role!r} may not request {action!r}", action)
    if rid in led["requests"]:
        return _no(led, "collective", f"request {rid!r} already exists", action)
    led["requests"][rid] = {"action": action, "requester": me, "votes": {}, "used": False}
    return _ok(led, "collective", target=rid)


def _vote(pol: dict, ev: HookEvent, led: dict, me: str, role: str) -> Decision:
    rid = ev.tool_input.get("request_id")
    req = led["requests"].get(rid)
    if req is None:
        return _no(led, "collective", f"no open request {rid!r}", rid)
    rule = next(c for c in pol["collective"] if c["action"] == req["action"])
    if role != rule["of"]:
        return _no(led, "collective", f"{role!r} is not a voter for {req['action']!r}", rid)
    if rule["independent"] and _descends_from(led, me, req["requester"]):
        return _no(led, "collective", "voter was spawned by the requester's subtree (Sybil)",
                   rid)
    if me in req["votes"]:
        return _no(led, "collective", "this instance already voted", rid)
    req["votes"][me] = bool(ev.tool_input.get("approve", False))
    return _ok(led, "collective", target=rid)


def _gate(pol: dict, led: dict, me: str, role: str, actions: list[str],
          target: str | None) -> Decision | None:
    depth = led["instances"][me]["depth"]
    for a in actions:
        if a in pol["non_delegable"] and depth >= 1:
            return _no(led, "non-delegable", f"{a!r} is non-delegable and {role!r} is a "
                       f"spawned agent (depth {depth})", target)
        for c in pol["collective"]:
            if c["action"] != a:
                continue
            if role != c["requested_by"]:
                return _no(led, "collective", f"only {c['requested_by']!r} may perform {a!r}",
                           target)
            ok = [rid for rid, r in led["requests"].items()
                  if r["action"] == a and r["requester"] == me and not r["used"]
                  and sum(r["votes"].values()) >= c["min"]]
            if not ok:
                return _no(led, "collective", f"{a!r} needs {c['min']} approvals from "
                           f"{c['of']!r} voters first", target)
            led["requests"][ok[0]]["used"] = True
    return None


def _pre_tool(pol: dict, ev: HookEvent, led: dict) -> Decision:
    tool = ev.tool_name or ""
    me = ev.agent_id or ROOT
    inst = led["instances"].get(me)
    if inst is None or inst["role"] is None:
        return _no(led, "identity", f"unknown instance {me!r}; calls are refused")
    role = inst["role"]
    _meter(ev, led)
    if ev.agent_type is not None and ev.agent_type != role:
        return _no(led, "identity", f"instance {me!r} was spawned as {role!r} but reports "
                   f"{ev.agent_type!r}")
    left = _budget_left(pol, led, me)
    if left is not None and left < 0:
        return _no(led, "budget", f"{role!r} has used its token budget")

    path = _call_path(ev) if tool in FILE_TOOLS else None
    target = ev.tool_input.get("subagent_type") or ev.tool_input.get("to") or path or \
        (ev.tool_input.get("command", "")[:80] or None)
    if tool in FILE_TOOLS and path is None:
        return _no(led, "default-deny", "path is outside the project", target)
    if tool in WRITE_TOOLS and any(globs.overlaps(path, p) for p in pol["protected"]):
        return _no(led, "integrity", f"{path} is protected by swarm.toml", target)

    if tool in SPAWN_TOOLS:
        return _spawn(pol, ev, led, me, role)
    if tool == "SendMessage":
        return _message(pol, ev, led, me, role)
    if tool == REQUEST_TOOL:
        return _request(pol, ev, led, me, role)
    if tool == VOTE_TOOL:
        return _vote(pol, ev, led, me, role)

    if not _role_allows(pol, role, ev, path):
        return _no(led, "permissions", f"{role!r} is not allowed {tool} on {target}", target)

    if tool in FILE_TOOLS:
        for ch in pol["channels"]:
            if not any(globs.overlaps(path, g) for g in ch["paths"]):
                continue
            side = "writers" if tool in WRITE_TOOLS else "readers"
            if role not in ch[side]:
                return _no(led, "channels", f"{role!r} is not among channel "
                           f"{ch['name']!r} {side}", target)

    actions = _actions_for(pol, ev, path)
    gated = _gate(pol, led, me, role, actions, target)
    if gated is not None:
        return gated

    frags = _label_sources(pol, ev, path, actions)
    if frags:
        mine = set(led["frags"].get(me, []))
        new = mine | {f"{f}|{me}" for f in frags}
        if _would_aggregate(pol, role, new):
            return _no(led, "aggregation", f"{role!r} would combine restricted fragments it "
                       "may not merge", target)
        led["frags"][me] = sorted(new)
    return _ok(led, "allow", target=target)


def decide(policy: dict, event: HookEvent, ledger: dict) -> Decision:
    """Pure: no I/O. Returns Decision(allow, rule, reason, ledger)."""
    led = copy.deepcopy(ledger) if ledger else new_ledger()
    ev = event
    if ev.event == "SessionStart":
        return _session_start(policy, ev, led)
    if ev.event == "SubagentStart":
        return _subagent_start(policy, ev, led)
    if ev.event in ("SubagentStop", "Stop"):
        return _subagent_stop(policy, ev, led)
    if ev.event != "PreToolUse":
        return _ok(led, "observe")
    if led["locked"]:
        return _no(led, "integrity" if led["locked"].startswith("integrity") else "identity",
                   led["locked"])
    if ev.facts.get("hash") != policy["hash"]:
        led["locked"] = "integrity: swarm.toml or a referenced file changed during the run"
        return _no(led, "integrity", led["locked"])
    return _pre_tool(policy, ev, led)
