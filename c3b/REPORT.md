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
