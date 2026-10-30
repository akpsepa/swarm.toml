# Rules: the release team

Roles: `lead` (root), `coder`, `reviewer`, `publisher`.

Your hooks must enforce these rules for every agent. Refuse anything they do not allow.

## 1. Who may message whom

A `SendMessage` is allowed only along these routes (sender role -> recipient role):

| Sender | May message |
| --- | --- |
| lead | coder, reviewer, publisher |
| coder | lead, reviewer |
| reviewer | lead, coder |
| publisher | lead |

Refuse every other message: to a role not listed, to a name that is not an agent started
in this run (the lead is `main`), and broadcasts (`to` of `*` or `all`, or empty).

## 2. How many of each role

At most one instance of each role in the run, the lead included. Refuse a spawn that would
start a second one. Re-waking a finished agent does not count as a new instance.

## 3. Shared folders

| Folder | May write | May read |
| --- | --- | --- |
| `src/**` | lead, coder | lead, coder, reviewer |
| `public/**` | lead, publisher | lead |

Refuse any file tool call (Read, Write, Edit, MultiEdit, NotebookEdit, Glob, Grep) on a path
in one of these folders by a role not listed for that side. Writing tools are Write, Edit,
MultiEdit and NotebookEdit; the others read. Paths outside these folders are not covered by
this rule.
