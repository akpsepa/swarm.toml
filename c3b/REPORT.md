# C3b: hand-written hooks, authoring cost

Each team's between-agent rules (RULES.md in `rules/`) were implemented by hand by a separate `claude-opus-5-5` session
that saw only RULES.md and HOOKS.md (`write.py`), with no swarm.toml, compiler or enforcer code. Its hooks were installed
unchanged in `harness/cases/<team>/c3b/`. Raw output and fence logs are in `raw/`.

| Team | Hook code (non-blank, non-comment lines) | Its own tests (lines) | swarm.toml lines for the same rules | Writer turns | Writer minutes |
| --- | --- | --- | --- | --- | --- |
| release | 395 | 174 | 12 | 20 | 4.5 |
| counting | 515 | 305 | 13 | 19 | 6.0 |
| orders | 344 | 204 | 9 | 11 | 3.6 |

swarm.toml lines count only the between-agent rules that C3b had to add (message routes, instance limits, budgets, channels,
aggregation), not roles and per-agent permissions, which both C3 and C3b get from the same setup. Writer time is the
model's, not a person's; it is a lower bound on effort, not a measure of it. Whether the hooks decide correctly is measured
by the C3b runs.

## What the line counts do and do not compare

The table above compares a team's hand-written hooks with its swarm.toml. That flatters
swarm.toml, because each team's hooks contain a whole enforcement engine while the
swarm.toml count leaves out the engine that reads it. Added after the C3b runs; no
numbers above change.

**What is in the hand-written code.** The hooks are not padded: they put each team's rules
into small tables at the top of `hook.py` (constants and tables: release 27, counting 42,
orders 30 non-blank lines, including generic ones such as the list of write tools). The
rest is machinery any correct enforcer needs, and ours has the same parts:

- a shared record of live agents, locked because several agents' hooks run at once;
- token counting from transcripts, for budgets;
- deciding whether a path or glob reaches a channel's folder;
- parsing shell commands for writes into that folder (the most defensive part);
- reading events, errors and logging.

A leaner writer could plausibly shorten this, but not to the size of the tables.

**The engine behind swarm.toml** (`src/swarm_toml`, non-blank, non-comment lines):

| Part | Lines |
| --- | --- |
| Loader, model, graph (`load.py`, `model.py`, `graph.py`) | 349 |
| Compiler to Claude Code (`compile/claude_code.py`, `perms.py`, `globs.py`) | 535 |
| Enforcer (`enforce/decide.py`, `hook.py`, `budget.py`, `audit.py`) | 505 |
| **Compile and enforce, total** | **1,389** |
| Optional: static checker (`check.py`, `invariants/`) | 432 |
| Optional: vote server (`enforce/vote_mcp.py`) | 59 |

The engine enforces every clause, per-agent ones included. The C3b hooks only add the
C3-only rules on top of that same enforcer running at C2. So the engine-against-hooks
figure below is not like for like either: it shows orders of size, not a ratio.

**Three comparisons:**

| Comparison | Hand-written | swarm.toml | Ratio |
| --- | --- | --- | --- |
| A team with no tooling, first team (the table above) | 344–515 lines | 9–13 lines, on an engine that already exists | ~30–40× |
| Each further team, once a reusable hand-built engine exists | ~27–42 lines of tables | 9–13 lines | ~2–3× |
| Total effort including the engine | ~350–500 lines, one team, C3-only rules | ~1,390 lines once, plus ~10 per team, every clause | swarm.toml is more code until several teams share it |

**What the data supports.** Protection matched (`RESULTS.md`). The saving in code is large
for a team building enforcement from nothing, and about 2–3× per team against a
well-factored hand-built engine. Lines of code alone do not show swarm.toml's other
claimed benefits: one tested engine shared across teams instead of many private ones, a
standard file other tools can read, static checks before anything runs, and rules a
non-programmer can review. This study did not measure those.
