#!/usr/bin/env python3
"""Claude Code hooks enforcing RULES.md for the release team.

Usage (from settings.json):  $PYTHON $C3B/hook.py <EventName>

Rules:
  1. SendMessage routes between roles (names resolved through our own name -> role map).
  2. At most one instance of each role per run (the lead included).
  3. Read/write access to src/** and public/** by role.

State lives next to this script in state.json, guarded by an flock on state.lock.
PreToolUse decisions fail closed: if the hook itself errors on a governed tool the call is
refused (exit 2), because any other non-zero exit would let the call through.
"""
import fcntl
import fnmatch
import json
import os
import shlex
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state.json")
LOCK_PATH = os.path.join(HERE, "state.lock")
LOG_PATH = os.path.join(HERE, "hook.log")

ROOT_ROLE = "lead"
ROOT_ADDRESS = "main"

# Rule 1: sender role -> recipient roles.
ROUTES = {
    "lead": {"coder", "reviewer", "publisher"},
    "coder": {"lead", "reviewer"},
    "reviewer": {"lead", "coder"},
    "publisher": {"lead"},
}
BROADCAST = {"", "*", "all"}

# Rule 3: top-level folder -> roles allowed to write / read.
FOLDERS = {
    "src": {"write": {"lead", "coder"}, "read": {"lead", "coder", "reviewer"}},
    "public": {"write": {"lead", "publisher"}, "read": {"lead"}},
}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Glob", "Grep"}
SPAWN_TOOLS = {"Agent", "Task"}
GLOB_CHARS = set("*?[{")

# A spawn the lead asked for that never produced a SubagentStart nor a successful
# PostToolUse is assumed to have been refused elsewhere after this long.
PENDING_TTL = 600


class Block(Exception):
    pass


# ---------------------------------------------------------------- state

def empty_state():
    return {"agents": {}, "names": {}, "pending": [], "root_cwd": None}


class Locked:
    """Exclusive lock around a read-modify-write of the state file."""

    def __enter__(self):
        self.fd = open(LOCK_PATH, "a+")
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        try:
            with open(STATE_PATH) as f:
                self.state = json.load(f)
            for k, v in empty_state().items():
                self.state.setdefault(k, v)
        except FileNotFoundError:
            self.state = empty_state()
        return self

    def save(self):
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, STATE_PATH)

    def __exit__(self, *exc):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        self.fd.close()
        return False


def log(msg):
    try:
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- identity

def caller_role(ev, state):
    if not ev.get("agent_id"):
        return ROOT_ROLE
    role = ev.get("agent_type")
    if not role:
        role = (state["agents"].get(ev["agent_id"]) or {}).get("role")
    return role


def spawn_failed(p):
    """True if the lead's transcript shows this Agent call ended in an error (e.g. another
    hook refused it), so it will never start."""
    path = p.get("transcript")
    if not path:
        return False
    try:
        with open(path) as f:
            for line in f:
                if p["tool_use_id"] not in line:
                    continue
                try:
                    content = json.loads(line).get("message", {}).get("content")
                except ValueError:
                    continue
                if not isinstance(content, list):
                    continue
                for item in content:
                    if (isinstance(item, dict) and item.get("type") == "tool_result"
                            and item.get("tool_use_id") == p["tool_use_id"]):
                        return bool(item.get("is_error"))
    except OSError:
        pass
    return False


def live_pending(state):
    """Pending spawns that may still start (not bound, not known to have failed)."""
    now = time.time()
    keep = []
    for p in state["pending"]:
        if p.get("bound"):
            continue
        if p.get("launched"):
            keep.append(p)
            continue
        if spawn_failed(p) or now - p.get("ts", now) > PENDING_TTL:
            p["dead"] = True
            continue
        keep.append(p)
    # Drop dead entries so they can never be bound.
    state["pending"] = [p for p in state["pending"] if not p.get("dead")]
    return keep


# ---------------------------------------------------------------- paths

def project_dir(ev, state):
    return (os.environ.get("CLAUDE_PROJECT_DIR") or state.get("root_cwd")
            or ev.get("cwd") or os.getcwd())


def _abs(p, cwd):
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    return p


def rel_variants(path, proj):
    """Relative parts of `path` under `proj`, for lexical and symlink-resolved forms.
    Parts are casefolded (macOS file systems are case-insensitive by default)."""
    out = []
    for pp in {os.path.normpath(path), os.path.realpath(path)}:
        for pr in {os.path.normpath(proj), os.path.realpath(proj)}:
            rel = os.path.relpath(pp, pr)
            if rel == ".":
                out.append(())
            elif rel != ".." and not rel.startswith(".." + os.sep):
                out.append(tuple(x.casefold() for x in rel.split(os.sep)))
    return out


