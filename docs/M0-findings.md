# M0 findings: Claude Code spikes

**Verdict: GO.** Peer messaging works headless, hooks see every agent's calls with identity, and exit 2 blocks with the reason shown to the agent. Two brief assumptions are wrong and are corrected below: `TaskCreate` does not exist, and transcripts are not written under `--no-session-persistence`.

- Date: 2026-10-28
- Claude Code: **2.1.312** (pin this for every run)
- Model: `claude-sonnet-5-5` (`--model claude-sonnet-5-5`)
- Spike: `spikes/m0_spike.py` with hook `spikes/hook_logger.py`. Three agents: `lead` (root, via `--agent lead`), `alpha` and `beta`, all defined in `.claude/agents/`.
- Runs: `m0-run1` (dontAsk, failed writes), `m0-run2` (all checks pass, no persistence), `m0-run3` (all checks pass, persistence on) and `m0-nested` (lead → alpha → beta). Raw logs are in the session scratchpad and are not committed.

## Checklist

| M0 item | Result | Evidence |
| --- | --- | --- |
| Named subagent can `SendMessage` another named subagent under `claude -p` | **Pass** | alpha → `{"to":"beta","message":"PING-M0-4471"}`; response `{"success":true,"message":"Resuming agent beta","revivedAgentId":"…"}`; beta wrote `PING-M0-4471` to `beta_inbox.txt` (runs 2 and 3) |
| `PreToolUse` fires inside subagents with `agent_id` + `agent_type` | **Pass** for Agent, Read, Write, Edit, Bash, SendMessage and ToolSearch | `hooks.jsonl`, runs 2 and 3; `m0-nested`, where alpha (`agent_id` set) called `Agent` → beta |
| `claude -p --agent <role>` sets `agent_type` on the root's own calls | **Pass**. The root has `agent_type: "lead"` and **no `agent_id`** | every root event |
| Exit 2 blocks the call and the agent sees the reason | **Pass** | alpha reported: `PreToolUse:Write hook error: [...hook_logger.py]: SWARM-DENY rule=M0-spike: writes to BLOCKED.txt are forbidden by swarm policy`; the file was not created |
| Tokens per 3-agent toy run, projected for the matrix | **Partial**: measured per toy run; the projection needs a realistic case (see Budget) | runs 2 and 3 |
| `TaskCreate` `tool_input` fields | **No such tool** in 2.1.312, not even deferred behind `ToolSearch` | init `tools` list; alpha's `ToolSearch select:TaskCreate` returned nothing |

## Exact hook input fields

Top-level keys by event (2.1.312). `agent_id` is present only for subagents.

| Event | Keys |
| --- | --- |
| `SessionStart` | `session_id`, `cwd`, `agent_type`, `hook_event_name`, `source` (`transcript_path` too when persistence is on) |
| `UserPromptSubmit` | `agent_type`, `cwd`, `hook_event_name`, `permission_mode`, `prompt`, `prompt_id`, `session_id`, `transcript_path` |
| `PreToolUse` | `[agent_id]`, `agent_type`, `cwd`, `effort`, `hook_event_name`, `permission_mode`, `prompt_id`, `session_id`, `tool_input`, `tool_name`, `tool_use_id`, `transcript_path` |
| `PostToolUse` | as `PreToolUse`, plus `tool_response` and `duration_ms` |
| `SubagentStart` | `agent_id`, `agent_type`, `cwd`, `hook_event_name`, `prompt_id`, `session_id`, `transcript_path` |
| `SubagentStop` | `agent_id`, `agent_type`, `agent_transcript_path`, `background_tasks`, `cwd`, `effort`, `hook_event_name`, `last_assistant_message`, `permission_mode`, `prompt_id`, `session_crons`, `session_id`, `stop_hook_active`, `transcript_path` |
| `Stop` | as `SubagentStop` minus `agent_id` and `agent_transcript_path` |

`tool_input` for the coordination tools:

