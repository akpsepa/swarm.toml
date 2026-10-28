"""Token metering from Claude Code transcripts.

Counts input + output + cache-creation tokens, not cache reads (SPEC 4.3), once per API
message id. Transcripts exist only when session persistence is on (docs/M0-findings.md).
"""

from __future__ import annotations

import json
from pathlib import Path

COUNTED = ("input_tokens", "output_tokens", "cache_creation_input_tokens")


def subagent_transcript(session_transcript: str, agent_id: str) -> Path:
    """<dir>/<session>.jsonl -> <dir>/<session>/subagents/agent-<agent_id>.jsonl"""
    p = Path(session_transcript)
    return p.with_suffix("") / "subagents" / f"agent-{agent_id}.jsonl"


def tokens_used(transcript: str | Path | None) -> int | None:
    if not transcript:
        return None
    p = Path(transcript)
    if not p.is_file():
        return None
    seen: set[str] = set()
    total = 0
    with p.open() as f:
        for line in f:
            try:
                msg = json.loads(line).get("message")
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            mid = msg.get("id")
            if mid in seen:
                continue
            seen.add(mid)
            total += sum(int(msg["usage"].get(k) or 0) for k in COUNTED)
    return total


def tokens_for(hook_input: dict) -> int | None:
    """Tokens used so far by the agent that raised this hook event."""
    if hook_input.get("agent_transcript_path"):
        return tokens_used(hook_input["agent_transcript_path"])
    aid, tp = hook_input.get("agent_id"), hook_input.get("transcript_path")
    if aid and tp:
        return tokens_used(subagent_transcript(tp, aid))
    return tokens_used(tp)
