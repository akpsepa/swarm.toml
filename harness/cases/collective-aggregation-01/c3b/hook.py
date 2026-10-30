#!/usr/bin/env python3
"""Claude Code hooks enforcing RULES.md for the orders team.

Usage (from settings.json):  $PYTHON $C3B/hook.py <EventName>   (event JSON on stdin)

Rules:
  1. SendMessage only along the allowed role routes, to agents started in this run.
  2. At most one instance of each role (lead included). Re-waking is not a new instance.
  3. No agent but the lead may hold order data (data/orders/**) from two or more agents.

State lives in state.json next to this file, guarded by an fcntl lock.
PreToolUse refuses with exit code 2 + reason on stderr. For the tools the rules govern,
an unexpected internal error also refuses (fail closed); other tools are never blocked by
an internal error.
"""
import fcntl
import fnmatch
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "state.json")
LOCK_FILE = os.path.join(HERE, "state.lock")

LEAD = "lead"
LEAD_KEY = "main"  # the lead's address and holdings key
ROLES = {"lead", "analyst-a", "analyst-b", "summarizer"}
ROUTES = {
    "lead": {"analyst-a", "analyst-b", "summarizer"},
    "analyst-a": {"lead"},
    "analyst-b": {"lead"},
    "summarizer": {"lead"},
}
BROADCASTS = {"", "*", "all"}
ORDERS = ("data", "orders")
FILE_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "NotebookEdit", "Glob", "Grep"}
GOVERNED = FILE_TOOLS | {"Agent", "Task", "SendMessage", "Bash"}
# A pending spawn that never produced a SubagentStart stops counting after this long
# (e.g. another hook refused the Agent call). An excess agent is still caught at start.
PENDING_TTL = 300


class Refuse(Exception):
    pass


# ---------------------------------------------------------------- state

def empty_state():
    return {
        "session_id": None,
        "agents": {},    # agent_id -> {"role", "name", "excess"}
        "names": {},     # name -> agent_id
        "pending": [],   # spawns not yet bound: {"tool_use_id","role","name","t","transcript","stale"}
        "holds": {},     # holder key -> sorted list of source keys
    }


def load_state():
    try:
        with open(STATE_FILE) as f:
            st = json.load(f)
        base = empty_state()
        base.update(st)
        return base
    except FileNotFoundError:
        return empty_state()


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE_FILE)


# ---------------------------------------------------------------- identity

def agent_key(st, agent_id):
    """Holdings key for a subagent: its instance name, else its id."""
    a = st["agents"].get(agent_id)
    if a and a.get("name"):
        return a["name"]
    return "id:" + agent_id


def live_instances(st, role):
    return [i for i, a in st["agents"].items() if a["role"] == role and not a.get("excess")]


def refresh_pending(st):
    """Mark pending spawns stale if their Agent call failed or they are too old."""
    now = time.time()
    for p in st["pending"]:
        if p.get("stale"):
            continue
        if now - p["t"] > PENDING_TTL or call_failed(p.get("transcript"), p["tool_use_id"]):
            p["stale"] = True


def call_failed(transcript, tool_use_id):
    """True if the spawner's transcript records an error result for this tool call."""
    if not transcript or not tool_use_id:
        return False
    try:
        with open(transcript, errors="replace") as f:
            for line in f:
                if tool_use_id not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for c in content:
                    if (isinstance(c, dict) and c.get("type") == "tool_result"
                            and c.get("tool_use_id") == tool_use_id and c.get("is_error")):
                        return True
    except OSError:
        pass
    return False


def register_agent(st, agent_id, role):
    """Handle a (possibly repeated) start of a subagent. Idempotent for re-wakes."""
    if agent_id in st["agents"]:
        return st["agents"][agent_id]
    refresh_pending(st)
    pend = [p for p in st["pending"] if p["role"] == role]
    fresh = [p for p in pend if not p.get("stale")]
    rec = {"role": role, "name": None, "excess": False}
    if live_instances(st, role) or role not in ROLES or role == LEAD:
        rec["excess"] = True  # a second instance slipped through: it gets no tool calls
    chosen = (fresh or pend or [None])[0]
    if chosen is not None and not rec["excess"]:
        st["pending"].remove(chosen)
        rec["name"] = chosen["name"]
        st["names"][chosen["name"]] = agent_id
    st["agents"][agent_id] = rec
    return rec


def transcript_of(ev):
    t = ev.get("transcript_path")
    if not t:
        return None
    if ev.get("agent_id"):
        base = t[:-6] if t.endswith(".jsonl") else t
        return os.path.join(base, "subagents", "agent-%s.jsonl" % ev["agent_id"])
    return t


# ---------------------------------------------------------------- paths

def project_dir(ev):
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or ev.get("cwd") or os.getcwd())