- **`Agent`** (hook `tool_name` is `Agent`; the init tool list calls it `Task`). Fields: `subagent_type`, `name`, `description`, `prompt`, `run_in_background` (optional). `PostToolUse.tool_response` for a background spawn: `{isAsync, status:"async_launched", agentId, resolvedModel, outputFile, canContinueAgent, ...}`.
- **`SendMessage`**. `PreToolUse` sees `to`, `message`, `summary`, plus normalised duplicates `recipient`, `recipient_kind` (`"name"`), `content` and `type` (`"message"`). `PostToolUse.tool_input` shows only `to`, `summary` and `message`. **Match on `to`/`recipient`, and on `message`/`content`.**
- `Write`: `file_path`, `content`. `Edit`: `file_path`, `old_string`, `new_string`, `replace_all`. `Read`: `file_path`. `Bash`: `command`, `description`, `timeout`. `TaskStop`: `task_id`.
- File paths arrive **absolute** (resolved under `cwd`); normalise them before glob matching against channel `paths`.

## Consequences for the design

1. **No `TaskCreate`.** `can_assign` cannot hook a task tool. Assignment happens through the `Agent` prompt at spawn and through `SendMessage` to a running agent. The spec should define assignment over those two tools, and the conformance report should mark any task-list clause as unenforced on this target.
2. **No parent or chain in hook input.** Neither `PreToolUse` nor `SubagentStart` names the spawning agent. To fill the audit `chain`, the enforcer must keep its own spawn ledger. Record the caller of each `PreToolUse Agent`, then bind the next `SubagentStart` of that `subagent_type`. `PostToolUse Agent` (`agentId`) confirms the binding, but only once the call returns, which for a foreground spawn is after the child finishes. Parallel spawns of the same type make this ambiguous. That needs a spike in M4, or I4/attenuation checks must be keyed on role, not on instance.
3. **Hooks see `agent_type`, not the instance `name`.** `SendMessage.to` is a name (`"beta"`), so the enforcer needs a name → role map, built from `Agent.tool_input.name` at spawn. `name` is **optional**: in `m0-nested`, alpha spawned beta without one. So a message to an unmapped name should be denied by default, and governed swarms may need the hook to require `name` on every spawn.
7. **Nested spawning is available.** Subagents have the `Agent` tool, and `PreToolUse Agent` fires inside them with their `agent_id`. So `max_depth` and scope attenuation are both enforceable at spawn time.
4. **A message can revive a finished agent.** `SendMessage` to a stopped agent resumes it, with a second `SubagentStart` that reuses the same `agent_id`. Budget and `max_instances` must count by `agent_id`, not by `SubagentStart` events.
5. **The root has no `agent_id`.** The decision function must treat a missing `agent_id` with `agent_type == <root role>` as the root.
6. **Background notifications re-enter the root.** One `claude -p` run produced three `result` events, one per root turn woken by a completion. The harness must read until process exit, not until the first `result`.

## Harness settings that work

- **Do not** use `--dangerously-skip-permissions`; the local auto-mode classifier refuses it for spawned swarms.
- `--permission-mode dontAsk` denied `Write` even with `Write` in `permissions.allow` (run 1). Use **`--permission-mode acceptEdits --permission-prompts none`** with an explicit `permissions.allow` list: anything that would prompt is denied.
- Isolation: `--setting-sources project` (drops user hooks and plugins) and `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`. Without the latter, the user's claude.ai connectors (Gmail, Drive and others) appeared in the swarm's tool list.
- Hooks run with exit 2 under `acceptEdits`. Permission denials happen independently of hooks: in run 1 both fired on the same call.

## Budget

| Measure (one toy run, 3 agents, ~12 tool calls) | Value |
| --- | --- |
| Root tokens (input + output + cache) | ~88–90k |
| Subagent tokens from transcripts | ~39k (alpha) + ~43k (beta, two activations) |
| Subagent `total_tokens` from `task_notification` | 10.9k–15.1k per agent (this counter excludes cache reads) |
| List-price cost reported by `result.total_cost_usd` (root only) | $0.12–0.24 |
| Wall time | ~75 s |
| Plan utilisation across 3 runs | `five_hour` 0.03 → 0.04; `seven_day` stayed at 0.07 |

