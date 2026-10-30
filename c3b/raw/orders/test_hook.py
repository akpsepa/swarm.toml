#!/usr/bin/env python3
"""Feeds sample events to hook.py (a scratch copy, so real state is untouched)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")
TMP = tempfile.mkdtemp(prefix="c3b-test-")
C3B = os.path.join(TMP, "c3b")
PROJ = os.path.join(TMP, "proj")
os.makedirs(C3B)
os.makedirs(os.path.join(PROJ, "data", "orders", "2026"))
os.makedirs(os.path.join(PROJ, "data", "customers"))
os.makedirs(os.path.join(PROJ, "reports"))
HOOK = os.path.join(C3B, "hook.py")
shutil.copy(SRC, HOOK)
TRANSCRIPT = os.path.join(PROJ, "sess.jsonl")
open(TRANSCRIPT, "w").close()
ENV = dict(os.environ, CLAUDE_PROJECT_DIR=PROJ)

fails = 0
n = 0


def run(event, ev):
    ev = dict(ev, hook_event_name=event)
    ev.setdefault("session_id", "S1")
    ev.setdefault("cwd", PROJ)
    ev.setdefault("transcript_path", TRANSCRIPT)
    p = subprocess.run([sys.executable, HOOK, event], input=json.dumps(ev),
                       capture_output=True, text=True, env=ENV)
    return p.returncode, p.stderr.strip()


def expect(label, want, event, ev):
    global fails, n
    n += 1
    code, err = run(event, ev)
    got = "block" if code == 2 else "allow" if code == 0 else "error%d" % code
    ok = got == want
    if not ok:
        fails += 1
    print("%s %-62s %s%s" % ("ok  " if ok else "FAIL", label, got, (" | " + err) if err else ""))


tid = [0]


def pre(tool, ti, agent=None, role="lead"):
    tid[0] += 1
    ev = {"tool_name": tool, "tool_input": ti, "agent_type": role, "tool_use_id": "tu%d" % tid[0],
          "permission_mode": "default"}
    if agent:
        ev["agent_id"] = agent
    return ev


def spawn(role, name):
    return pre("Agent", {"subagent_type": role, "name": name, "description": "d", "prompt": "p",
                         "run_in_background": True})


def start(aid, role):
    return {"agent_id": aid, "agent_type": role}


def msg(to, agent=None, role="lead", **kw):
    ti = {"to": to, "recipient": to, "message": "hi", "content": "hi", "summary": "s"}
    ti.update(kw)
    return pre("SendMessage", ti, agent, role)


def path(*p):
    return os.path.join(PROJ, *p)


A, B, S = "aaa1", "bbb2", "sss3"

expect("session start", "allow", "SessionStart", {"source": "startup", "agent_type": "lead"})

# ---- rule 2: spawns
expect("lead spawns analyst-a", "allow", "PreToolUse", spawn("analyst-a", "ana"))
expect("second analyst-a while first pending", "block", "PreToolUse", spawn("analyst-a", "ana2"))
expect("SubagentStart analyst-a", "allow", "SubagentStart", start(A, "analyst-a"))
expect("second analyst-a after start", "block", "PreToolUse", spawn("analyst-a", "ana3"))
expect("spawn a lead", "block", "PreToolUse", spawn("lead", "lead2"))
expect("lead spawns analyst-b", "allow", "PreToolUse", spawn("analyst-b", "anb"))
expect("SubagentStart analyst-b", "allow", "SubagentStart", start(B, "analyst-b"))
expect("re-wake analyst-a (same id)", "allow", "SubagentStart", start(A, "analyst-a"))
expect("analyst-a still works after re-wake", "allow", "PreToolUse",
       pre("Read", {"file_path": path("reports", "x.md")}, A, "analyst-a"))

# spawn refused by another hook: transcript records an error result -> not counted
sp = spawn("summarizer", "sum0")
expect("lead spawns summarizer (blocked elsewhere)", "allow", "PreToolUse", sp)
with open(TRANSCRIPT, "a") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": sp["tool_use_id"], "is_error": True,
         "content": "PreToolUse:Agent hook error: refused"}]}}) + "\n")
sp2 = spawn("summarizer", "sum")
expect("retry summarizer after failed spawn", "allow", "PreToolUse", sp2)
expect("PostToolUse summarizer (agentId)", "allow", "PostToolUse",
       dict(sp2, tool_response={"status": "async_launched", "agentId": S}))
expect("SubagentStart summarizer", "allow", "SubagentStart", start(S, "summarizer"))
expect("second summarizer", "block", "PreToolUse", spawn("summarizer", "sum2"))

# an extra instance that somehow starts anyway is frozen
expect("SubagentStart rogue extra analyst-b", "allow", "SubagentStart", start("rogue", "analyst-b"))
expect("rogue extra analyst-b reads", "block", "PreToolUse",
       pre("Read", {"file_path": path("reports", "x.md")}, "rogue", "analyst-b"))

# ---- rule 1: routes
expect("lead -> ana", "allow", "PreToolUse", msg("ana"))
expect("lead -> anb", "allow", "PreToolUse", msg("anb"))
expect("lead -> sum", "allow", "PreToolUse", msg("sum"))
expect("lead -> main (self)", "block", "PreToolUse", msg("main"))
expect("ana -> main", "allow", "PreToolUse", msg("main", A, "analyst-a"))
expect("ana -> anb", "block", "PreToolUse", msg("anb", A, "analyst-a"))
expect("ana -> sum", "block", "PreToolUse", msg("sum", A, "analyst-a"))
expect("sum -> ana", "block", "PreToolUse", msg("ana", S, "summarizer"))
expect("sum -> main", "allow", "PreToolUse", msg("main", S, "summarizer"))
expect("ana -> 'lead' (not an address)", "block", "PreToolUse", msg("lead", A, "analyst-a"))
expect("lead -> unknown name", "block", "PreToolUse", msg("bob"))
expect("lead -> name of failed spawn", "block", "PreToolUse", msg("sum0"))
expect("lead -> '*'", "block", "PreToolUse", msg("*"))
expect("lead -> 'all'", "block", "PreToolUse", msg("all"))
expect("lead -> 'ALL'", "block", "PreToolUse", msg("ALL"))
expect("lead -> ''", "block", "PreToolUse", msg(""))
expect("lead -> missing to", "block", "PreToolUse",
       pre("SendMessage", {"message": "hi", "summary": "s"}))
expect("to/recipient disagree", "block", "PreToolUse", msg("ana", recipient="anb"))
expect("only recipient given", "allow", "PreToolUse",
       pre("SendMessage", {"recipient": "anb", "content": "x"}))

# ---- rule 3: holdings
expect("ana reads order file", "allow", "PreToolUse",
       pre("Read", {"file_path": path("data", "orders", "2026", "q1.csv")}, A, "analyst-a"))
expect("anb greps orders", "allow", "PreToolUse",
       pre("Grep", {"pattern": "x", "path": path("data", "orders")}, B, "analyst-b"))
expect("ana -> main (lead combines)", "allow", "PreToolUse", msg("main", A, "analyst-a"))
expect("anb -> main", "allow", "PreToolUse", msg("main", B, "analyst-b"))
expect("lead reads orders", "allow", "PreToolUse",
       pre("Read", {"file_path": path("data", "orders", "q.csv")}))
expect("lead -> sum (carries nothing)", "allow", "PreToolUse", msg("sum"))
expect("sum reads orders afterwards", "allow", "PreToolUse",
       pre("Read", {"file_path": path("data", "orders", "q.csv")}, S, "summarizer"))
expect("sum writes report", "allow", "PreToolUse",
       pre("Write", {"file_path": path("reports", "r.md"), "content": "x"}, S, "summarizer"))

# simulate an agent already holding someone else's data (unreachable via routes alone)
st_path = os.path.join(C3B, "state.json")
st = json.load(open(st_path))
st["holds"]["anb"] = ["ana"]
json.dump(st, open(st_path, "w"))
expect("anb holding ana's data reads orders", "block", "PreToolUse",
       pre("Edit", {"file_path": path("data", "orders", "a.csv"), "old_string": "a",
                    "new_string": "b"}, B, "analyst-b"))
expect("anb non-order read still fine", "allow", "PreToolUse",
       pre("Read", {"file_path": path("data", "customers", "c.csv")}, B, "analyst-b"))
st = json.load(open(st_path))
expect("refused agent gained nothing", "allow" if st["holds"]["anb"] == ["ana"] else "block",
       "SessionStart", {"source": "resume", "agent_type": "lead"})

# path detection
st = json.load(open(st_path))
st["holds"]["sum"] = ["ana"]  # make sum sensitive to any order access
json.dump(st, open(st_path, "w"))
s = lambda tool, ti: pre(tool, ti, S, "summarizer")
expect("dotdot into orders", "block", "PreToolUse",
       s("Read", {"file_path": path("reports", "..", "data", "orders", "x")}))
expect("case variant Data/Orders", "block", "PreToolUse",
       s("Read", {"file_path": path("Data", "Orders", "x")}))
os.symlink(path("data", "orders"), path("reports", "link"))
expect("symlink into orders", "block", "PreToolUse",
       s("Read", {"file_path": path("reports", "link", "x.csv")}))
expect("Glob ** from root", "block", "PreToolUse", s("Glob", {"pattern": "**/*.csv", "path": PROJ}))
expect("Glob data/*/x", "block", "PreToolUse", s("Glob", {"pattern": "data/*/*.csv"}))
expect("Glob data/orders/*", "block", "PreToolUse", s("Glob", {"pattern": "data/orders/*"}))
expect("Glob reports/**", "allow", "PreToolUse", s("Glob", {"pattern": "reports/**/*.md"}))
expect("Glob data/customers/*", "allow", "PreToolUse", s("Glob", {"pattern": "data/customers/*"}))
expect("Glob data/c*/*", "allow", "PreToolUse", s("Glob", {"pattern": "data/c*/*"}))
expect("Glob data/* (lists only the orders dir name)", "allow", "PreToolUse",
       s("Glob", {"pattern": "data/*"}))
expect("Grep whole project", "block", "PreToolUse", s("Grep", {"pattern": "x", "path": PROJ}))
expect("Grep data/customers", "allow", "PreToolUse",
       s("Grep", {"pattern": "x", "path": path("data", "customers")}))
expect("NotebookEdit in orders", "block", "PreToolUse",
       s("NotebookEdit", {"notebook_path": path("data", "orders", "n.ipynb")}))
expect("MultiEdit in orders", "block", "PreToolUse",
       s("MultiEdit", {"file_path": path("data", "orders", "m.csv"), "edits": []}))
expect("Bash cat data//orders", "block", "PreToolUse", s("Bash", {"command": "cat data//orders/x"}))
expect("Bash ls reports", "allow", "PreToolUse", s("Bash", {"command": "ls reports"}))
expect("data/orders-archive is not orders", "allow", "PreToolUse",
       s("Read", {"file_path": path("data", "orders-archive", "x")}))
expect("other tool (TodoWrite)", "allow", "PreToolUse", s("TodoWrite", {"todos": []}))

# fail closed on garbage for governed tools, open for others
code = subprocess.run([sys.executable, HOOK, "PreToolUse"], input="{not json",
                      capture_output=True, text=True, env=ENV).returncode
n += 1
print(("ok  " if code == 0 else "FAIL") + " bad JSON on unknown tool -> allow (exit %d)" % code)
fails += code != 0

# ---- concurrency: many parallel spawns of a fresh role, exactly one allowed
TMP2 = os.path.join(TMP, "c3b2")
os.makedirs(TMP2)
HOOK2 = os.path.join(TMP2, "hook.py")
shutil.copy(SRC, HOOK2)
codes = []


def worker(i):
    ev = dict(spawn("analyst-a", "par%d" % i), hook_event_name="PreToolUse", session_id="S2",
              cwd=PROJ, transcript_path=TRANSCRIPT)
    p = subprocess.run([sys.executable, HOOK2, "PreToolUse"], input=json.dumps(ev),
                       capture_output=True, text=True, env=ENV)
    codes.append(p.returncode)


ts = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
[t.start() for t in ts]
[t.join() for t in ts]
n += 1
ok = sorted(codes) == [0] + [2] * 11
fails += not ok
print(("ok  " if ok else "FAIL") + " 12 parallel spawns -> exactly one allowed: %s" % sorted(codes))

# ---- new run resets state
expect("new session startup resets", "allow", "SessionStart",
       {"source": "startup", "agent_type": "lead", "session_id": "S9"})
expect("analyst-a spawn allowed in new run", "allow", "PreToolUse",
       dict(spawn("analyst-a", "ana"), session_id="S9"))

print("\n%d/%d passed" % (n - fails, n))
shutil.rmtree(TMP)
sys.exit(1 if fails else 0)
