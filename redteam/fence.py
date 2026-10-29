"""PreToolUse hook for red-team writers: deny file access outside the writer's directory and
log every tool call, so blindness to the repo can be checked afterwards."""

import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    data = json.loads(sys.stdin.read() or "{}")
    root = Path(os.environ["CLAUDE_PROJECT_DIR"]).resolve()
    ti = data.get("tool_input") or {}
    raw = ti.get("file_path") or ti.get("path") or ti.get("notebook_path")
    deny = None
    if data.get("tool_name") not in ("Read", "Write", "Edit", "Glob", "Grep", "LS"):
        deny = f"only file tools inside the working folder are available, not {data.get('tool_name')}"
    elif raw:
        p = (root / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
        if p != root and root not in p.parents:
            deny = "access outside the working folder is not available"
    with (root / ".fence.jsonl").open("a") as f:
        f.write(json.dumps({"ts": time.time(), "tool": data.get("tool_name"), "input": ti,
                            "denied": deny}) + "\n")
    if deny:
        print(deny, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
