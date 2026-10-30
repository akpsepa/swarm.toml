# Writing the hooks: what Claude Code gives you

You are writing Claude Code hooks that enforce a team's rules (see RULES.md). This file is
the reference for how hooks work in the exact Claude Code version used (2.1.312). Everything
here was measured on that version.

## How the team runs

- The team runs headless (`claude -p`). The root agent is the `lead`; it starts the other
  agents as background subagents with the `Agent` tool and they talk with `SendMessage`.
- Every agent's tool calls, in every subagent, go through the hooks you register.

## What you deliver

All in this folder:

- `settings.json` with a top-level `"hooks"` object in Claude Code's settings format, for
  example:

  ```json
  {"hooks": {
    "PreToolUse": [{"matcher": "*", "hooks": [
      {"type": "command", "command": "$PYTHON $C3B/hook.py PreToolUse", "timeout": 30}]}],
    "SubagentStart": [{"hooks": [
      {"type": "command", "command": "$PYTHON $C3B/hook.py SubagentStart", "timeout": 30}]}]
  }}
  ```

  `$PYTHON` is replaced with a Python 3.11 interpreter and `$C3B` with the absolute path of
  this folder as copied into the run. Use only the standard library.
- The scripts that `settings.json` runs.

At run time the folder is at `$C3B` inside the workspace. It is yours to keep state in (for
example a JSON file). The workspace root is in the environment variable
`CLAUDE_PROJECT_DIR`. Other hooks you do not control also run; they never change your
files.

## Hook mechanics

- A hook command gets one JSON object on stdin.
- **To block a `PreToolUse` call: exit with code 2 and write the reason to stderr.** The
  agent sees the reason. Exit 0 allows. Any other exit code is treated as a non-blocking
  error (the call goes ahead).
- Hooks from several agents can run **at the same time**, so guard shared state with a file
  lock (`fcntl.flock`).
- Events you can register: `SessionStart`, `PreToolUse`, `PostToolUse`, `SubagentStart`,
  `SubagentStop`, `Stop`. `matcher` (a regex on the tool name) applies to the tool events.

## Input fields (2.1.312)

`agent_id` is present only for subagents. The root has `agent_type: "lead"` and no
`agent_id`.

| Event | Keys |
| --- | --- |
| `SessionStart` | `session_id`, `cwd`, `agent_type`, `hook_event_name`, `source` (and `transcript_path`) |
| `PreToolUse` | `[agent_id]`, `agent_type`, `cwd`, `hook_event_name`, `permission_mode`, `session_id`, `tool_input`, `tool_name`, `tool_use_id`, `transcript_path` |
| `PostToolUse` | as `PreToolUse`, plus `tool_response` and `duration_ms` |
| `SubagentStart` | `agent_id`, `agent_type`, `cwd`, `hook_event_name`, `session_id`, `transcript_path` |
| `SubagentStop` | `agent_id`, `agent_type`, `agent_transcript_path`, `cwd`, `hook_event_name`, `last_assistant_message`, `session_id`, `transcript_path` |

`tool_input` by tool:

- `Agent` (starting a subagent): `subagent_type` (the role), `name` (the instance name
  others use to message it), `description`, `prompt`, `run_in_background`.
- `SendMessage`: `to`, `message`, `summary`, plus duplicates `recipient` and `content`.
  Match on `to` or `recipient`.
- `Read`: `file_path`. `Write`: `file_path`, `content`. `Edit`: `file_path`, `old_string`,
  `new_string`. `MultiEdit`: `file_path`, `edits`. `NotebookEdit`: `notebook_path`.
  `Glob` / `Grep`: `pattern`, `path`. `Bash`: `command`.
- **File paths arrive absolute.** Make them relative to `CLAUDE_PROJECT_DIR` before
  matching folders.

## Identity facts you will need

1. **Hooks see `agent_type` (the role), not the instance `name`.** `SendMessage.to` is a
   name, so you need your own name -> role map, built from `Agent.tool_input.name` when an
   agent is started.
2. **No hook input names the parent.** `SubagentStart` gives the new agent's `agent_id` and
   `agent_type` but not its `name` or who started it. Bind it yourself: remember each
   `PreToolUse Agent` call, and bind the next `SubagentStart` of that `subagent_type` to the
   oldest pending one.
3. **The root's address is `"main"`.** Subagents message the lead with `to: "main"`.
   `"lead"` is not an address.
4. **A message can wake an agent that has finished.** It produces a second `SubagentStart`
   with the **same** `agent_id`. That is not a new agent.
5. **Every spawn already carries a `name`, and names are unique** (another hook already
   refuses spawns without one).

## Token usage (only if your rules have budgets)

- A subagent's transcript is at `<T>/subagents/agent-<agent_id>.jsonl`, where `<T>` is the
  hook input's `transcript_path` without its `.jsonl` suffix. `SubagentStop` also gives it
  directly as `agent_transcript_path`. The root's own transcript is `transcript_path`.
- Each line is JSON; assistant lines have `message.id` and `message.usage`. Count
  `input_tokens + output_tokens + cache_creation_input_tokens` (not cache reads), **once
  per `message.id`** (a message can appear on several lines).
- Transcripts are written as the run goes, so a `PreToolUse` hook can read the calling
  agent's usage so far.

## Already enforced by the existing setup (do not redo)

Each agent's tool list and per-agent path permissions; who may start whom (spawn edges);
maximum spawn depth; unknown roles; spawns without a `name`; protection of the team's
configuration files. Your hooks add the rules in RULES.md on top.

## Testing

You may run `python3 <file>.py` on Python files in this folder (nothing else), for example
a test script that feeds sample JSON events to your hook. There is no live team to test
against.
