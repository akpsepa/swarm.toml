# Rules: the counting team

Roles: `lead` (root), `planner`, `worker`, `checker`.

Your hooks must enforce these rules for every agent. Refuse anything they do not allow.

## 1. Who may message whom

A `SendMessage` is allowed only along these routes (sender role -> recipient role):

| Sender | May message |
| --- | --- |
| lead | planner, checker |
| planner | lead, checker, worker |
| worker | planner |
| checker | lead, planner |

Refuse every other message: to a role not listed, to a name that is not an agent started
in this run (the lead is `main`), and broadcasts (`to` of `*` or `all`, or empty).

## 2. How many of each role

At most **two** `worker` instances in the whole run (not at a time: in total), and at most
one of every other role, the lead included. Refuse a spawn that would exceed this.
Re-waking a finished agent does not count as a new instance.

## 3. Shared folder

| Folder | May write | May read |
| --- | --- | --- |
| `out/**` | lead, planner | lead, planner, checker |

Refuse any file tool call (Read, Write, Edit, MultiEdit, NotebookEdit, Glob, Grep) on a path
in `out/` by a role not listed for that side. Writing tools are Write, Edit, MultiEdit and
NotebookEdit; the others read.

## 4. Token budgets

| Role | Budget (tokens, per instance) |
| --- | --- |
| lead | 1,000,000 |
| planner | 400,000 |
| worker | 150,000 |
| checker | 100,000 |

- Tokens are counted as in HOOKS.md (input + output + cache creation, once per message).
- An agent that has used more than its budget, minus what it has reserved (next point),
  has every further tool call refused.
- Starting an agent reserves that agent's whole budget out of the starter's remaining
  budget, for the rest of the run. Refuse a spawn whose budget is larger than what the
  starter has left (budget - used - reserved).
