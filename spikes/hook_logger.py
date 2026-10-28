"""M0 spike hook: log every hook input verbatim; deny writes to BLOCKED.txt with exit 2."""
import json
import os
import sys

raw = sys.stdin.read()
log = os.environ.get("SPIKE_LOG", "/dev/null")
try:
    data = json.loads(raw)
except json.JSONDecodeError:
    data = {"_unparsed": raw}
with open(log, "a") as f:
    f.write(json.dumps(data) + "\n")

if data.get("hook_event_name") == "PreToolUse":
    ti = data.get("tool_input") or {}
    if str(ti.get("file_path", "")).endswith("BLOCKED.txt"):
        print("SWARM-DENY rule=M0-spike: writes to BLOCKED.txt are forbidden by swarm policy",
              file=sys.stderr)
        sys.exit(2)
sys.exit(0)