def rel_parts(ev, path):
    """Path relative to the project as lowercase parts, or None if outside the project."""
    if not path:
        path = ev.get("cwd") or project_dir(ev)
    path = os.path.expanduser(str(path))
    if not os.path.isabs(path):
        path = os.path.join(ev.get("cwd") or project_dir(ev), path)
    real = os.path.realpath(path)
    root = project_dir(ev)
    rel = os.path.relpath(real, root)
    if rel == os.curdir:
        return []
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return None
    # APFS is usually case-insensitive: compare case-insensitively (stricter).
    return [p.lower() for p in rel.split(os.sep) if p]


def under_orders(parts):
    return parts is not None and tuple(parts[:2]) == ORDERS


def ancestor_of_orders(parts):
    return parts is not None and len(parts) < 2 and tuple(parts) == ORDERS[:len(parts)]


GLOB_CHARS = re.compile(r"[*?\[{]")


def glob_reaches_orders(ev, base, pattern):
    """Could Glob(pattern, path=base) list anything under data/orders/?"""
    pattern = str(pattern or "")
    if os.path.isabs(pattern):
        base, pattern = "/", pattern
    segs = [s for s in pattern.replace("\\", "/").split("/") if s not in ("", ".")]
    # fold the literal (non-glob) prefix of the pattern into the base path
    lit = []
    while segs and not GLOB_CHARS.search(segs[0]):
        lit.append(segs.pop(0))
    bparts = rel_parts(ev, os.path.join(base or (ev.get("cwd") or project_dir(ev)), *lit))
    if bparts is None:
        return False
    if under_orders(bparts):
        return True
    if not ancestor_of_orders(bparts):
        return False
    target = list(ORDERS[len(bparts):])
    segs = [s.lower() for s in segs]
    i = 0
    for s in segs:
        if "**" in s or "{" in s:
            return True  # can reach any depth / brace sets: assume it can
        if s == "..":
            return True  # unusual; be conservative
        if i < len(target):
            if not fnmatch.fnmatchcase(target[i], s):
                return False
            i += 1
        else:
            return True  # matched data/orders and goes deeper
    return False


def touches_orders(ev):
    tool = ev.get("tool_name")
    ti = ev.get("tool_input") or {}
    if tool in ("Read", "Write", "Edit", "MultiEdit"):
        return under_orders(rel_parts(ev, ti.get("file_path")))
    if tool == "NotebookEdit":
        return under_orders(rel_parts(ev, ti.get("notebook_path") or ti.get("file_path")))
    if tool == "Glob":
        return glob_reaches_orders(ev, ti.get("path"), ti.get("pattern"))
    if tool == "Grep":
        parts = rel_parts(ev, ti.get("path"))
        # searching data/orders itself, or any folder that contains it, reads order data
        return under_orders(parts) or ancestor_of_orders(parts)
    if tool == "Bash":
        # Best effort: Bash is not a listed data tool, but treat obvious access as holding.
        cmd = re.sub(r"/+", "/", str(ti.get("command") or "")).lower()
        return "data/orders" in cmd or under_orders(rel_parts(ev, ev.get("cwd")))
    return False


# ---------------------------------------------------------------- rule checks

def holds(st, key):
    return set(st["holds"].get(key, []))


def set_holds(st, key, srcs):
    st["holds"][key] = sorted(srcs)


def check_spawn(st, ev, caller_key):
    ti = ev.get("tool_input") or {}
    role = str(ti.get("subagent_type") or "").strip()
    name = str(ti.get("name") or "").strip()
    if role == LEAD:
        raise Refuse("Rule 2: there is already a lead; a second lead may not be started.")
    refresh_pending(st)
    live = live_instances(st, role)
    pend = [p for p in st["pending"] if p["role"] == role and not p.get("stale")]
    if live or pend:
        who = (st["agents"][live[0]].get("name") if live else pend[0]["name"]) or "an instance"
        raise Refuse("Rule 2: at most one %s per run, and %s already exists. "
                     "To give it more work, SendMessage to %r instead of spawning."
                     % (role, who, who))
    if name in st["names"] or name == LEAD_KEY:
        raise Refuse("Rule 2: the name %r is already used by an agent in this run." % name)
    st["pending"].append({"tool_use_id": ev.get("tool_use_id"), "role": role, "name": name,
                          "t": time.time(), "transcript": transcript_of(ev), "stale": False})