def folder_of(path, proj):
    """Protected folder containing `path` (or equal to it), else None."""
    for parts in rel_variants(path, proj):
        if parts and parts[0] in FOLDERS:
            return parts[0]
    return None


def below(base, proj):
    """Protected folders lying strictly below directory `base`, each with its path parts
    relative to `base` (casefolded)."""
    out = {}
    for b in {os.path.normpath(base), os.path.realpath(base)}:
        for pr in {os.path.normpath(proj), os.path.realpath(proj)}:
            for name in FOLDERS:
                rel = os.path.relpath(os.path.join(pr, name), b)
                if rel not in (".", "..") and not rel.startswith(".." + os.sep):
                    out.setdefault(name, set()).add(
                        tuple(x.casefold() for x in rel.split(os.sep)))
    return out


def split_pattern(pattern):
    """Split a glob into its literal leading directories and the wildcard remainder."""
    if not pattern:
        return [], []
    segs = pattern.split("/")
    i = 0
    while i < len(segs) - 1 and not (GLOB_CHARS & set(segs[i])):
        i += 1
    if not (GLOB_CHARS & set(segs[i])):
        i += 1  # fully literal pattern
    return segs[:i], [s for s in segs[i:] if s not in ("", ".")]


def pattern_reaches(rest, target_parts):
    """Could glob segments `rest` (relative to a base dir) match something at or below
    the directory reached by `target_parts` from that base?"""
    if ".." in rest:
        return True
    for i, seg in enumerate(target_parts):
        if i >= len(rest):
            return False
        pat = rest[i]
        if "**" in pat or "{" in pat:
            return True
        if not fnmatch.fnmatchcase(seg, pat.casefold()):
            return False
    return True


def glob_folders(ev, proj):
    ti = ev.get("tool_input") or {}
    cwd = ev.get("cwd") or proj
    base = _abs(ti.get("path") or cwd, cwd)
    pattern = os.path.expanduser(ti.get("pattern") or "")
    lit, rest = split_pattern(pattern)
    if lit:
        base = os.path.join(base, "/".join(lit) or "/")  # absolute lit replaces base
    f = folder_of(base, proj)
    if f:
        return {f}
    return {name for name, targets in below(base, proj).items()
            if any(pattern_reaches(rest, tp) for tp in targets)}


def grep_folders(ev, proj):
    ti = ev.get("tool_input") or {}
    cwd = ev.get("cwd") or proj
    base = _abs(ti.get("path") or cwd, cwd)
    f = folder_of(base, proj)
    if f:
        return {f}
    # Grep descends into every subfolder, so any protected folder below is searched.
    return set(below(base, proj))


def bash_folders(ev, proj):
    """Best effort: protected folders a shell command names as a path."""
    ti = ev.get("tool_input") or {}
    cmd = ti.get("command") or ""
    cwd = ev.get("cwd") or proj
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = cmd.split()
    hit = set()
    for tok in tokens:
        for piece in tok.split("="):
            piece = piece.strip()
            if not piece or piece.startswith("-"):
                continue
            piece = piece.replace("${CLAUDE_PROJECT_DIR}", proj) \
                .replace("$CLAUDE_PROJECT_DIR", proj)
            f = folder_of(_abs(piece, cwd), proj)
            if f:
                hit.add(f)
    return hit


# ---------------------------------------------------------------- rules

def check_send(ev, state):
    ti = ev.get("tool_input") or {}
    sender = caller_role(ev, state)
    if sender not in ROUTES:
        raise Block(f"SendMessage refused: sender role {sender!r} may not message anyone.")
    targets = []
    for key in ("to", "recipient"):
        if key in ti:
            targets.append(ti[key])
    if not targets:
        targets = [""]
    for to in targets:
        if not isinstance(to, str):
            raise Block("SendMessage refused: recipient must be a single agent name.")
        name = to.strip()
        if name.casefold() in BROADCAST:
            raise Block("SendMessage refused: broadcasts are not allowed; message one "
                        "agent by name.")
        if name == ROOT_ADDRESS:
            role = ROOT_ROLE
        else:
            role = state["names"].get(name)
        if role is None:
            raise Block(f"SendMessage refused: {name!r} is not an agent started in this run "
                        f"(the lead's address is 'main').")
        if role not in ROUTES[sender]:
            allowed = ", ".join(sorted(ROUTES[sender]))
            raise Block(f"SendMessage refused: {sender} may not message {role} ({name!r}). "
                        f"{sender} may message: {allowed}.")