- **Metering.** `transcript_path` and `agent_transcript_path` are **not written** under `--no-session-persistence`. `SubagentStop` metering therefore needs persistence on, which also leaves transcripts in `~/.claude/projects/`; the harness must clean them up. As a backup, `stream-json` assistant messages carry per-message `usage` tagged by `parent_tool_use_id`, which gives per-agent totals without transcripts.
- **Budget definition.** The two counters differ by about 3×, because cache reads dominate. The spec must say which tokens a role budget counts. I recommend input + output + cache creation, excluding cache reads.
- **Quota tracking.** Each run's stream includes a `rate_limit_event` with `five_hour` and `seven_day` utilisation, rounded to 2 decimals. The harness should log it before and after every run and stop at a configured ceiling. That is more reliable than a token-to-quota conversion that Anthropic does not publish.
- **Projection (not yet a go/no-go).** At about 0.3% of the 5-hour window per toy run, the 5-hour window caps a session at about 300 toy-sized runs. Real cases with attackers and benign tasks will likely be 5–20× larger. The weekly fit cannot be judged until one realistic M6 case is measured. Re-check at the M5 dry run, before freezing the seed count in `prereg/`.

## M4 live checks (compiled enforcer under `claude -p`)

Spike: `spikes/m4_live.py`, runs `m4-live1` and `m4-live2`. Lead → reviewer (background) and coder, with the compiled `swarm-hook`. Every expected allow and deny landed, and every decision appears in `.swarm/audit.jsonl`: helper spawn denied (`spawn-edges`), `swarm.toml` write denied (`integrity`), coder → main denied (`message-edges`), reviewer → main delivered.

8. **The root's `SendMessage` address is `"main"`.** It is not the role name; `lead`, `parent` and `team-lead` are all "not reachable". The tool description says `"main"` works for background subagents only. The enforcer aliases `main` to the root instance.
9. **`settings.json` deny rules fire before `PreToolUse` hooks.** A denied call never reaches the hook, so it is never audited. The compiler therefore emits no deny rules, and the hook denies protected writes itself. (In run 1, the `dontAsk` denial and the hook both fired on one call, so the order depends on the kind of permission decision.)
10. **Project `permissions.allow` is ignored in an untrusted workspace.** stderr: "Ignoring 4 permissions.allow entries from .claude/settings.json: this workspace has not been trusted". Deny rules and hooks still apply. In M0, `Write` succeeded because of `acceptEdits`, not the allow list. The harness must pass permissions with `--settings <file>` (for C2 especially) and run with `stdin` from `/dev/null`.
11. **Safety: subagents can reach other local Claude sessions.** `ListAgents` from inside a swarm subagent lists every local session, including the researcher's own interactive session, and `SendMessage` can address them. The compiled hook denies them, since an unknown name fails default deny. **C0–C2 have no such hook, so the harness must add a guard that denies `SendMessage` to any name outside the run in every condition.** This is a guardrail, not a condition variable.
12. **Metering on every call works.** A subagent's transcript is at `<session>/subagents/agent-<agent_id>.jsonl`, derivable from `transcript_path` and `agent_id` in `PreToolUse`. Budgets are therefore enforced from the first call after the limit, not only at `SubagentStop`.

## Open items

- [ ] Spawn-chain binding under parallel same-type spawns (consequence 2). The enforcer binds `SubagentStart` to the oldest pending spawn of that type (FIFO), so the name and parent can be swapped between two same-type siblings. Their role and depth are still correct.
- [ ] Whether `SessionStart` with `source: "resume"` fires on agent revival; it did not in these runs.
- [ ] Which real deployment to use for M8 (owner's decision).
