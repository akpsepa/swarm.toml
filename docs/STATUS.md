# Status

As of 2026-10-28. Claude Code 2.1.312, model `claude-sonnet-5-5`.

| Milestone | State | Evidence |
| --- | --- | --- |
| M0 spikes | Done: GO | `docs/M0-findings.md` (findings 1-12) |
| M1 spec + model | Done | `spec/SPEC.md`, `spec/swarm.schema.json`, `tests/test_examples.py` |
| M2 validator | Done | invariants I1-I10, `swarm check`, `tests/corpus/` (50 seeded + 12 valid), pre-commit hook, GitHub Action |
| M3 compiler | Done | `swarm compile --target claude-code`, conformance report, `tests/golden/` |
| M4 enforcer | Done | `decide.py` at 100% branch coverage; hook, audit, budget, swarm.vote; live check passed |
| Gate 1 | **Passed** | recall 50/50, 0/12 false positives (corpus and validator by the same author: needs outside review) |
| M5 harness | Built and tested offline; **live dry run not yet done** | `harness/`, `tests/test_harness.py`; 3 pilot cases |
| M6 suite | 3 pilot cases (one per open area) | `harness/cases/` |
| M7 runs | Not started | |

## Blocked

The C0 dry run (M5) launches headless agents whose prompts carry an adversarial test objective. The local auto-mode permission classifier refused it ("Create Unsafe Agents"). The run needs the researcher's explicit approval: a permission rule for `uv run python -m harness.run`, or running it manually.

## Decisions made while building (review these)

1. **`[actions]` table added to v0.1.** It maps action names to tool rules. Without it, `non_delegable`, labels and collective actions cannot be matched to calls.
2. **Bash-derived facts are inferred.** A shell implies read/write everywhere and network access. Findings that rest only on inferred facts are runtime-dependent: warnings at L1-L2, errors at L3-L4.
3. **`max_instances` is cumulative per run**, counted by distinct `agent_id`.
4. **Budgets count input + output + cache-creation tokens**, not cache reads.
5. **The `merge_by` role's output carries no fragments.** The merger is trusted to release combined results.
6. **On Claude Code, a spawn needs both `can_spawn` and `can_assign`**, because every spawn carries a prompt. Every spawn must also set `name`.
7. **C2 = the same enforcer limited to identity, per-agent permissions, integrity, default deny, spawn edges, max depth and non-delegable actions.** Spawn edges are included to keep the baseline strong, since Waxell already offers allowed-delegate lists.
8. **C0/C1 remove swarm.toml from the workspace; C2/C3 keep it**, because the hook pins its hash. Agents in C2 could read the file. This is a possible confound to note.
9. **Red-team rows 11-18 take the stopgaps in SPEC section 7.**
