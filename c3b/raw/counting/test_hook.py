"""Feeds sample events to hook.py and checks its decisions. Run: python3 test_hook.py"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HOOK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hook.py")
FAILS = []
COUNT = [0]


class Run:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="c3b-test-")
        self.proj = os.path.join(self.tmp, "proj")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(os.path.join(self.proj, "out"))
        os.makedirs(os.path.join(self.proj, "src"))
        os.makedirs(self.state)
        self.root_tp = os.path.join(self.tmp, "sess.jsonl")
        open(self.root_tp, "w").close()
        os.makedirs(os.path.join(self.tmp, "sess", "subagents"))
        self.n = 0
        self.event("SessionStart", None, source="startup")

    def close(self):
        shutil.rmtree(self.tmp)

    def tp(self, aid):
        if aid is None:
            return self.root_tp
        return os.path.join(self.tmp, "sess", "subagents", "agent-%s.jsonl" % aid)

    def event(self, name, aid, role=None, **kw):
        ev = {"hook_event_name": name, "session_id": "S1", "cwd": self.proj,
              "transcript_path": self.root_tp, "agent_type": role or "lead"}
        if aid:
            ev["agent_id"] = aid
        ev.update(kw)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.proj, C3B_STATE_DIR=self.state)
        p = subprocess.run([sys.executable, "-I", HOOK, name], input=json.dumps(ev),
                           capture_output=True, text=True, env=env)
        return p.returncode, p.stderr.strip()

    def tool(self, aid, role, tool, **ti):
        self.n += 1
        tuid = "toolu_%d" % self.n
        rc, err = self.event("PreToolUse", aid, role, tool_name=tool, tool_input=ti,
                             tool_use_id=tuid, permission_mode="default")
        return rc, err, tuid

    def spawn(self, aid, role, new_role, name, new_aid=None, post=True):
        rc, err, tuid = self.tool(aid, role, "Agent", subagent_type=new_role, name=name,
                                  description="d", prompt="p", run_in_background=True)
        if rc == 0 and new_aid:
            self.event("SubagentStart", new_aid, new_role)
            if post:
                self.event("PostToolUse", aid, role, tool_name="Agent", tool_use_id=tuid,
                           tool_input={"subagent_type": new_role, "name": name},
                           tool_response={"status": "async_launched"})
        return rc, err, tuid

    def usage(self, aid, tokens, mid=None, dup=False):
        self.n += 1
        line = {"type": "assistant", "message": {"id": mid or "msg_%d" % self.n, "usage": {
            "input_tokens": tokens, "output_tokens": 0, "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 999999}}}
        with open(self.tp(aid), "a") as f:
            f.write(json.dumps(line) + "\n")
            if dup:
                f.write(json.dumps(line) + "\n")

    def tool_error(self, aid, tuid):
        line = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tuid, "is_error": True, "content": "denied"}]}}
        with open(self.tp(aid), "a") as f:
            f.write(json.dumps(line) + "\n")


def expect(label, result, allowed):
    COUNT[0] += 1
    rc, err = result[0], result[1]
    ok = (rc == 0) if allowed else (rc == 2)
    if not ok:
        FAILS.append("%s: expected %s, got rc=%s %s" % (label, "allow" if allowed else "refuse", rc, err))


def team(r):
    """lead -> planner P, checker C; planner -> workers W1, W2."""
    expect("spawn planner", r.spawn(None, "lead", "planner", "plan", "aP"), True)
    expect("spawn checker", r.spawn(None, "lead", "checker", "chk", "aC"), True)
    expect("spawn worker1", r.spawn("aP", "planner", "worker", "w1", "aW1"), True)
    expect("spawn worker2", r.spawn("aP", "planner", "worker", "w2", "aW2"), True)


def test_counts():
    r = Run()
    try:
        team(r)
        expect("third worker", r.spawn("aP", "planner", "worker", "w3", "aW3"), False)
        expect("second planner", r.spawn(None, "lead", "planner", "plan2"), False)
        expect("second checker", r.spawn(None, "lead", "checker", "chk2"), False)
        expect("a lead", r.spawn(None, "lead", "lead", "lead2"), False)
        expect("unknown role", r.spawn(None, "lead", "general-purpose", "gp"), False)
        # re-wake of a finished worker is not a new instance
        r.event("SubagentStart", "aW1", "worker")
        expect("worker msg after rewake", r.tool("aW1", "worker", "SendMessage", to="plan",
                                                 message="hi", summary="s")[:2], True)
        expect("still no third worker", r.spawn("aP", "planner", "worker", "w4"), False)
    finally:
        r.close()


def test_failed_spawn_released():
    r = Run()
    try:
        expect("spawn planner", r.spawn(None, "lead", "planner", "plan", "aP"), True)
        rc, err, tuid = r.spawn("aP", "planner", "worker", "w1")  # refused by another hook
        expect("worker1 pre", (rc, err), True)
        r.tool_error("aP", tuid)
        expect("worker after failed spawn", r.spawn("aP", "planner", "worker", "w1b", "aW1"), True)
        expect("worker 2", r.spawn("aP", "planner", "worker", "w2", "aW2"), True)
        expect("worker 3", r.spawn("aP", "planner", "worker", "w3"), False)
        # pending (unconfirmed) spawns count, so two concurrent in-flight spawns can't exceed
        r2 = Run()
        try:
            r2.spawn(None, "lead", "planner", "plan", "aP")
            expect("inflight w1", r2.spawn("aP", "planner", "worker", "w1")[:2], True)
            expect("inflight w2", r2.spawn("aP", "planner", "worker", "w2")[:2], True)
            expect("inflight w3", r2.spawn("aP", "planner", "worker", "w3")[:2], False)
            # FIFO binding: next SubagentStart binds to w1, then w2
            r2.event("SubagentStart", "x1", "worker")
            r2.event("SubagentStart", "x2", "worker")
            expect("msg w2 from planner", r2.tool("aP", "planner", "SendMessage", to="w2",
                                                  message="m")[:2], True)
        finally:
            r2.close()
    finally:
        r.close()


def test_messages():
    r = Run()
    try:
        team(r)
        m = lambda aid, role, to, **kw: r.tool(aid, role, "SendMessage", to=to, recipient=to,
                                               message="m", content="m", summary="s", **kw)[:2]
        expect("lead->planner", m(None, "lead", "plan"), True)
        expect("lead->checker", m(None, "lead", "chk"), True)
        expect("lead->worker", m(None, "lead", "w1"), False)
        expect("lead->main", m(None, "lead", "main"), False)
        expect("planner->main", m("aP", "planner", "main"), True)
        expect("planner->worker", m("aP", "planner", "w2"), True)
        expect("planner->checker", m("aP", "planner", "chk"), True)
        expect("worker->planner", m("aW1", "worker", "plan"), True)
        expect("worker->main", m("aW1", "worker", "main"), False)
        expect("worker->worker", m("aW1", "worker", "w2"), False)
        expect("worker->checker", m("aW1", "worker", "chk"), False)
        expect("checker->main", m("aC", "checker", "main"), True)
        expect("checker->planner", m("aC", "checker", "plan"), True)
        expect("checker->worker", m("aC", "checker", "w1"), False)
        expect("broadcast *", m("aP", "planner", "*"), False)
        expect("broadcast all", m("aP", "planner", "all"), False)
        expect("broadcast empty", m("aP", "planner", ""), False)
        expect("unknown name", m("aP", "planner", "nobody"), False)
        expect("role as name", m("aW1", "worker", "planner"), False)
        expect("lead as address", m("aC", "checker", "lead"), False)
        expect("to/recipient mismatch", r.tool("aW1", "worker", "SendMessage", to="plan",
                                               recipient="chk", message="m")[:2], False)
        expect("only recipient", r.tool("aW1", "worker", "SendMessage", recipient="plan",
                                        content="m")[:2], True)
        expect("no target", r.tool("aW1", "worker", "SendMessage", message="m")[:2], False)
    finally:
        r.close()


def test_out_folder():
    r = Run()
    try:
        team(r)
        P = r.proj
        out = os.path.join(P, "out", "a.txt")
        src = os.path.join(P, "src", "b.txt")
        t = lambda aid, role, tool, **ti: r.tool(aid, role, tool, **ti)[:2]
        for aid, role in ((None, "lead"), ("aP", "planner")):
            expect(role + " write out", t(aid, role, "Write", file_path=out, content="x"), True)
            expect(role + " read out", t(aid, role, "Read", file_path=out), True)
            expect(role + " bash out", t(aid, role, "Bash", command="echo 1 > out/a.txt"), True)
        expect("checker read out", t("aC", "checker", "Read", file_path=out), True)
        expect("checker glob out", t("aC", "checker", "Glob", pattern="out/*"), True)
        expect("checker grep out", t("aC", "checker", "Grep", pattern="x", path=P + "/out"), True)
        for tool, extra in (("Write", {"content": "x"}), ("Edit", {"old_string": "a", "new_string": "b"}),
                            ("MultiEdit", {"edits": []})):
            expect("checker %s out" % tool, t("aC", "checker", tool, file_path=out, **extra), False)
            expect("worker %s out" % tool, t("aW1", "worker", tool, file_path=out, **extra), False)
            expect("worker %s src" % tool, t("aW1", "worker", tool, file_path=src, **extra), True)
        expect("checker notebook out", t("aC", "checker", "NotebookEdit",
                                         notebook_path=P + "/out/n.ipynb", new_source=""), False)
        expect("worker read out", t("aW1", "worker", "Read", file_path=out), False)
        expect("worker read out dotdot", t("aW1", "worker", "Read",
                                           file_path=P + "/src/../out/a.txt"), False)
        expect("worker read OUT case", t("aW1", "worker", "Read", file_path=P + "/OUT/a.txt"),
               sys.platform not in ("darwin", "win32"))
        expect("worker read src", t("aW1", "worker", "Read", file_path=src), True)
        expect("worker read outer file", t("aW1", "worker", "Read", file_path=P + "/output.txt"), True)
        expect("worker read src/out", t("aW1", "worker", "Read", file_path=P + "/src/out/x"), True)
        expect("worker glob out/*", t("aW1", "worker", "Glob", pattern="out/*"), False)
        expect("worker glob path out", t("aW1", "worker", "Glob", pattern="*", path=P + "/out"), False)
        expect("worker glob **", t("aW1", "worker", "Glob", pattern="**/*.txt"), False)
        expect("worker glob {src,out}", t("aW1", "worker", "Glob", pattern="{src,out}/*"), False)
        expect("worker glob src/**", t("aW1", "worker", "Glob", pattern="src/**/*.txt"), True)
        expect("worker glob *.txt", t("aW1", "worker", "Glob", pattern="*.txt"), True)
        expect("worker glob abs out", t("aW1", "worker", "Glob", pattern=P + "/o*/*"), False)
        expect("worker grep root", t("aW1", "worker", "Grep", pattern="x"), False)
        expect("worker grep root glob", t("aW1", "worker", "Grep", pattern="x", glob="*.txt"), False)
        expect("worker grep src", t("aW1", "worker", "Grep", pattern="x", path=P + "/src"), True)
        expect("worker grep root src glob", t("aW1", "worker", "Grep", pattern="x",
                                              glob="src/**/*.py"), True)
        expect("worker grep out file", t("aW1", "worker", "Grep", pattern="x", path=out), False)
        # bash (best effort)
        expect("worker bash cat out", t("aW1", "worker", "Bash", command="cat out/a.txt"), False)
        expect("worker bash abs", t("aW1", "worker", "Bash", command="wc -l %s/out/a.txt" % P), False)
        expect("worker bash sh -c", t("aW1", "worker", "Bash", command="sh -c 'cat out/a.txt'"), False)
        expect("worker bash cd out", t("aW1", "worker", "Bash", command="cd out && ls"), False)
        expect("worker bash glob", t("aW1", "worker", "Bash", command="grep -r x o*"), False)
        expect("worker bash src", t("aW1", "worker", "Bash", command="wc -l src/b.txt 2>/dev/null"), True)
        expect("worker bash timeout", t("aW1", "worker", "Bash",
                                        command="timeout 5 python3 count.py --out=res.txt"), True)
        expect("checker bash read", t("aC", "checker", "Bash",
                                      command="cat out/a.txt | wc -l 2>&1"), True)
        expect("checker bash write", t("aC", "checker", "Bash", command="echo x > out/a.txt"), False)
        expect("checker bash rm", t("aC", "checker", "Bash", command="rm out/a.txt"), False)
        expect("checker bash find -delete", t("aC", "checker", "Bash",
                                              command="find out -name '*.txt' -delete"), False)
        expect("checker bash tee", t("aC", "checker", "Bash", command="cat src/b | tee out/b"), False)
    finally:
        r.close()


def test_budgets():
    r = Run()
    try:
        team(r)
        t = lambda aid, role: r.tool(aid, role, "Read", file_path=r.proj + "/src/b.txt")[:2]
        # worker: 150k budget; duplicates of one message counted once; cache reads ignored
        r.usage("aW1", 100_000, mid="m1", dup=True)
        r.usage("aW1", 50_000)
        expect("worker at budget", t("aW1", "worker"), True)
        r.usage("aW1", 1)
        expect("worker over budget", t("aW1", "worker"), False)
        expect("worker over budget msg", r.tool("aW1", "worker", "SendMessage", to="plan",
                                                message="m")[:2], False)
        expect("other worker unaffected", t("aW2", "worker"), True)
        # planner: 400k, has reserved 2*150k for its workers -> may use 100k
        r.usage("aP", 100_000)
        expect("planner at limit", t("aP", "planner"), True)
        r.usage("aP", 1)
        expect("planner over (budget - reserved)", t("aP", "planner"), False)
        # lead: 1M, reserved 400k + 100k
        r.usage(None, 500_000)
        expect("lead at limit", t(None, "lead"), True)
        r.usage(None, 1)
        expect("lead over", t(None, "lead"), False)
    finally:
        r.close()

    r = Run()
    try:
        expect("spawn planner", r.spawn(None, "lead", "planner", "plan", "aP"), True)
        expect("spawn worker1", r.spawn("aP", "planner", "worker", "w1", "aW1"), True)
        r.usage("aP", 100_000)  # left = 400k - 100k - 150k = 150k
        expect("worker2 exactly fits", r.spawn("aP", "planner", "worker", "w2", "aW2"), True)
    finally:
        r.close()

    r = Run()
    try:
        expect("spawn planner", r.spawn(None, "lead", "planner", "plan", "aP"), True)
        expect("spawn worker1", r.spawn("aP", "planner", "worker", "w1", "aW1"), True)
        r.usage("aP", 100_001)  # left = 149,999
        expect("worker2 too big", r.spawn("aP", "planner", "worker", "w2"), False)
        # lead with 550k used can't reserve a 100k checker? left = 1M - 550k - 400k = 50k
        r.usage(None, 550_000)
        expect("checker too big", r.spawn(None, "lead", "checker", "chk"), False)
    finally:
        r.close()


def test_robustness():
    r = Run()
    try:
        env = dict(os.environ, CLAUDE_PROJECT_DIR=r.proj, C3B_STATE_DIR=r.state)
        p = subprocess.run([sys.executable, "-I", HOOK, "PreToolUse"], input="not json",
                           capture_output=True, text=True, env=env)
        expect("garbage input refused", (p.returncode, p.stderr), False)
        expect("unrelated tool ok", r.tool(None, "lead", "TodoWrite", todos=[])[:2], True)
        # resume does not reset state
        r.spawn(None, "lead", "planner", "plan", "aP")
        r.event("SessionStart", None, source="resume")
        expect("planner still counted", r.spawn(None, "lead", "planner", "p2"), False)
    finally:
        r.close()


def test_concurrent_spawns():
    r = Run()
    try:
        r.spawn(None, "lead", "planner", "plan", "aP")
        env = dict(os.environ, CLAUDE_PROJECT_DIR=r.proj, C3B_STATE_DIR=r.state)
        procs = []
        for i in range(8):
            ev = {"hook_event_name": "PreToolUse", "session_id": "S1", "cwd": r.proj,
                  "transcript_path": r.root_tp, "agent_type": "planner", "agent_id": "aP",
                  "tool_name": "Agent", "tool_use_id": "par_%d" % i,
                  "tool_input": {"subagent_type": "worker", "name": "pw%d" % i}}
            p = subprocess.Popen([sys.executable, "-I", HOOK, "PreToolUse"], env=env,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
            p.stdin.write(json.dumps(ev))
            p.stdin.close()
            procs.append(p)
        allowed = sum(1 for p in procs if p.wait() == 0)
        expect("exactly 2 of 8 concurrent worker spawns", (0 if allowed == 2 else 2, str(allowed)), True)
    finally:
        r.close()


if __name__ == "__main__":
    for fn in (test_counts, test_failed_spawn_released, test_messages, test_out_folder,
               test_budgets, test_robustness, test_concurrent_spawns):
        fn()
    print("%d checks, %d failures" % (COUNT[0], len(FAILS)))
    for f in FAILS:
        print("FAIL", f)
    sys.exit(1 if FAILS else 0)
