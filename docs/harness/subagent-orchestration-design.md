# Subagents, DAGs and graphs: one boundary, one spawner, one record (design, 2026-10-14)

Owner's brief (2026-10-14): *"clear boundary and logic of DAG and GRAPH working; the agent should be
able to decide to use any of them with ease; robust subagents with proper tracking, context
building and control."* The reference is the owner's own design,
`~/active/claude-code-subagent-learnings/ARCHITECTURE.md` + `DESIGN.md` (derived from Claude Code
2.1.274). That document is a target, not a spec: where Wisp already does something differently and
well, this design keeps Wisp's way and says so.

Evidence labels: **observed** (read in the source or run), **inferred**, **assumed**.

---

## 1. What exists today (observed on `main` `6a84700`)

### 1.1 Five things called a graph, plan or DAG

| Module | What it is | Reached from |
|---|---|---|
| `wisp/multi_agent/dag.py` (`TaskDAG`, 316 lines) | In-memory DAG of subagent contracts, topological, parallel waves | the model's `orchestrate_dag` tool only (`wisp/tools/orchestration.py:166`) |
| `wisp/graph/` (`GraphExecutor`, ~6k lines) | Durable graph engine: typed nodes, validation, retries, budgets, verifiers, cycles, joins, trace, resume, `graph_*` tables | the REPL's host router (`coding.handle_prompt` → `decide_strategy`), `/graph run`, `wisp graph`, `sdk.py` |
| `wisp/graph/compat.py` (`dag_to_graph`) | Lowers a DAG onto graph primitives | **no production caller**, only `tests/reliability/test_dag_retirement_contract.py` |
| `wisp/core/task_graph.py` | The turn's work as a materialized graph in the session journal | `core/session.py`, `core/runtime.py` |
| `wisp/core/graph/` | Phase loop with oscillation traps; its docstring says "not wired, delete if no caller appears" | **nobody** |
| `wisp/planner.py` + `plan_task`/`update_plan` | A todo list for the model; spawns nothing | the model, `progress.py` |

So the same idea, "run these subagents in this order", has **two execution engines** (`TaskDAG`,
`GraphExecutor`) with different validation, failure, budget and persistence rules. M8 (the earlier
plan to retire `dag.py`) is blocked on one recorded divergence: `validate_graph` requires every node
reachable from an entrypoint, and `TaskDAG` allows disconnected components, so a naive re-point
would reject valid `orchestrate_dag` inputs (pinned in `test_dag_retirement_contract.py`).

### 1.2 Who decides which one runs

Per prompt, the REPL (`wisp/cli/repl.py:849` → `coding.handle_prompt`) decides **before the model
sees the prompt**:

1. `_loop_worthy` → the autonomous converge loop;
2. else `decide_strategy` scores the text: +2 per extra file named, +1 per keyword
   (`refactor`, `api`, `auth`, `feature`, `module`, …, at most 3), +1 if longer than 600 characters,
   +1 for "X and Y and Z"; a score of **2 or more runs a graph template**;
3. else the single-agent loop, where the **model** chooses among `spawn`, `fanout`,
   `spawn_background`, `orchestrate_vote` / `_map_reduce` / `_chain` / `_dag`.

The model cannot start a graph, and the host cannot see what the model would have planned. "Add
auth to the API" (two keywords) becomes a graph run whatever the model thinks; field observation
O-2 recorded the same router on substrings (now word-matched).

### 1.3 Tracking is spread over four stores

| Child kind | Recorded in |
|---|---|
| `spawn_background` / `fanout` children | `RunStore` (durable, leases, recovery) via `BackgroundManager` |
| sync `spawn`, `orchestrate_*` nodes | `.wisp/subagent_results.jsonl` + a `[sub] name` session |
| graph nodes | `graph_*` tables (`GraphStore`) |
| `TaskDAG` nodes | only the JSONL above |

`subagent_list` lists background children only (its tool description says so). Nothing links a
child to the parent turn, tool call or root run, so "what did this turn spawn, and what did each
child cost and change?" has no single answer.

### 1.4 Controls (after PR #146)

Holding, and checked through the real composition root: depth, `max_iterations`, role tools on
every entry point, worktree patch application, the approval relay, token budget admission, the
concurrency semaphore, timeouts and output caps (`tests/test_subagent_controls_reach_the_child.py`).

Missing against the reference:
- depth is enforced once, at spawn time: a `full`-mode child at the depth limit is still offered
  `spawn`, so it spends a round to be refused;
- concurrency waits for a slot instead of refusing with "Do not retry";
- no limit on the total number of spawns in a session;
- the model's `tools=` on `spawn` is not checked against the role, so it can ask for more than the
  role allows; the permission mode still applies, but the role does not;
