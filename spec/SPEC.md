# swarm.toml v0.1

**Status:** draft, normative. Licensed CC-BY-4.0. Schema: [`swarm.schema.json`](swarm.schema.json). Reference validator: `swarm check`.

swarm.toml is a file at a repository root that governs the layer *between* agents in a swarm. It declares who may spawn, assign to or message whom; how authority and budget pass along those edges; which shared state counts as a channel; and which actions need a quorum. Each agent's own tool, path and network rules stay in existing permission files, which swarm.toml references rather than restates.

The key words MUST, MUST NOT, SHOULD and MAY are used as in RFC 2119.

## 1. Model

A **swarm** is a set of agent **instances**, each running as exactly one **role** declared in the file. Instances are connected by four kinds of directed edge between roles:

| Edge | Field | Carries | Meaning |
| --- | --- | --- | --- |
| spawn | `can_spawn` | authority (attenuated), budget | An instance of A may start an instance of B |
| assign | `can_assign` | authority | A may hand B a structured task. The only edge that carries instruction (R7) |
| message | `can_message` | data only | A may send B free text. The enforcer MUST deliver it labelled as untrusted peer data |
| channel | `[[channel]]` | data, and authority when it holds tasks | Writers may write paths that readers read (R4) |

The **communication graph** is the union of message edges and channel flows (writer → reader). The **authority graph** is the union of assign edges and channel flows. The **control graph** is the union of spawn and assign edges.

A **root** is a role that no other role can spawn, or the role named by `[swarm] root`. **Depth** is the number of spawn hops from a root instance; roots have depth 0.

## 2. File format

The file is TOML 1.0 and MUST be named `swarm.toml`. Tools discover it by walking up from the working directory. Unknown keys are invalid. Every table except `[roles]` is optional.

Name syntax:

- **Role names** match `^[a-z][a-z0-9_-]*$`.
- **Action names** are dotted lower-case identifiers with at least one dot (`git.push.main`).
- **Rule strings** use Claude Code / agent-perms syntax: `Tool` or `Tool(spec)`, e.g. `Read(notes/**)`, `Bash(uv run pytest:*)` or `mcp__crm__read`.
- **Paths** are repo-relative globs. `**` spans segments, `*` and `?` stay within one, and a leading `./` is ignored.

### 2.1 `[swarm]`

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | the directory name | Swarm name, used in audit records |
| `default` | `"deny"` \| `"allow"` | `"deny"` | Decision for anything undeclared. `"allow"` parses but fails I10; it exists so the error can be reported with a line |
| `root` | role | every role no other role can spawn | Entry role. REQUIRED when every role is spawnable (see I2) |

### 2.2 `[identity]` (R2)

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `issuer` | `"enforcer"` \| `"spiffe"` | `"enforcer"` | Who issues role credentials. An agent MUST NOT declare its own role |
| `trust_domain` | string | none | SPIFFE trust domain, when `issuer = "spiffe"` |

### 2.3 `[conformance]` (R5)

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `required` | `"L1"`–`"L4"` | `"L1"` | Minimum level a target must enforce (§6). Also sets finding severity (§5.1) |

### 2.4 `[roles.<name>]`

At least one role is REQUIRED.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `permissions` | reference \| inline table | none | Per-agent tool, path and network rules (§3). With none, the role is treated as able to use every tool, and every fact about it is *inferred* |
| `max_instances` | integer ≥ 1 | `1` | Instances of this role per run, **counted cumulatively** by distinct instance id, so a revived instance counts once |
| `can_spawn` | role[] | `[]` | Spawn edges |
| `can_assign` | role[] | `[]` | Assign edges |
| `can_message` | role[] | `[]` | Message edges. A role MAY list itself, letting its instances message each other |
| `budget` | `{ tokens = int }` | none | Per-instance budget. Spawning a child carves the child's budget out of the parent's remaining budget (§4.3) |
| `capabilities` | action[] | inferred from tools | When present, the **complete** list of abstract capabilities (§3.2) the role holds, plus any project actions it holds outright. An abstract capability implied by a tool but missing here MUST be removed by the target (e.g. a sandbox without network for `Bash`), or the target reports the clause as unenforced |
| `allow_escape` | bool | `false` | Waives I8 for this role |
| `escape_reason` | string | none | REQUIRED when `allow_escape = true` |