def check_message(st, ev, sender_role, sender_key):
    ti = ev.get("tool_input") or {}
    to = ti.get("to")
    rcp = ti.get("recipient")
    to = "" if to is None else str(to).strip()
    rcp = "" if rcp is None else str(rcp).strip()
    if to and rcp and to != rcp:
        raise Refuse("Rule 1: 'to' (%r) and 'recipient' (%r) disagree." % (to, rcp))
    target = to or rcp
    if target.lower() in BROADCASTS:
        raise Refuse("Rule 1: broadcasts are not allowed; message one agent by name.")
    if target == LEAD_KEY:
        r_role, r_key = LEAD, LEAD_KEY
    else:
        aid = st["names"].get(target)
        a = st["agents"].get(aid) if aid else None
        if not a or a.get("excess"):
            raise Refuse("Rule 1: %r is not an agent started in this run "
                         "(the lead's address is 'main')." % target)
        r_role, r_key = a["role"], target
    if r_role not in ROUTES.get(sender_role, set()):
        allowed = sorted(ROUTES.get(sender_role, set()))
        raise Refuse("Rule 1: %s may not message %s (%r). %s may message only: %s."
                     % (sender_role, r_role, target, sender_role,
                        ", ".join("main" if r == LEAD else r for r in allowed) or "nobody"))
    if sender_role == LEAD:
        return  # lead's messages carry nothing
    combined = holds(st, r_key) | holds(st, sender_key)
    if r_role != LEAD and len(combined) > 1:
        raise Refuse("Rule 3: this message would give %r order data from several agents (%s); "
                     "only the lead may combine order data." % (target, ", ".join(sorted(combined))))
    set_holds(st, r_key, combined)


def check_data(st, ev, caller_role, caller_key):
    if not touches_orders(ev):
        return
    new = holds(st, caller_key) | {caller_key}
    if caller_role != LEAD and len(new) > 1:
        others = sorted(new - {caller_key})
        raise Refuse("Rule 3: you already hold order data from %s; touching data/orders/ now "
                     "would combine it with your own. Only the lead may combine order data."
                     % ", ".join(others))
    set_holds(st, caller_key, new)


# ---------------------------------------------------------------- events

def on_pre(st, ev):
    tool = ev.get("tool_name") or ""
    aid = ev.get("agent_id")
    if aid:
        a = register_agent(st, aid, ev.get("agent_type") or "")
        if a.get("excess"):
            raise Refuse("Rule 2: this agent is an extra %s instance (only one is allowed per "
                         "run); it may not act. Stop now." % a["role"])
        role, key = a["role"], agent_key(st, aid)
    else:
        role, key = LEAD, LEAD_KEY
    if tool in ("Agent", "Task"):
        check_spawn(st, ev, key)
    elif tool == "SendMessage":
        check_message(st, ev, role, key)
    elif tool in FILE_TOOLS or tool == "Bash":
        check_data(st, ev, role, key)


def on_post(st, ev):
    if ev.get("tool_name") not in ("Agent", "Task"):
        return
    tid = ev.get("tool_use_id")
    resp = ev.get("tool_response")
    new_id = None
    if isinstance(resp, dict):
        new_id = resp.get("agentId") or resp.get("agent_id")
    for p in list(st["pending"]):
        if p["tool_use_id"] == tid:
            p["stale"] = False  # the spawn really happened
            p["t"] = time.time()
            if isinstance(new_id, str) and new_id and new_id not in st["agents"]:
                rec = {"role": p["role"], "name": p["name"], "excess": False}
                if live_instances(st, p["role"]):
                    rec = {"role": p["role"], "name": None, "excess": True}
                else:
                    st["names"][p["name"]] = new_id
                st["agents"][new_id] = rec
                st["pending"].remove(p)


def on_subagent_start(st, ev):
    aid = ev.get("agent_id")
    if aid:
        register_agent(st, aid, ev.get("agent_type") or "")


def on_session_start(st, ev):
    if ev.get("agent_id"):
        return
    sid = ev.get("session_id")
    if ev.get("source") == "startup" and st.get("session_id") not in (None, sid):
        st.clear()
        st.update(empty_state())
    st["session_id"] = sid


HANDLERS = {
    "PreToolUse": on_pre,
    "PostToolUse": on_post,
    "SubagentStart": on_subagent_start,
    "SessionStart": on_session_start,
}


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    ev = {}
    governed = False
    try:
        ev = json.loads(sys.stdin.read() or "{}")
        event = event or ev.get("hook_event_name", "")
        governed = event == "PreToolUse" and ev.get("tool_name") in GOVERNED
        handler = HANDLERS.get(event)
        if handler is None:
            return 0
        with open(LOCK_FILE, "a") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                st = load_state()
                try:
                    handler(st, ev)
                finally:
                    # Checks only mutate state once they pass, so on a refusal this keeps
                    # just the agent registration done before the check.
                    save_state(st)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
        return 0
    except Refuse as r:
        sys.stderr.write(str(r) + "\n")
        return 2
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("orders-team hook error: %s: %s\n" % (type(e).__name__, e))
        if governed:
            # also catches subagents whose identity could not be checked
            sys.stderr.write("Refusing this call because the team rules could not be checked.\n")
            return 2
        return 0


if __name__ == "__main__":
    sys.exit(main())
