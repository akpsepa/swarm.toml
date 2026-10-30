"""Claude Code hooks enforcing RULES.md for the counting team.

Usage (from settings.json): python3 hook.py <EventName>   (event JSON on stdin)

PreToolUse exits 2 with a reason on stderr to refuse a call, 0 to allow.
State lives in state.json next to this script (override: C3B_STATE_DIR),
guarded by an fcntl lock because hooks of several agents run concurrently.
"""

import fcntl
import json
import os
import re
import shlex
import sys
import time
import traceback
from fnmatch import fnmatchcase

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.environ.get("C3B_STATE_DIR") or HERE
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOCK_FILE = os.path.join(STATE_DIR, "state.lock")
LOG_FILE = os.path.join(STATE_DIR, "hook-errors.log")

ROOT = "main"  # the lead's address and our key for the root agent

# Rule 1: sender role -> recipient roles
ROUTES = {
    "lead": {"planner", "checker"},
    "planner": {"lead", "checker", "worker"},
    "worker": {"planner"},
    "checker": {"lead", "planner"},
}
BROADCASTS = {"", "*", "all"}

# Rule 2: total instances per role over the whole run
LIMITS = {"lead": 1, "planner": 1, "worker": 2, "checker": 1}

# Rule 3: out/**
OUT_DIR_NAME = "out"
OUT_WRITERS = {"lead", "planner"}
OUT_READERS = {"lead", "planner", "checker"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Glob", "Grep"}

# Rule 4: tokens per instance
BUDGETS = {"lead": 1_000_000, "planner": 400_000, "worker": 150_000, "checker": 100_000}

SPAWN_TOOLS = {"Agent", "Task"}

CASE_INSENSITIVE_FS = sys.platform in ("darwin", "win32")


class Refuse(Exception):
    pass


# --------------------------------------------------------------------------- state

def empty_state(session_id=None):
    # spawns: one record per allowed Agent call (keyed by tool_use_id)
    #   status: "pending" (no agent bound yet), "started" (bound), "released" (call failed)
    # agents: agent_id -> {role, name, spawn}
    return {"session_id": session_id, "spawns": [], "agents": {}}


class Locked:
    def __enter__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        try:
            with open(STATE_FILE) as f:
                self.state = json.load(f)
        except FileNotFoundError:
            self.state = empty_state()
        return self

    def save(self):
        tmp = STATE_FILE + ".tmp.%d" % os.getpid()
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, STATE_FILE)

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


# --------------------------------------------------------------------------- transcripts