### 2.5 `[delegation]`

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `attenuate` | bool | `true` | Spawned roles' permissions MUST be a subset of the spawner's (I1). `false` is allowed but reported |
| `max_depth` | integer ≥ 0 | the deepest declared spawn chain from a root | Spawns that would exceed this depth are refused. REQUIRED when the spawn graph has a cycle |
| `non_delegable` | action[] | `[]` | Actions no spawned role may hold, and that no role may reach through assign edges or channels unless it holds them itself (I1, I4) |

### 2.6 `[[channel]]` (R4)

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | required, unique | Channel name |
| `paths` | glob[] (≥ 1) | required | Shared paths: files, task lists, stores mounted as files |
| `writers` | role[] | `[]` | Roles that may write `paths` |
| `readers` | role[] | `[]` | Roles that may read `paths` |

A channel restricts and declares; it grants nothing. A role still needs per-agent permission to touch the paths. At L3+ the enforcer MUST refuse a read or write of a channel path by a role not listed.

### 2.7 `[labels]` (R9)

`[labels.<name>]` or `<name> = { ... }`:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `tools` | (action \| rule string)[] | `[]` | Tools whose output carries the label. An entry that names an `[actions]` key means that action's tool calls |
| `paths` | glob[] | `[]` | Paths whose contents carry the label |

### 2.8 `[[aggregation]]` (R9, experimental)

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `labels` | label[] (≥ 1) | required | Restricted labels whose fragments may not be combined |
| `merge_by` | role | required | The only role allowed to combine fragments (I5) |

### 2.9 `[[collective]]` (R6)

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `action` | action | required | The gated action. MUST be defined in `[actions]` |
| `requested_by` | role | required | The role that may request the action |
| `requires_quorum.of` | role | required | Voter role |
| `requires_quorum.min` | integer ≥ 1 | required | Distinct approving voter instances needed |
| `requires_quorum.independent` | bool | `true` | The requester MUST have no control path to the voters (I9). At runtime, approvals from instances spawned by the requester's own subtree are not counted |

### 2.10 `[audit]` (R10)

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `sequences` | bool | `false` | Log action sequences per task, for detecting action splitting after the fact |
| `path` | string | `".swarm/audit.jsonl"` | Audit log. Protected (I7) |

### 2.11 `[actions]` (new in v0.1; not in the RFC draft)

`"<action>" = [rule strings]` maps a project action to the tool calls that perform it, e.g. `"git.push.main" = ["Bash(git push origin main:*)"]`.

Every action named in `non_delegable`, a `[[collective]]`, a label's `tools` or a role's `capabilities` (other than the abstract capabilities in §3.2) MUST be defined here. Without the mapping, an enforcer cannot tell which tool call *is* the action, so the clause could never fire.

## 3. Permission references

### 3.1 Forms

| Form | Example | Resolution |
| --- | --- | --- |
| inline | `{ allow = ["Read(notes/**)"], deny = ["Bash"] }` | As written |
| agent-perms | `"agent-perms:profiles.web-read"` | The profile in the nearest `.agents/permissions.json` walking up from swarm.toml, with `extends` applied |
| Claude Code | `"claude:.claude/agents/writer.md"` | The subagent file's `tools` and `disallowedTools` frontmatter. A file without `tools` inherits every tool |

A reference that cannot be resolved makes every fact about that role *inferred* (§5.1).

### 3.2 What the validator derives