def check_spawn(ev, state):
    ti = ev.get("tool_input") or {}
    role = ti.get("subagent_type") or "general-purpose"
    if role == ROOT_ROLE:
        raise Block("Agent refused: the run already has its lead; at most one per role.")
    existing = {a.get("role") for a in state["agents"].values()}
    pending = {p["role"] for p in live_pending(state)}
    if role in existing or role in pending:
        raise Block(f"Agent refused: the run already has a {role}; at most one instance of "
                    f"each role. Message the existing {role} instead (a finished agent "
                    f"wakes up when messaged).")
    state["pending"].append({
        "tool_use_id": ev.get("tool_use_id") or "",
        "role": role,
        "name": ti.get("name"),
        "ts": time.time(),
        "transcript": ev.get("transcript_path"),
        "launched": False,
        "bound": None,
    })


def check_files(ev, state):
    tool = ev.get("tool_name")
    ti = ev.get("tool_input") or {}
    proj = project_dir(ev, state)
    cwd = ev.get("cwd") or proj
    role = caller_role(ev, state)

    if tool == "Bash":
        hit = bash_folders(ev, proj)
        # A shell command may read or write; refuse only where the role has no access at all.
        bad = [f for f in hit
               if role not in FOLDERS[f]["read"] and role not in FOLDERS[f]["write"]]
        if bad:
            raise Block(f"Bash refused: role {role!r} has no access to {bad[0]}/ "
                        f"(RULES.md section 3).")
        return

    side = "write" if tool in WRITE_TOOLS else "read"
    if tool == "Glob":
        hit = glob_folders(ev, proj)
    elif tool == "Grep":
        hit = grep_folders(ev, proj)
    else:
        hit = set()
        for key in ("file_path", "notebook_path", "path"):
            v = ti.get(key)
            if isinstance(v, str) and v:
                f = folder_of(_abs(v, cwd), proj)
                if f:
                    hit.add(f)
    for f in sorted(hit):
        if role not in FOLDERS[f][side]:
            extra = ""
            if tool in ("Glob", "Grep"):
                extra = " Narrow the search path/pattern so it does not cover that folder."
            raise Block(f"{tool} refused: role {role!r} may not {side} {f}/** "
                        f"(RULES.md section 3).{extra}")


# ---------------------------------------------------------------- events

def on_pre(ev):
    tool = ev.get("tool_name") or ""
    governed = tool == "SendMessage" or tool in SPAWN_TOOLS or tool in WRITE_TOOLS \
        or tool in READ_TOOLS or tool == "Bash"
    if not governed:
        return
    with Locked() as lk:
        st = lk.state
        if tool == "SendMessage":
            check_send(ev, st)
        elif tool in SPAWN_TOOLS:
            check_spawn(ev, st)
            lk.save()
        else:
            check_files(ev, st)


def on_post(ev):
    if ev.get("tool_name") not in SPAWN_TOOLS:
        return
    with Locked() as lk:
        for p in lk.state["pending"]:
            if p["tool_use_id"] and p["tool_use_id"] == ev.get("tool_use_id"):
                p["launched"] = True
                if p.get("name"):
                    lk.state["names"][p["name"]] = p["role"]
        lk.save()


def on_subagent_start(ev):
    aid = ev.get("agent_id")
    role = ev.get("agent_type") or "general-purpose"
    if not aid:
        return
    with Locked() as lk:
        st = lk.state
        if aid in st["agents"]:
            return  # re-woken agent, not a new instance
        name = None
        for p in live_pending(st):
            if p["role"] == role:
                p["bound"] = aid
                name = p.get("name")
                break
        st["agents"][aid] = {"role": role, "name": name}
        if name:
            st["names"][name] = role
        else:
            log(f"SubagentStart {aid} ({role}) with no pending spawn to bind")
        lk.save()


def on_session_start(ev):
    if ev.get("agent_id"):
        return
    with Locked() as lk:
        if ev.get("source") in ("startup", "clear", None):
            lk.state = empty_state()
        lk.state["root_cwd"] = lk.state.get("root_cwd") or ev.get("cwd")
        lk.save()


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        ev = json.load(sys.stdin)
        if not isinstance(ev, dict):
            raise ValueError("hook input is not an object")
        event = event or ev.get("hook_event_name", "")
    except Exception as e:  # noqa: BLE001
        if event == "PreToolUse":
            sys.stderr.write(f"release-team hook could not parse its input ({e}); refusing.\n")
            sys.exit(2)
        sys.exit(0)

    try:
        if event == "PreToolUse":
            on_pre(ev)
        elif event == "PostToolUse":
            on_post(ev)
        elif event == "SubagentStart":
            on_subagent_start(ev)
        elif event == "SessionStart":
            on_session_start(ev)
    except Block as b:
        log(f"BLOCK {ev.get('agent_type')} {ev.get('tool_name')}: {b}")
        sys.stderr.write(str(b) + "\n")
        sys.exit(2)
    except Exception as e:  # noqa: BLE001
        log(f"ERROR {event} {ev.get('tool_name')}: {e!r}")
        if event == "PreToolUse":
            sys.stderr.write(f"release-team hook error ({e!r}); refusing to be safe.\n")
            sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
