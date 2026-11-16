# swarm.toml

An illustrative prototype of a governance layer for swarms of AI agents. Per-agent permissions already exist in agent frameworks. This project is about the inter-agent layer. 

swarm.toml is a single file at the root of a repo. It declares which agents may start,
instruct or message which others, how permissions and token budgets pass from one agent to
the next, which shared folders count as channels between agents, and which actions need
approval from several agents. A checker validates it before anything runs, and a
compiler turns it into hooks that enforce it at runtime (currently for Claude Code).
 

Swarms of agents are becoming more common. Recent high-profile incidents show that there is a gap in their governance.

## Status

This is a research prototype. It has been tested on Claude Code 2.1.312 with one model. See `docs/STATUS.md`.

## What's here

- `spec/`: the swarm.toml v0.1 spec, JSON schema and three example files
- `src/swarm_toml/`: the checker, compiler and enforcer (about 1,900 lines of Python)
- `tests/`: 482 tests
- `harness/`: runs an agent team under a given set of rules and scores the outcome
- `redteam/`, `ordinary/`, `c3b/`: the experiments and their results
- `docs/`: status and platform findings

`harness/cases/` holds 87 generated test cases. Most of its files are small toy workspaces
(text files, CSVs) that the agents work on.

## Results

Each experiment compares the same agent teams under no rules (C0), per-agent permissions
only (C2), and full swarm.toml (C3). The attacks were written by separate model sessions which were blind to rules. 

| Measure | No rules | Per-agent only | swarm.toml |
| --- | --- | --- | --- |
| Attacks that succeeded in scope (29 attacks × 6 runs) | 89/174 | 43/174 | 17/174 |
| Ordinary tasks completed (24 tasks × 3 runs) | 100% | 94.4% | 91.7% |

Hand-written hooks enforcing the same rules gave the same protection, though at 344–515 lines of
code per team against 9–13 lines of swarm.toml. The value is one shared, tested engine and a standard file, rather than stronger protection.

What it doesn't cover yet: an agent misusing access its own role already has (most of the
remaining successful attacks), many agents coordinating towards the same goal, and other
platforms. Full results and caveats: `redteam/SELECTION.md`, `ordinary/RESULTS.md`,
`c3b/REPORT.md`.

Next steps:
- Will it work against current threats? Need to run wider collusion; repeat across several models and platforms. Output: public eval suite for multi-agent control. 
- Will people use it? Expand beyond Claude Code; run 1/2 pilots with a lab, eval org and/or agent-platform team. Output: data from proper deployments. 
- Will it ever become a standard? Outside review of spec; keep open sourced; and get it referenced by eval and standards efforts. 

## Try it

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
uv run swarm check spec/examples/literature-review
```

To see the compiled output, copy an example to a scratch folder and compile it there (the
compiler writes `.claude/` and `.swarm/` into the target folder):

```sh
cp -r spec/examples/pipeline /tmp/swarm-demo
uv run swarm compile --target claude-code /tmp/swarm-demo
```

Running the experiments needs Claude Code 2.1.312; results are written to `~/swarm-toml-runs/`.

## License

Code: Apache-2.0 (`LICENSE`). Spec: CC BY 4.0 (`spec/LICENSE`).