| Rule | Derived fact | Provenance |
| --- | --- | --- |
| `Read`, `Grep`, `Glob`, `LS` (spec) | reads the glob (`**` if no spec) | declared |
| `Write` (spec) | writes the glob | declared |
| `Edit`, `MultiEdit`, `NotebookEdit` (spec) | writes and reads the glob (an edit returns what it changes) | declared |
| `Bash` (any spec) | `code.exec` | declared |
| `Bash` (any spec) | reads and writes `**`; `network.egress` | **inferred**: a shell command cannot be parsed for paths or hosts |
| `WebFetch`, `WebSearch` | `network.egress` | declared |
| `Agent` / `Task` | `agent.spawn` | declared |

The abstract capabilities are `code.exec`, `network.egress`, `agent.launch` (starting an agent outside the enforcer) and `model.call` (calling a model endpoint directly).

A role **holds** an action when the action is in its capabilities, or when an allow rule overlaps one of the action's rules. A role with `code.exec` holds every Bash-defined action as an *inferred* fact.

Patterned `deny` rules are ignored, so derived permissions over-approximate. Bare tool denies (`"Bash"`) remove the tool.

## 4. Runtime semantics

An enforcer at level L3 or above MUST provide the following.

### 4.1 Identity (R2)

The enforcer binds a role credential to each instance at spawn. Every spawn, assignment, message and tool call is attributed to that credential, never to a claim the agent makes. A spawn MUST name a declared role.

### 4.2 Default deny (I10)

A spawn, assignment, message, channel access or action not permitted by the file is refused. An instance whose role is unknown to the enforcer is refused everything.

### 4.3 Budgets (L4)

A role's budget counts input, output and cache-creation tokens, **not** cache reads (M0 found cache reads dominate and inflate totals by about 3×).

Spawning an instance of B reserves `B.budget` from the spawner's remaining budget, and the spawn is refused if the remainder is too small. An instance whose metered use reaches its budget is refused further tool calls. Instances are counted cumulatively (§2.4), so respawning cannot reset a budget.

### 4.4 Messages and assignments (R7)

A free-text message MUST be delivered marked as untrusted peer data. Only assign edges and channels carry instruction.

### 4.5 Collective actions (R6)

A gated action is refused unless the quorum ledger holds at least `min` approvals for that request, from distinct voter instances that, when `independent`, were not spawned by the requester or its descendants.

### 4.6 Integrity (R1)

At load, the enforcer pins a sha256 over swarm.toml and every file it references. If the hash changes during a run, every call is refused until a restart with human review. No role may write the protected set: `swarm.toml`, referenced files, `.claude/**`, `.swarm/**` and the audit log.

### 4.7 Fail-closed

If the enforcer cannot load or verify the policy, it refuses every call.

## 5. Static invariants

`swarm check` evaluates these over the effective graph. I1–I2 follow AgentFlow; I3–I9 are new; I10 is the static half of default deny.

| ID | Invariant | Class |
| --- | --- | --- |
| I1 | **Attenuation.** For every spawn edge A → B, every allow rule of B is contained in one of A's and every capability of B is held by A. No spawned role holds a `non_delegable` action | static, or runtime when a profile is unresolved or the fact is inferred |
| I2 | **Bounded spawning.** The spawn graph is acyclic, or `max_depth` is set explicitly. At least one root exists | static |
| I3 | **Budget conservation.** For every budgeted spawner A, the sum of `budget × max_instances` over the roles A can spawn is at most A's budget. A spawned role with no budget under a budgeted spawner is a *missing budget* | static; missing budget is runtime |
| I4 | **No laundering over paths (R8).** For every `non_delegable` action X and every role R that does not hold X, no role reachable from R in the authority graph holds X | static, or runtime if the hold is inferred |
| I5 | **Aggregation closure (R9, experimental).** Any role that can receive two or more fragments of an aggregation's labels must be its `merge_by` role. See below | static if two declared fragments, else runtime |
| I6 | **Declared channels (R4).** Whenever role A can write a path that role B can read (A ≠ B, or A = B with `max_instances` > 1), a channel with A among its writers and B among its readers covers it. Protected paths are excluded | runtime |
| I7 | **Integrity (R1).** No role can write a protected path (§4.6) | static, or runtime if inferred |
| I8 | **Escape (R3).** No role holds `code.exec` together with `network.egress`, `agent.launch` or `model.call` unless `allow_escape = true` with an `escape_reason`. `allow_escape` without a reason is an error | runtime; a missing reason is static |
| I9 | **Independent voters (R6).** With `independent = true`, the requester is not the voter role and has no path in the control graph to it. In every quorum, `min ≤ max_instances` of the voter role | static |
| I10 | **Default deny.** `default = "deny"`, and every role, label and action the file names is declared | static |