def read_jsonl(path):
    if not path:
        return
    try:
        with open(path, errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue  # partially written last line
                if isinstance(obj, dict):
                    yield obj
    except (FileNotFoundError, IsADirectoryError, PermissionError):
        return


def tokens_used(path):
    per_msg = {}
    for obj in read_jsonl(path):
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        mid, usage = msg.get("id"), msg.get("usage")
        if not mid or not isinstance(usage, dict):
            continue
        n = 0
        for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens"):
            v = usage.get(k)
            if isinstance(v, (int, float)):
                n += int(v)
        per_msg[mid] = max(per_msg.get(mid, 0), n)
    return sum(per_msg.values())


def own_transcript(ev):
    """Transcript of the agent that triggered this hook."""
    tp = ev.get("transcript_path") or ""
    aid = ev.get("agent_id")
    if not aid:
        return tp
    if os.path.basename(tp) == "agent-%s.jsonl" % aid:
        return tp
    base = tp[:-len(".jsonl")] if tp.endswith(".jsonl") else tp
    return os.path.join(base, "subagents", "agent-%s.jsonl" % aid)


def tool_result_status(path, tool_use_id):
    """None if no result yet, else True when the result is an error."""
    found = None
    for obj in read_jsonl(path):
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for c in content:
            if (isinstance(c, dict) and c.get("type") == "tool_result"
                    and c.get("tool_use_id") == tool_use_id):
                found = bool(c.get("is_error"))
    return found


# --------------------------------------------------------------------------- bookkeeping

def caller_key(ev):
    return ev.get("agent_id") or ROOT


def caller_role(st, ev):
    aid = ev.get("agent_id")
    if not aid:
        return "lead"
    rec = st["agents"].get(aid)
    if rec and rec.get("role"):
        return rec["role"]
    return ev.get("agent_type")


def live(sp):
    return sp["status"] != "released"


def refresh_spawns(st):
    """Release spawn records whose Agent call failed (e.g. refused by another hook)."""
    for sp in st["spawns"]:
        if sp["status"] != "pending" or sp.get("launched"):
            continue
        res = tool_result_status(sp.get("parent_transcript"), sp["id"])
        if res is True:
            sp["status"] = "released"
        elif res is False:
            sp["launched"] = True


def role_count(st, role):
    n = 1 if role == "lead" else 0  # the root lead itself
    n += sum(1 for sp in st["spawns"] if live(sp) and sp["role"] == role)
    n += sum(1 for a in st["agents"].values() if a.get("spawn") is None and a.get("role") == role)
    return n


def reserved_by(st, key):
    return sum(sp["budget"] for sp in st["spawns"] if live(sp) and sp["parent"] == key)


def known_names(st):
    names = {ROOT: "lead"}
    for sp in st["spawns"]:
        if live(sp) and (sp["status"] == "started" or sp.get("launched")):
            names[sp["name"]] = sp["role"]
    return names


def bind(st, sp, agent_id):
    sp["status"] = "started"
    sp["agent_id"] = agent_id
    st["agents"][agent_id] = {"role": sp["role"], "name": sp["name"], "spawn": sp["id"]}


# --------------------------------------------------------------------------- paths

def project_dir(ev):
    return os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR") or ev.get("cwd") or os.getcwd())


def norm(p):
    return p.casefold() if CASE_INSENSITIVE_FS else p


def inside(path, root):
    path, root = norm(path), norm(root)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def resolve(p, cwd):
    p = os.path.expanduser(os.path.expandvars(p))
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    return os.path.realpath(p)


MAGIC = re.compile(r"[*?\[{]")


def expand_braces(pat, limit=64):
    out, todo = [], [pat]
    while todo and len(out) + len(todo) <= limit:
        p = todo.pop()
        m = re.search(r"\{([^{}]*)\}", p)
        if not m:
            out.append(p)
            continue
        for alt in m.group(1).split(","):
            todo.append(p[:m.start()] + alt + p[m.end():])
    return out + todo


def comps_can_reach(pat, path, include_self=False):
    """Could glob components `pat` match `path` components followed by >=1 more component
    (or by none, when include_self)?"""
    if not path:
        return bool(pat) or include_self
    if not pat:
        return False
    if pat[0] == "**":
        return (comps_can_reach(pat[1:], path, include_self)
                or comps_can_reach(pat, path[1:], include_self))
    return (fnmatchcase(norm(path[0]), norm(pat[0]))
            and comps_can_reach(pat[1:], path[1:], include_self))


def glob_touches(base, pattern, out_dir, include_self=False):
    """Does `pattern` (relative to dir `base`, or absolute) reach anything in out_dir?
    include_self: matching out/ itself counts (shell commands may recurse into it)."""
    for pat in expand_braces(pattern):
        if os.path.isabs(pat):
            start, pat = os.sep, pat.lstrip(os.sep)
        else:
            start = base
        comps = [c for c in pat.split("/") if c not in ("", ".")]
        lit = []
        while comps and not MAGIC.search(comps[0]):
            lit.append(comps.pop(0))
        b = resolve(os.path.join(start, *lit), base) if lit else os.path.realpath(start)
        if inside(b, out_dir):
            return True
        if comps and inside(out_dir, b) and os.path.exists(out_dir):
            rel = os.path.relpath(out_dir, b).split(os.sep)
            if comps_can_reach(comps, rel, include_self):
                return True
    return False


def file_tool_touches_out(tool, ti, cwd, out_dir):
    if tool in ("Read", "Write", "Edit", "MultiEdit"):
        p = ti.get("file_path")
        return bool(p) and inside(resolve(p, cwd), out_dir)
    if tool == "NotebookEdit":
        p = ti.get("notebook_path") or ti.get("file_path")
        return bool(p) and inside(resolve(p, cwd), out_dir)
    if tool == "Glob":
        base = resolve(ti.get("path") or cwd, cwd)
        if inside(base, out_dir):
            return True
        return glob_touches(base, ti.get("pattern") or "**", out_dir)
    if tool == "Grep":
        base = resolve(ti.get("path") or cwd, cwd)
        if inside(base, out_dir):
            return True
        if not (inside(out_dir, base) and os.path.exists(out_dir)):
            return False
        g = (ti.get("glob") or "").strip()
        if not g or g.startswith("!"):
            return True  # searches everything below base, out/ included
        for one in expand_braces(g):
            while one.startswith("./"):
                one = one[2:]
            if "/" not in one:
                one = "**/" + one  # rg matches slash-less globs on any basename
            if glob_touches(base, one, out_dir):
                return True
        return False
    return False


# Bash (best effort): not in RULES.md's tool list, but it is an easy way around rule 3.
READONLY_CMDS = {
    "cat", "head", "tail", "less", "more", "ls", "wc", "grep", "egrep", "fgrep", "rg",
    "diff", "cmp", "comm", "stat", "file", "sort", "uniq", "cut", "tr", "nl", "md5sum",
    "md5", "shasum", "sha1sum", "sha256sum", "jq", "echo", "printf", "test", "[", "true",
    "cd", "pwd", "realpath", "basename", "dirname", "od", "xxd", "hexdump", "column",
    "paste", "find", "tree", "du",
}
CMD_SEPS = {";", "&&", "||", "|", "&", "|&", "(", ")", ";;", "\n"}
REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>", ">&"}


def bash_tokens(cmd):
    try:
        lx = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lx.whitespace_split = True
        return list(lx)
    except ValueError:
        return re.findall(r"[;&|()<>]+|[^\s;&|()<>]+", cmd)


def bash_path_candidates(tokens):
    seen = []
    for tok in tokens:
        parts = [tok] + [p for p in re.split(r"[\s;&|()<>`\"']+|\$\(", tok) if p]
        for p in parts:
            if p.startswith("-") and "=" in p:
                p = p.split("=", 1)[1]
            elif "=" in p and not p.startswith("/"):
                p = p.split("=", 1)[1]
            if p:
                seen.append(p)
    return seen


def bash_touches_out(tokens, cwd, out_dir):
    for p in bash_path_candidates(tokens):
        if MAGIC.search(p):
            if glob_touches(cwd, p, out_dir, include_self=True):
                return True
        elif inside(resolve(p, cwd), out_dir):
            return True
    return False


def bash_is_readonly(tokens, cwd, out_dir):
    expect_cmd = True
    cmd = None
    skip = False
    for i, tok in enumerate(tokens):
        if skip:
            skip = False
            continue
        if tok in CMD_SEPS:
            expect_cmd = True
            continue
        if tok in REDIRECTS:
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            skip = True
            if target.isdigit() or target == "-":
                continue  # fd duplication
            if (not target or MAGIC.search(target)
                    or inside(resolve(target, cwd), out_dir)):
                return False
            continue
        if expect_cmd:
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
                continue  # env assignment prefix
            cmd = os.path.basename(tok)
            if cmd not in READONLY_CMDS:
                return False
            expect_cmd = False
            continue
        if cmd == "find" and tok in ("-delete", "-exec", "-execdir", "-ok", "-okdir",
                                     "-fprint", "-fprint0", "-fprintf", "-fls"):
            return False
        if cmd == "sort" and (tok == "-o" or tok.startswith("--output") or
                              (tok.startswith("-") and not tok.startswith("--") and "o" in tok)):
            return False
    return True


# --------------------------------------------------------------------------- checks

def check_budget(st, ev, role):
    if role not in BUDGETS:
        return None
    used = tokens_used(own_transcript(ev))
    reserved = reserved_by(st, caller_key(ev))
    if used > BUDGETS[role] - reserved:
        raise Refuse(
            "Rule 4: token budget exhausted for this %s (used %d, budget %d, reserved for "
            "agents it started %d). No further tool calls are allowed."
            % (role, used, BUDGETS[role], reserved))
    return used


def check_spawn(st, ev, role, used):
    ti = ev.get("tool_input") or {}
    tuid = ev.get("tool_use_id")
    if tuid and any(sp["id"] == tuid for sp in st["spawns"]):
        return False  # same call seen again; already accounted for
    new_role = ti.get("subagent_type")
    name = ti.get("name")
    if new_role not in LIMITS:
        raise Refuse("Rule 2/4: unknown role %r; cannot count or budget it." % (new_role,))
    if not isinstance(name, str) or name.strip() in BROADCASTS or name.strip() == ROOT:
        raise Refuse("Rule 1: %r cannot be used as an agent name." % (name,))
    if role_count(st, new_role) + 1 > LIMITS[new_role]:
        raise Refuse(
            "Rule 2: at most %d %s instance(s) in the whole run; that limit is reached. "
            "Re-use an existing agent by messaging it instead." % (LIMITS[new_role], new_role))
    if role not in BUDGETS:
        raise Refuse("Rule 4: caller role %r has no budget to reserve from." % (role,))
    left = BUDGETS[role] - used - reserved_by(st, caller_key(ev))
    if BUDGETS[new_role] > left:
        raise Refuse(
            "Rule 4: starting a %s reserves %d tokens but this %s has only %d left "
            "(budget - used - reserved)." % (new_role, BUDGETS[new_role], role, left))
    st["spawns"].append({
        "id": tuid or "anon-%f" % time.time(),
        "role": new_role,
        "name": name,
        "parent": caller_key(ev),
        "parent_transcript": own_transcript(ev),
        "budget": BUDGETS[new_role],
        "status": "pending",
        "agent_id": None,
        "launched": False,
        "t": time.time(),
    })
    return True


def check_message(st, ev, role):
    ti = ev.get("tool_input") or {}
    targets = []
    for k in ("to", "recipient"):
        if k in ti and ti[k] is not None:
            targets.append(ti[k])
    if not targets:
        raise Refuse("Rule 1: message has no recipient; broadcasts are not allowed.")
    names = None
    for t in targets:
        if not isinstance(t, str):
            raise Refuse("Rule 1: recipient must be a single agent name.")
        t = t.strip()
        if t.lower() in BROADCASTS:
            raise Refuse("Rule 1: broadcasts (%r) are not allowed; message one agent." % t)
        if names is None:
            names = known_names(st)
        if t not in names:
            raise Refuse("Rule 1: %r is not an agent started in this run (the lead is 'main')." % t)
        to_role = names[t]
        if to_role not in ROUTES.get(role, set()):
            allowed = ", ".join(sorted(ROUTES.get(role, set()))) or "nobody"
            raise Refuse("Rule 1: a %s may not message a %s (%r). A %s may message: %s."
                         % (role, to_role, t, role, allowed))


def check_out_access(ev, role, tool):
    ti = ev.get("tool_input") or {}
    cwd = ev.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    out_dir = os.path.join(project_dir(ev), OUT_DIR_NAME)
    out_dir = os.path.realpath(out_dir)
    if tool in WRITE_TOOLS or tool in READ_TOOLS:
        if role in OUT_WRITERS:
            return
        writing = tool in WRITE_TOOLS
        if not writing and role in OUT_READERS:
            return
        if file_tool_touches_out(tool, ti, cwd, out_dir):
            raise Refuse("Rule 3: a %s may not %s files in out/."
                         % (role, "write" if writing else "read"))
    elif tool == "Bash":
        if role in OUT_WRITERS:
            return
        cmd = ti.get("command")
        if not isinstance(cmd, str):
            return
        toks = bash_tokens(cmd)
        if not bash_touches_out(toks, cwd, out_dir):
            return
        if role in OUT_READERS and bash_is_readonly(toks, cwd, out_dir):
            return
        if role in OUT_READERS:
            raise Refuse("Rule 3: a %s may only read out/; this shell command could write "
                         "there. Use the Read/Glob/Grep tools." % role)
        raise Refuse("Rule 3: a %s may not read or write out/ (shell command touches it)." % role)


# --------------------------------------------------------------------------- events

def on_pre_tool_use(ev):
    tool = ev.get("tool_name") or ""
    with Locked() as L:
        st = L.state
        role = caller_role(st, ev)
        used = check_budget(st, ev, role)
        if tool in SPAWN_TOOLS:
            refresh_spawns(st)
            try:
                check_spawn(st, ev, role, used or 0)
            finally:
                L.save()  # refresh may have released records even when refusing
            return
        if tool == "SendMessage":
            refresh_spawns(st)
            L.save()
            check_message(st, ev, role)
            return
    check_out_access(ev, role, tool)


def on_post_tool_use(ev):
    if ev.get("tool_name") not in SPAWN_TOOLS:
        return
    tuid = ev.get("tool_use_id")
    resp = ev.get("tool_response")
    aid = None
    if isinstance(resp, dict):
        aid = resp.get("agentId") or resp.get("agent_id")
    with Locked() as L:
        st = L.state
        for sp in st["spawns"]:
            if sp["id"] != tuid:
                continue
            sp["launched"] = True
            if sp["status"] == "released":
                sp["status"] = "pending"
            if sp["agent_id"] is None and isinstance(aid, str) and aid not in st["agents"]:
                bind(st, sp, aid)
            break
        L.save()


def on_subagent_start(ev):
    aid = ev.get("agent_id")
    if not aid:
        return
    role = ev.get("agent_type")
    with Locked() as L:
        st = L.state
        if aid in st["agents"]:
            return  # re-woken agent: not a new instance
        refresh_spawns(st)
        cands = [sp for sp in st["spawns"]
                 if sp["status"] == "pending" and sp["agent_id"] is None and sp["role"] == role]
        if cands:
            bind(st, min(cands, key=lambda s: s.get("t", 0)), aid)
        else:
            # Started without a spawn we saw; still an instance of its role.
            st["agents"][aid] = {"role": role, "name": None, "spawn": None}
        L.save()


def on_session_start(ev):
    if ev.get("agent_id") or ev.get("source") != "startup":
        return
    if ev.get("agent_type") not in (None, "lead"):
        return
    with Locked() as L:
        if L.state.get("session_id") != ev.get("session_id"):
            L.state = empty_state(ev.get("session_id"))
            L.save()


def log_error():
    try:
        with open(LOG_FILE, "a") as f:
            f.write("%s\n%s\n" % (time.ctime(), traceback.format_exc()))
    except OSError:
        pass


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        ev = json.loads(sys.stdin.read() or "{}")
        if not isinstance(ev, dict):
            raise ValueError("event is not an object")
        event = event or ev.get("hook_event_name", "")
    except Exception:
        log_error()
        if event == "PreToolUse":
            sys.stderr.write("Rule hook could not parse its input; refusing to be safe.\n")
            sys.exit(2)
        sys.exit(0)

    try:
        if event == "PreToolUse":
            on_pre_tool_use(ev)
        elif event == "PostToolUse":
            on_post_tool_use(ev)
        elif event == "SubagentStart":
            on_subagent_start(ev)
        elif event == "SessionStart":
            on_session_start(ev)
    except Refuse as r:
        sys.stderr.write(str(r) + "\n")
        sys.exit(2)
    except Exception:
        log_error()
        if event == "PreToolUse":
            # Fail closed: an enforcement hook that silently allows is worse than a retry.
            sys.stderr.write("Rule hook internal error; call refused. Retry once.\n")
            sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
