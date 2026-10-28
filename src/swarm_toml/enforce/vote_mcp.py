"""`swarm-vote`: a minimal stdio MCP server exposing swarm.request and swarm.vote.

The server holds no state and trusts no caller: MCP servers do not learn which agent is
calling. The PreToolUse hook sees each call with the caller's verified agent_id, checks it
and records it in the ledger, so by the time a call reaches this server it has already
been allowed and recorded. The server only acknowledges.
"""

from __future__ import annotations

import json
import sys

TOOLS = [
    {"name": "request",
     "description": "Open a quorum request for a gated action. Voters then call vote.",
     "inputSchema": {"type": "object", "required": ["action", "request_id"],
                     "properties": {"action": {"type": "string"},
                                    "request_id": {"type": "string"},
                                    "summary": {"type": "string"}}}},
    {"name": "vote",
     "description": "Approve or reject an open quorum request.",
     "inputSchema": {"type": "object", "required": ["request_id", "approve"],
                     "properties": {"request_id": {"type": "string"},
                                    "approve": {"type": "boolean"},
                                    "reason": {"type": "string"}}}},
]


def respond(req: dict) -> dict | None:
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        return None  # notification
    if method == "initialize":
        result = {"protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "swarm", "version": "0.1"}}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = req.get("params", {})
        args = p.get("arguments", {})
        text = (f"recorded vote on {args.get('request_id')}" if p.get("name") == "vote"
                else f"opened request {args.get('request_id')} for {args.get('action')}")
        result = {"content": [{"type": "text", "text": text}]}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            out = respond(json.loads(line))
        except json.JSONDecodeError:
            continue
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
