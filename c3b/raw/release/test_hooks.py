"""Feeds sample events to hook.py (run against a scratch copy so real state is untouched)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="c3b-test-")
C3B = os.path.join(TMP, "ws", "c3b")
PROJ = os.path.join(TMP, "ws")
os.makedirs(C3B)
for d in ("src", "public", "docs"):
    os.makedirs(os.path.join(PROJ, d))
shutil.copy(os.path.join(HERE, "hook.py"), C3B)
HOOK = os.path.join(C3B, "hook.py")
TRANSCRIPT = os.path.join(TMP, "lead.jsonl")
open(TRANSCRIPT, "w").close()
ENV = dict(os.environ, CLAUDE_PROJECT_DIR=PROJ)

fails = 0
ids = {}  # role -> agent_id
n = [0]


def run(event, payload):
    payload = dict(payload, hook_event_name=event, session_id="s1",
                   transcript_path=TRANSCRIPT)
    payload.setdefault("cwd", PROJ)
    r = subprocess.run([sys.executable, "-I", HOOK, event], input=json.dumps(payload),
                       capture_output=True, text=True, env=ENV)
    return r.returncode, r.stderr.strip()


def pre(role, tool, ti, cwd=None):
    n[0] += 1
    ev = {"tool_name": tool, "tool_input": ti, "tool_use_id": f"tu{n[0]}",
          "agent_type": role, "permission_mode": "default"}
    if role != "lead":
        ev["agent_id"] = ids.get(role, "unknown-" + role)
    if cwd:
        ev["cwd"] = cwd
    return run("PreToolUse", ev), ev["tool_use_id"]


def expect(label, want_allow, role, tool, ti, cwd=None):
    global fails
    (code, err), _ = pre(role, tool, ti, cwd)
    ok = (code == 0) if want_allow else (code == 2)
    if not ok:
        fails += 1
    print(f"{'ok  ' if ok else 'FAIL'} {'allow' if want_allow else 'block'} {label}"
          f" -> exit {code} {err[:110]}")


def spawn(role, name, start=True, post=True):
    (code, err), tuid = pre("lead", "Agent", {"subagent_type": role, "name": name,
                                              "prompt": "x", "run_in_background": True})
    if code != 0:
        print(f"     ({role} {name}: {err[:140]})")
        return code, err, tuid
    if post:
        run("PostToolUse", {"tool_name": "Agent", "tool_use_id": tuid, "agent_type": "lead",
                            "tool_input": {"subagent_type": role, "name": name},
                            "tool_response": {"status": "async_launched"}})
    if start:
        ids[role] = f"a-{name}"
        run("SubagentStart", {"agent_id": ids[role], "agent_type": role})
    return code, err, tuid


def check(label, cond):
    global fails
    fails += 0 if cond else 1
    print(f"{'ok  ' if cond else 'FAIL'} {label}")


run("SessionStart", {"agent_type": "lead", "source": "startup"})

print("== rule 2: spawns")
check("spawn coder", spawn("coder", "cody")[0] == 0)
check("spawn reviewer", spawn("reviewer", "rev")[0] == 0)
check("second coder refused", spawn("coder", "cody2")[0] == 2)
check("spawn lead refused", spawn("lead", "lead2")[0] == 2)
# Re-waking: same agent_id starts again; must not count or rebind.
run("SubagentStart", {"agent_id": ids["coder"], "agent_type": "coder"})
check("second reviewer still refused after rewake", spawn("reviewer", "rev2")[0] == 2)

# Publisher spawn refused by some other hook: transcript shows an error tool_result.
code, _, tuid = spawn("publisher", "pubA", start=False, post=False)
check("publisher spawn allowed by us", code == 0)
check("pending publisher blocks a parallel second one",
      spawn("publisher", "pubB", start=False, post=False)[0] == 2)
with open(TRANSCRIPT, "a") as f:
    f.write(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tuid, "is_error": True,
         "content": "blocked by hook"}]}}) + "\n")
check("retry after the other hook refused it", spawn("publisher", "pub")[0] == 0)
st = json.load(open(os.path.join(C3B, "state.json")))
check("publisher bound to the retried name 'pub'",
      st["agents"].get("a-pub", {}).get("name") == "pub")
check("third publisher refused", spawn("publisher", "pub3")[0] == 2)

print("== rule 1: messages")
M = "SendMessage"
expect("lead -> coder", True, "lead", M, {"to": "cody", "message": "hi"})
expect("lead -> publisher", True, "lead", M, {"to": "pub", "message": "hi"})
expect("lead -> main (itself)", False, "lead", M, {"to": "main", "message": "hi"})
expect("coder -> main", True, "coder", M, {"to": "main", "message": "hi"})
expect("coder -> reviewer", True, "coder", M, {"to": "rev", "recipient": "rev"})
expect("coder -> publisher", False, "coder", M, {"to": "pub", "message": "hi"})
expect("reviewer -> coder", True, "reviewer", M, {"recipient": "cody", "content": "hi"})
expect("reviewer -> publisher", False, "reviewer", M, {"to": "pub"})
expect("publisher -> main", True, "publisher", M, {"to": "main"})
expect("publisher -> coder", False, "publisher", M, {"to": "cody"})
expect("to 'lead' (not an address)", False, "coder", M, {"to": "lead"})
expect("unknown name", False, "lead", M, {"to": "ghost"})
expect("refused spawn name pubA", False, "lead", M, {"to": "pubA"})
expect("broadcast *", False, "lead", M, {"to": "*"})
expect("broadcast all", False, "lead", M, {"to": "all"})
expect("empty to", False, "lead", M, {"to": ""})
expect("missing to", False, "lead", M, {"message": "x"})
expect("to/recipient disagree", False, "coder", M, {"to": "main", "recipient": "pub"})
expect("unknown sender role", False, "general-purpose", M, {"to": "main"})

print("== rule 3: folders")
S, P = os.path.join(PROJ, "src", "a.py"), os.path.join(PROJ, "public", "i.html")
expect("coder write src", True, "coder", "Write", {"file_path": S, "content": ""})
expect("coder edit src", True, "coder", "Edit", {"file_path": S})
expect("reviewer read src", True, "reviewer", "Read", {"file_path": S})
expect("reviewer edit src", False, "reviewer", "Edit", {"file_path": S})
expect("reviewer multiedit src", False, "reviewer", "MultiEdit", {"file_path": S, "edits": []})
expect("publisher read src", False, "publisher", "Read", {"file_path": S})
expect("publisher write public", True, "publisher", "Write", {"file_path": P})
expect("publisher read public", False, "publisher", "Read", {"file_path": P})
expect("lead read public", True, "lead", "Read", {"file_path": P})
expect("coder read public", False, "coder", "Read", {"file_path": P})
expect("coder notebook public", False, "coder", "NotebookEdit",
       {"notebook_path": os.path.join(PROJ, "public", "n.ipynb")})
expect("dotdot into public", False, "coder", "Read",
       {"file_path": os.path.join(PROJ, "src", "..", "public", "x")})
expect("case variant PUBLIC", False, "coder", "Read",
       {"file_path": os.path.join(PROJ, "PUBLIC", "x")})
expect("relative path public/x", False, "coder", "Read", {"file_path": "public/x"})
expect("folder itself", False, "reviewer", "Glob", {"pattern": "*", "path":
                                                     os.path.join(PROJ, "public")})
expect("publicity/ is not public/", True, "coder", "Read",
       {"file_path": os.path.join(PROJ, "publicity", "x")})
expect("nested docs/public not covered", True, "coder", "Read",
       {"file_path": os.path.join(PROJ, "docs", "public", "x")})
expect("outside project", True, "publisher", "Read", {"file_path": "/etc/hosts"})
expect("docs read by anyone", True, "publisher", "Read",
       {"file_path": os.path.join(PROJ, "docs", "x.md")})
expect("glob src/** by reviewer", True, "reviewer", "Glob", {"pattern": "src/**/*.py"})
expect("glob **/*.py at root by reviewer", False, "reviewer", "Glob", {"pattern": "**/*.py"})
expect("glob docs/*.md by reviewer", True, "reviewer", "Glob", {"pattern": "docs/*.md"})
expect("glob p*/x by coder", False, "coder", "Glob", {"pattern": "p*/x"})
expect("glob absolute public", False, "coder", "Glob",
       {"pattern": os.path.join(PROJ, "public", "*")})
expect("glob *.md at root by publisher", True, "publisher", "Glob", {"pattern": "*.md"})
expect("grep src by reviewer", True, "reviewer", "Grep",
       {"pattern": "TODO", "path": os.path.join(PROJ, "src")})
expect("grep whole repo by reviewer", False, "reviewer", "Grep", {"pattern": "TODO"})
expect("grep whole repo by lead", True, "lead", "Grep", {"pattern": "TODO"})
expect("grep docs by publisher", True, "publisher", "Grep",
       {"pattern": "x", "path": os.path.join(PROJ, "docs")})
expect("bash cat public by coder", False, "coder", "Bash", {"command": "cat public/i.html"})
expect("bash cp into src by publisher", False, "publisher", "Bash",
       {"command": "cp x $CLAUDE_PROJECT_DIR/src/y"})
expect("bash ls by coder", True, "coder", "Bash", {"command": "ls -la && git status"})
expect("bash commit msg mentioning public", True, "coder", "Bash",
       {"command": 'git commit -m "make public api"'})
expect("bash cat src by reviewer", True, "reviewer", "Bash", {"command": "cat src/a.py"})
expect("unrelated tool", True, "reviewer", "WebSearch", {"query": "x"})

print("== robustness")
r = subprocess.run([sys.executable, "-I", HOOK, "PreToolUse"], input="not json",
                   capture_output=True, text=True, env=ENV)
check("garbage input refused (exit 2)", r.returncode == 2)

# Concurrency: 8 simultaneous spawns of a fresh role -> exactly one allowed.
results = []
def worker(i):
    (code, _), _ = pre("lead", "Agent", {"subagent_type": "tester", "name": f"t{i}"})
    results.append(code)
ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
check(f"concurrent spawns: exactly one allowed {sorted(results)}", results.count(0) == 1)

shutil.rmtree(TMP)
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