**Fragments (I5).** A fragment is one source (path glob or tool) of one label, read by one role. Fragments flow along spawn, assign, message and channel edges. A role with `max_instances > 1` passes on each fragment once per instance, since instances may read different parts. The `merge_by` role is trusted to release combined results, so what it passes on carries no fragments. This over-approximates. It is experimental in v0.1, and results MUST report what it does not catch.

### 5.1 Severity

Each finding is static or runtime-dependent:

- **Static** findings are always errors.
- **Runtime-dependent** findings rest on facts only an enforcer can settle (an inferred fact, an unresolved profile, or a class the RFC lists as runtime: undeclared channels, missing budgets, escape-capable roles). They are **warnings at L1–L2 and errors at L3–L4**.

Adopting the file early therefore never blocks a merge over rules no one can enforce yet.

### 5.2 `swarm check`

| Exit | Meaning |
| --- | --- |
| 0 | No errors (warnings allowed) |
| 1 | One or more errors |
| 2 | Invalid file: missing, not TOML, or fails the schema |

Each finding prints as `path:line: severity Ik: message`. `--json` prints the findings, the level and the policy hash.

## 6. Conformance (R5)

| Level | The target guarantees |
| --- | --- |
| L1 | The file is validated statically (CI or pre-commit) |
| L2 | L1, plus per-agent permissions enforced |
| L3 | L2, plus identity and the spawn, assign, message and channel edges enforced, plus integrity and default deny |
| L4 | L3, plus budget metering, labels and collective rules |

A compiler MUST list every clause the target cannot enforce. It MUST refuse to emit, or a runtime refuse to start, when any clause required at `[conformance] required` is unenforced. A target MUST NOT drop rules silently.

### 6.1 Claude Code target notes (2.1.312; see `docs/M0-findings.md`)

- Identity comes from hook `agent_type` and `agent_id`. The root has no `agent_id`, is identified by `--agent <role>` and is addressed as `"main"` in `SendMessage`. Hooks carry neither the instance `name` nor the parent, so the enforcer keeps its own spawn ledger and requires `name` on every spawn.
- `SendMessage` can reach other Claude sessions on the same machine. Default deny refuses every name not bound in the run.
- **There is no `TaskCreate` tool.** Assign edges are enforced on the `Agent` prompt at spawn and on `SendMessage` to a running instance that was spawned by the sender.
- Budgets are metered from the caller's transcript on every call, which requires session persistence. L4 budget enforcement is approximate: the call that crosses the limit completes, and the next is refused.
- `Bash` cannot be parsed for paths or hosts. Without a sandbox, any role with `Bash` can escape and can write any path. Claude Code's sandbox applies per session, not per agent.

## 7. Not in v0.1

These come from the red-team rows still open for decision (#11–#18). v0.1 takes the stated stopgap.

| # | Gap | v0.1 stopgap |
| --- | --- | --- |
| 11 | Roles created at runtime | A spawn naming an undeclared role is refused (no fallback role) |
| 12 | Budget is tokens only | Tokens only, metered by the enforcer, counted cumulatively |
| 13 | Org-level and nested files | One file per swarm; no layering |
| 14 | Agents hidden behind tools | Not addressed |
| 15 | External A2A peers | Default deny covers them; no `[external]` table |
| 16 | Human approvers | No `[escalation]`; quorums are agent-only |
| 17 | Fail-open, mid-run revocation | Fail-closed (§4.7); permissions re-checked on every call |
| 18 | Correlated voters | Not addressed; a documented limitation |
