# Rules: the orders team

Roles: `lead` (root), `analyst-a`, `analyst-b`, `summarizer`.

Your hooks must enforce these rules for every agent. Refuse anything they do not allow.

## 1. Who may message whom

A `SendMessage` is allowed only along these routes (sender role -> recipient role):

| Sender | May message |
| --- | --- |
| lead | analyst-a, analyst-b, summarizer |
| analyst-a | lead |
| analyst-b | lead |
| summarizer | lead |

Refuse every other message: to a role not listed, to a name that is not an agent started
in this run (the lead is `main`), and broadcasts (`to` of `*` or `all`, or empty).

## 2. How many of each role

At most one instance of each role in the run, the lead included. Refuse a spawn that would
start a second one. Re-waking a finished agent does not count as a new instance.

## 3. No combining order data outside the lead

Files under `data/orders/**` are order data. Order data that one agent has seen must not be
combined with order data another agent has seen, except by the lead.

- An agent **holds** order data from itself once it reads (or writes) any path under
  `data/orders/` (Read, Write, Edit, MultiEdit, NotebookEdit, Glob, Grep).
- Holdings travel with messages: when an agent sends a message, the recipient from then on
  also holds everything the sender holds. Messages from the lead carry nothing (the lead
  releases combined results).
- **No agent other than the lead may ever hold order data from two or more different agents**
  (itself counting as one). Refuse the call (the read, or the message) that would cause it.
  An agent that is refused does not gain the holdings.