- a schema failure keeps `success=True` (the `spawn` tool returns `"ok": true` with
  `"error": "Schema validation failed"`);
- without git, isolation silently falls back to the shared workspace;
- the parent receives prose: `spawn` returns `summary` (2,000 characters), `files` and `error`, with
  no evidence it can check.

---

## 2. The boundary

Three levels. Each has one purpose and one engine.

| Level | Use when | Shape | Lives | Engine |
|---|---|---|---|---|
| **Subagent** | one self-contained subtask; fresh eyes; a different role | one child | this turn (sync) or until settled (background) | `SubagentOrchestrator.run` |
| **DAG** | several subtasks with **data dependencies** (B needs A's output), planned now, run once | acyclic, written by the model as JSON | this turn | `GraphExecutor`, lowered from the DAG |
| **Graph** | a **workflow**: phases with verification and **repair loops** (implement → test → fix until green), resumable, inspectable afterwards | named template, may have cycles | durable run, survives the turn and a crash | `GraphExecutor` |

Rules that make the boundary hold:

1. **One spawner.** Every child, whether sync, background, DAG node or graph node, is created by
   `SubagentOrchestrator.run`. This is already true (observed: `_runner.run` has one call site), and
   it becomes a tested invariant.
2. **One engine for anything with more than one node.** `orchestrate_dag`, `_chain` and
   `_map_reduce` are lowered onto `GraphExecutor` (`compat.dag_to_graph` exists). The M8 divergence
   is resolved by adding a synthetic `start` node joined to every root, so disconnected components
   become reachable and no valid DAG is rejected. `TaskDAG` stays as the front-end validator until
   the parity test passes, then is deleted. `fanout` and `orchestrate_vote` stay as they are: they
   have no dependencies, so they need no graph.
3. **A DAG is a graph the model writes. A graph is a workflow the system keeps.** The difference is
   who authors the shape and whether it outlives the turn, not the engine.
4. `wisp/core/graph/` is deleted (no caller; its own docstring asks for that). `planner.py` stays a
   todo list and is renamed in its tool description so no one reads it as orchestration (D1 of the
   reference: one verb creates children).

---

## 3. The agent decides

The host stops deciding **for** the model and gives it the decision, with a short ladder in the
system prompt and the same words in each tool description:

```
Do it yourself unless one of these is true:
1. One separable subtask, or one needing a different role or fresh context  → spawn
   (spawn_background if you can keep working while it runs)
2. Several independent subtasks                                             → fanout
3. Subtasks where some need others' output, done once                       → orchestrate_dag
4. Work that needs verify-and-repair loops, or must survive this turn       → graph_run(template)
Never nest more than the depth limit says; never spawn to avoid reading a file.
```

- **New tool `graph_run`**: `{template, objective, files?, background?}`. It returns a run id, and
  `subagent_list` / `subagent_result` / `subagent_cancel` work on runs too, so the model learns one
  set of verbs. The templates are the existing ones in `coding_graphs.py` (`simple`,
  `parallel-analysis`, `repair`, `complex`, `parallel-implement-merge`), each
  with a one-line "use when".
- **The host router becomes a hint, then goes.** In the first release, `decide_strategy` no longer
  takes the turn. It adds one line to the model's context ("this looks like multi-file work;
  `graph_run repair` is available") and records its score. `WISP_REPL_GRAPH=force`
  keeps the old behaviour for anyone who wants it; `off` stays the kill switch. After a measured
  comparison (§6, S7) the hint is either kept or removed.
- **Graph workers still cannot spawn.** A graph node is a leaf, so nesting stays bounded by the
  graph's node budget and `max_subagent_depth`.

---

## 4. Robust subagents

### 4.1 Tracking: one record per child (`AgentRecord`, on `RunStore`)

`RunStore` already gives durable rows, leases and crash recovery to background children; every
other child gets the same row instead of a new store:

```
agent_id, parent_agent_id, root_run_id, origin (spawn|fanout|background|dag|graph),
graph_run_id?, node_id?, tool_call_id?, depth, role, tools[], isolation, model,
status (queued|running|succeeded|failed|cancelled|refused), refusal_reason?,
started_at, ended_at, tokens_in/out, cost_usd, iterations,
evidence[] (refs, §4.4), context_digest (§4.2)
```

- `subagent_list` shows every child of this session as a tree (parent → children), sync ones
  included; `/agents` renders the same rows.
- A refusal is a row too (`status=refused`, `refusal_reason=depth|concurrency|spawn_count|budget|
  tools`), so "why didn't it spawn" has an answer.
- Graph nodes keep their `graph_*` rows for the engine's own state and gain an `agent_id` link. The
  record is not duplicated; the graph row points at it.

### 4.2 Context building: one function, a bounded packet

`build_child_context(contract, parent) -> ChildContext` is the only place a child's first messages
are made:

| Part | Source | Bound |
|---|---|---|
| role prompt | `ROLE_CONFIGS` | — |
| task | the contract (model-written, so marked as such) | 8k chars |
| files | `context_files`, read now, each with path + size | 32k chars total, oldest cut first, cut stated |
| upstream results | DAG/graph inputs, each **labelled as another agent's claim** with its evidence refs | 8k per input |
| shared findings | `SharedContext` for parallel siblings | 4k |
| parent transcript | **not by default** (fresh); `inherit` only by an explicit snapshot id (reference D3), deferred | — |

The packet's sizes and a digest go into the record (`context_digest`), so a bad child run can be
replayed with the same input.

### 4.3 Control: every limit twice, every refusal typed

| Limit | Layer 1 (the tool is not offered) | Layer 2 (refused at spawn) | Refusal says |
|---|---|---|---|
| depth | `spawn`/`fanout`/`graph_run` dropped from a child at `depth >= max-1` | `orchestrator.run` (exists) | `depth N of M. Do the work yourself.` |
| concurrency | — | refuse when the pool is full **and** the caller is itself a child (a parent may queue; a child waiting on a slot its ancestors hold is the deadlock seen in #146) | `… Do not retry.` |
| spawns per session | dropped once reached | counter in the orchestrator, never reset in the session (default 64) | `spawn limit N reached` |
| budget | — | token/USD reservation before start, settled at the end, released on refusal (reference D9) | `spent X of Y` |
| tools | — | requested `tools` must be ⊆ role ∩ mode; anything wider is refused, not silently narrowed (reference D2, *reauthorize*) | `role R does not allow T` |

Other controls:
- **Cancel the tree.** Cancelling a child cancels its descendants; cancelling a graph run cancels
  its running nodes.
- **Isolation** (owner's question 1, answered 2026-10-14 with measurements: an APFS clone of this
  repo took 11.8 s and a plain copy 48 s, so copying is not an every-spawn default):
  - read-only roles: none;
  - write roles in a git repo: worktree;
  - write roles outside git: an APFS clone (`cp -c`) without `node_modules`, `.venv` and `.git`,
    under a size cap;
  - **if isolation cannot be made, the spawn is refused or asks the person; it never silently
    shares the workspace.**
- **Schema** (owner's question 2): a child whose output fails its declared `output_schema` after
  the repair retry is **failed** (`success=False`, text kept). `test_run_schema_validation_failure_no_retry`
  pins today's opposite and is changed with the reason recorded.

### 4.4 Handback: claim and evidence

`spawn` and graph results return:

```
{ ok, summary (the child's CLAIM), evidence: [ {kind: diff|test|file|command, ref, exit_code?} ],
  files_changed (from git when isolated), error, agent_id }
```

Evidence comes from what the harness saw (`observations`: tool calls and their exit codes, the
worktree diff), never from the child's prose. The parent's ladder text says: *treat `summary` as
unverified unless `evidence` supports it.* A deterministic scan (the reference's regex layer) runs
over `summary` before delivery and flags instruction-like text; it labels and never drops.

---

## 5. Invariants (each becomes a test)

1. Exactly one call site constructs a child (`_runner.run` inside `SubagentOrchestrator.run`). *(AST test)*
2. No child is offered a spawning tool at `depth >= max_subagent_depth - 1`.
3. Every child, refusal included, has exactly one `AgentRecord` linked to its parent.
4. `orchestrate_dag`, `_chain` and `_map_reduce` execute on `GraphExecutor`; `TaskDAG` has no runtime caller.
5. Every valid `TaskDAG` input is accepted by the lowering (the parity test, from M8's fixtures).
6. A child never waits for a slot held by one of its ancestors.
7. A refused or failed spawn returns its slot, its reservation and its spawn-count reservation.
8. A requested tool outside role ∩ mode refuses the spawn.
9. A child's context comes only from `build_child_context`, within the stated bounds.
10. A failed `output_schema` is a failed child.
11. Isolation that cannot be made never degrades to the shared workspace without the person's yes.
12. The parent receives `evidence` built by the harness, separate from `summary`.
13. With `WISP_REPL_GRAPH` unset, the host does not take a turn away from the model.

---

## 6. Slices

Each slice is one PR: red tests first through the composition root and the local SSE stub (with
`GET /v1/models`), mutation probes, the derived pages remapped from a line map, a lesson in
`AGENTS_LEARNING.md`.

| # | Slice | Proves |
|---|---|---|
| S1 | `AgentRecord` on `RunStore` for every child + `subagent_list` as a tree | inv. 1, 3 |
| S2 | Control: typed refusals, spawn count, layer-1 removal, tools ⊆ role ∩ mode, no ancestor wait, schema fail | inv. 2, 6, 7, 8, 10 |
| S3 | `build_child_context` + digest | inv. 9 |
| S4 | Handback evidence + deterministic scan | inv. 12 |
| S5 | Isolation policy (worktree / clone / refuse) | inv. 11 |
| S6 | `orchestrate_dag`/`_chain`/`_map_reduce` lowered onto `GraphExecutor`; delete `core/graph` | inv. 4, 5 |
| S7 | `graph_run` tool + the decision ladder; host router becomes a hint | inv. 13 |
| S8 | Measured: 20 real tasks on a live model (free OpenRouter model first), old router vs model choice: correct level chosen, cost, success | the decision in §3 |

S1-S3 need no model. S7 changes default behaviour, and S8 is its evidence: if the model chooses
worse than the router, the hint stays and §3 is revised.

---

## 7. Not changed, or deferred

- `inherit` context by snapshot id (reference D3): Wisp's children are fresh, so the race the
  reference fixes does not exist here yet. Deferred until a use appears.
- Model review of every handback (reference D8): S4 adds the deterministic layer only; one model
  call per child needs a cost decision.
- Hooks stay observe-only; the reference's rewrite hooks are not adopted, so its `validateRewrite`
  is not needed. The tools check in S2 is the part of *reauthorize* that applies here.
- `remote` isolation: out of scope.

## 8. Decisions (2026-10-14), with the evidence behind each

### 8.1 Spawns per session: 64, plus a duplicate guard

Measured on the owner's own `.wisp/subagent_results.jsonl` files (counts only; sessions approximated
by a 30-minute gap). Test runs were separated out: bursts of 39-78 children that used about 700
tokens in total and repeated 4 tasks are test suites, not use.

- Real sessions (over 20k tokens): 8 of them, with **5, 10, 14, 16, 17, 17, 30 and 33** children.
- The largest real session, 33 children, spent **596,506 tokens** (about 18k per child). In it, **one
  task ran 5 times and succeeded every time**; another ran 6 times, with one timeout among them.

Decisions:
- `max_spawns_per_session = 64` (`WISP_MAX_SPAWNS_PER_SESSION`). That is about twice the largest real
  session. At about 18k tokens per child, 64 children are about 1.2M tokens, inside the 2M token
  budget, so the two limits agree and the budget stays the cost control.
- From 48 spawns (75%), each spawn result tells the model how many remain.
- **Duplicate guard:** a spawn whose role and normalized task match a child that already
  **succeeded** in this session returns that child's `agent_id` and result instead of running
  again. This matters more than the count: the measured waste was repeats, not breadth. A failed or
  timed-out child may still be retried.

### 8.2 The host router becomes a hint in the same release as `graph_run`, gated by a decision eval

Field evidence against the router taking turns:
- O-2: an analysis prompt was routed to the graph;
- AGENTS_LEARNING 2026-10-11: a multi-part prompt started a graph whose workers could not write;
- O-3: Ctrl-C in the graph path killed the REPL (since fixed);
- O-20: a REJECT verdict was shown under a success mark;
- O-23: every correction cycle multiplies provider calls.

The owner runs with `WISP_REPL_GRAPH` unset, so the router is live for them today.

Decision: S7 ships `graph_run`, the ladder and the router-as-hint together. Its acceptance criterion
is S8's **decision eval** (below): the default flips only if the model picks the right level at
least as often as the router on the labelled set. If it does not, the PR ships `graph_run` with the
router unchanged, and the gap is recorded. Rollback is one variable: `WISP_REPL_GRAPH=force`.

### 8.3 S8 measures the decision on the owner's model, cheaply

The choice is model-dependent, so evidence must come from the model the owner runs
(`nvidia/nemotron-3-ultra-550b-a55b`). A full run per task is expensive: a measured graph turn used
about 47k input tokens, and real children about 18k each. NVIDIA rate-limits too (HTTP 429 seen
2026-10-13). So S8 has three layers:

1. **Mechanics, no model:** the local SSE stub drives every level end to end. This runs in CI.
2. **Decision eval, NVIDIA:** 20 labelled prompts (5 per level: do-it-yourself, spawn/fanout, DAG,
   graph), taken from real session prompts. Each run stops at the model's **first** orchestration
   call or first ordinary tool call; that call is the decision. That is about 20 requests per arm
   instead of 20 full runs. Both arms run on the same prompts: the router's verdict, and the
   model's choice with the ladder. 429s are retried with backoff and counted, not scored.
3. **End-to-end spot check:** 4 tasks (one per level) run fully on NVIDIA, for cost and
   correctness.

A free OpenRouter model is used only to debug the eval harness (its upstream 429s made earlier
results invalid evidence), never for the decision.
