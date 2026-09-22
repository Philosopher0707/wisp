# WISP — PERSISTENT GRAPH LOOP ALIGNMENT AUDIT

**Phase 0 deliverable — audit only. No implementation code was modified.**

- **Repository:** `/Users/philosopher/iCloud Drive (Archive)/Documents/wisp`
- **Baseline commit:** `83b10af` (Phase 9) with Phase 10 work uncommitted
- **Method:** source-traced reconnaissance (7 parallel subsystem sweeps) followed by direct
  verification of every load-bearing "absent/unwired" claim. Evidence is `file:line`.
- **Standing rule applied throughout:** *actual repository evidence > architectural assumption.*
  The research report was treated as a target reference, never as a description of Wisp.

---

## 1. Executive Summary

### 1.1 The finding that reframes the engagement

Wisp does not need a Persistent Graph Loop built. **Most of it has already been built — in four
separate places that do not talk to each other.**

| Layer | What it is | Reached by the live turn path? |
|---|---|---|
| **A. Interactive agent loop** | `WispAgentCore.turn` + `AgentRuntime` — the thing users actually run | **YES** — this *is* the product |
| **B. Durable graph engine** | `wisp/graph/` — validated, SQLite-durable, hash-chained, artifact-producing execution engine | **NO** — `wisp graph` CLI / SDK / one REPL template only |
| **C. Experimental phase loop** | `wisp/core/graph/` — a literal OBSERVE→…→RECOVER→TERMINATE loop with a stagnation detector | **NO** — tests only; disowned in its own docstring |
| **D. Durable run / contract / trace layer** | `wisp/runs/`, `wisp/contracts/`, `wisp/trace/` | **NO** — constructed without its store; tables empty |

The target architecture sits **precisely in the gap between A and B**. Layer B has the persistence,
validation, provenance and artifacts but a **frozen topology** (no runtime graph mutation at all).
Layer A has all the dynamism (the model decides everything, every turn) but **no graph, no persisted
node state, and no evidence**.

### 1.2 The second finding: the dominant pattern is now measurable

This repository has a documented, self-diagnosed pathology — *"written-but-unwired controls"*
(`docs/audit-2026-08-24.md:270`, `CONTEXT.md:216-286`). This audit found **eight more instances**,
and they are not small ones: the durable run store, the contract envelopes, the trace store, the
subagent capability-narrowing function, the subagent circuit breaker, the three-tier context
compactor, the independent PageRank RepoMap, and — most consequentially — **the only stagnation
detector in the codebase**.

Every one of them is *complete code with tests that pass*. None is reachable from a production turn.
The failure mode is not "missing capability"; it is **capability that cannot be observed to be
missing because it exists and is tested**.

### 1.3 The third finding: verification is a self-assessment

In the live loop, a single model invocation plans, acts, and judges its own success
(`core/stateless.py:744,771`). The only completion gate is `VerificationFloorGuard`
(`core/verification.py:119-206`) — deterministic bookkeeping over *the same model's own tool
results*, which explicitly permits surrender with the word `UNVERIFIED`
(`core/verification.py:36-41`). Success is otherwise **event-derived**: `saw_done and not
saw_fatal_error` (`core/runtime.py:499`).

### 1.4 Bottom line

| Question | Answer |
|---|---|
| Is Wisp a Persistent Graph Loop? | **No.** The live loop is a stateless ReAct turn loop with no graph. |
| Does Wisp contain a persistent graph? | **Yes** — a durable, validated, audited one (`wisp/graph/`), but with frozen topology and no coupling to the loop. |
| Is a rewrite required? | **No.** The minimum evolution is *connection and one new capability* (runtime graph mutation), not replacement. |
| What is the single highest-value change? | Make the interactive loop's state **durable and typed** so that planning, execution, verification and recovery become explicit state transitions rather than prose in a message list. |

---

## 2. Repository Scope

### 2.1 Size and shape (verified)

| Fact | Value | Source |
|---|---|---|
| `wisp/` Python files | 366 | `CONTEXT.md:351` (re-verified by tree listing) |
| Test files / functions | ~357 files, ~5,374 functions | `CONTEXT.md:352` |
| Tools | 42 (`TOOL_SCHEMAS` == `TOOL_IMPLS` == 42) | `wisp/tools/registry.py` |
| Entry point | `wisp = wisp.__main__:main` | `pyproject.toml:35` |
| Python | `>=3.11`, POSIX-only | `pyproject.toml` |
| Server | 27 routers, ~70 routes, 41 mutating | `CONTEXT.md:359` |
| Graph package | 22 modules, ~5,628 LOC (`executor.py` alone 1,149 LOC) | `wisp/graph/` |

### 2.2 Subsystems inspected

Core loop, session/runtime, transport, tools + registry, authority (`auth/`, `policy/`, `pathsec`),
sandbox, verification, graph (both packages), multi-agent/delegation, persistence (`runs/`,
`contracts/`, `trace/`, `eval/`), memory, context engineering, repository intelligence, tests.

### 2.3 Explicitly out of scope

The desktop client (`wisp-desktop/`, TypeScript), the Android tree, packaging, and the known
environmental test failures (`CONTEXT.md:508-517`).

---

## 3. Current Architecture

### 3.1 The four layers, with the real call chain

```
                         ┌──────────────────────────────────────────┐
  user prompt ──────────►│ A. INTERACTIVE AGENT LOOP   (live)       │
                         │  __main__.py:1132 main()                 │
                         │  entry.py:120   run_mode()               │
                         │  composition.py:161 CompositionRoot      │
                         │  runtime.py:321 AgentRuntime.run_turn()  │
                         │  stateless.py:204 WispAgentCore.turn()   │
                         │  stateless.py:346   for iteration in ... │
                         │  tool_executor.py:653 ToolExecutor.exec()│
                         │  → 42 tools → host / sandbox             │
                         └───────────────────┬──────────────────────┘
                                             │  (no edge)
                         ┌───────────────────▼──────────────────────┐
                         │ B. DURABLE GRAPH ENGINE   (CLI/SDK only) │
                         │  graph/executor.py:264 GraphExecutor     │
                         │  graph/scheduler.py:25 ready_nodes()     │
                         │  graph/store.py:22  SQLite tables        │
                         │  graph/audit.py:72  hash chain           │
                         └──────────────────────────────────────────┘

   UNWIRED, COMPLETE, TESTED:
     C. core/graph/loop.py     — phase loop + OscillationTrap      (tests only)
     D. runs/, contracts/, trace/  — durable run + envelopes       (CLI/tests only)
```

### 3.2 Component register

| Component | File | Class / function | Responsibility | State owned | Persistence | Authority | Tests |
|---|---|---|---|---|---|---|---|
| Core turn engine | `core/stateless.py:204` | `WispAgentCore.turn` | One agent turn; provider rounds; tool dispatch | none (nominally) | none | yields events; delegates effects | `test_agent_runtime.py` |
| Turn inner loop | `core/stateless.py:315` | `_turn_inner` | `for iteration in range(max_iterations)` | local `messages` list | none | — | as above |
| Runtime / sessions | `core/runtime.py:321` | `AgentRuntime` | session CRUD, locks, core cache, persistence | `_session_cores`, `_session_locks`, `_steering_inbox`, `_approval_states` | `store.save_session`, `session_events` | owns session lifetime | `test_agent_runtime.py` |
| Events | `core/events.py:55` | `AgentEvent` + `EventType` (13) + `OutcomeClass` (8) | in-flight event vocabulary + outcome taxonomy | none | none | **single outcome authority** | `test_outcome_classification_authority.py` |
| Transport | `transport/cli.py`, `headless.py`, `websocket.py` | `CLITransport` etc. | render events, gather approval | buffers | none | presentation only | `test_cli_transport*.py` |
| Tool lifecycle | `tool_executor.py:653` | `ToolExecutor.execute` | 19-gate tool call pipeline | repeat cache, breakers, pools | `.wisp/audit.jsonl`, `audit_log` | **the agent-path choke point** | `test_tool_executor_bugs.py` |
| Tools | `tools/registry.py` | `TOOL_SCHEMAS`/`TOOL_IMPLS` (42) | tool definitions + impls | none | none | none (executor authorizes) | `test_tool_*.py` |
| Authorization | `auth/decision.py:42` | `authorize()` | 6-layer narrowing | none (pure) | none | decision only | `test_auth_decision.py` |
| Mode policy | `infra/security.py:190` | `SecurityPolicy.check` | 4-layer mode engine + hooks | none (pure) | none | decision only | `test_security*.py` |
| Protected paths | `pathsec.py:40` | `is_protected_path` | canonical protected-path predicate | none | none | canonical predicate | `test_protected_path_guard.py` |
| Graph executor | `graph/executor.py:264` | `GraphExecutor._drive` | single-threaded async DAG drive | `results`, `attempts`, `taken`, `_cancelled` | `graph_*` tables | run-level authority | `test_graph_engine.py` |
| Graph scheduler | `graph/scheduler.py:25` | `ready_nodes` | deterministic readiness | `SchedulerState` | via store | — | `test_graph_invariants.py` |
| Graph store | `graph/store.py:132` | `GraphStore` | durable run/node/artifact/event state | — | SQLite `.wisp/wisp.db` | persistence authority | `test_graph_engine.py` |
| Graph audit | `graph/audit.py:72` | hash chain | immutable provenance | — | `ImmutableAuditTrail` | audit | `test_graph_audit.py` |
| Planner (graph) | `graph/planner.py:82` | `propose` → `compile_ir` | model → IR → validated `Graph` | proposal store | `graph_proposals` | gated by `narrow_ir` + approve | `test_graph_planner.py` |
| Verifier (graph) | `graph/verifier.py:22` | `normalize_verdict` + 4 gates | verdict normalization + deterministic gates | none | node row / events | verdict only | `test_graph_verification.py` |
| Subagents | `multi_agent/subagent_orchestrator.py:711` | `SubagentOrchestrator.run` | delegation lifecycle | entries, telemetry rings | `.wisp/subagent_results.jsonl` | in-process, shares executor | `test_subagent_*.py` |
| Verification floor | `core/verification.py:119` | `VerificationFloorGuard` | completion gate | steps, verify flags | none | blocks `done` | `test_verification_loop.py` |
| Context assembly | `context_assembler.py:329` | `ContextAssembler.build` | priority-sorted prompt sections | 16-entry LRU | none | advisory | `test_context_assembler*.py` |
| Repo intelligence | `repo_map.py:197` | `RepoMap.build` | file/symbol index + PageRank | cache | `.wisp/repo_map.json` | advisory | `test_repo_map_*.py` |
| Durable runs | `runs/store.py:53` | `SQLiteRunStore` | run state machine | — | `background_runs` etc. | **unwired** | `test_runs_*.py` |
| Contracts | `contracts/*` | `CanonicalEvent` etc. | versioned wire envelopes | — | none | **unwired** | `test_contracts_*.py` |
| Trace | `trace/store.py:23` | `SQLiteTraceStore` | span recording | — | `trace_spans` | **unwired** | `test_trace_*.py` |
| Phase loop | `core/graph/loop.py:128` | `ExecutionGraph` | OBSERVE→…→TERMINATE | phase state | none | **unwired** | `test_architectural_upgrade.py` |

---

## 4. Actual Execution Flow

Traced from source, not documentation.

```
1  pyproject.toml:35                 console_script "wisp" -> wisp.__main__:main
2  __main__.py:1132  main()          argv parse + subcommand dispatch
3  __main__.py:121   cmd_run()       -> entry.run_mode("cli")
4  entry.py:120      run_mode()      config overrides; build root
5  entry.py:161      CompositionRoot(config)   wires store/security/executor/runtime
6  entry.py:765      _run_single_prompt()      get_or_create_session (:771)
7  entry.py:802      runtime.run_turn(session, prompt, approval_handler=transport.approve)
     [REPL: entry.py:315 -> cli/repl.py:633 -> :638 runtime.run_turn]
8  runtime.py:321    AgentRuntime.run_turn()   per-session lock (:362)
                                              crash replay if last turn incomplete (:365-376)
                                              compact (:379) ; prune (:388)
                                              append user message (:401)
                                              core = _get_core(sid) (:411)
9  runtime.py:439    core.turn(session, prompt, approval_handler, steering_drain)
10 stateless.py:204   WispAgentCore.turn()     turn_deadline contextvar (:224)
                                              boot seed (:247-259)
                                              system prompt (:269)
                                              tools (:273)
                                              max_iterations (:296)
11 stateless.py:299   async with asyncio.timeout(turn_timeout)
12 stateless.py:346   for iteration in range(max_iterations)     <-- THE MAIN LOOP
13 stateless.py:371     prune messages
14 stateless.py:377     guarded_provider_stream(...)   provider round-trip
15 stateless.py:432-641 normalize + intake-id + role/schema/gate/extension checks
16 stateless.py:479,585 ApprovalGate.check_decision(...)
17 stateless.py:812     _execute_tool(...)  ->  ToolExecutor.execute (1855)
18 stateless.py:840-883 append assistant message + role:"tool" messages
19 stateless.py:887-897 drain steering
20 stateless.py:714     no tool calls -> termination path
21 stateless.py:744     guard.rejection()  -> nudge + continue (:766-768)
22 stateless.py:771     guard.resolved()   -> auto-capture skill
23 stateless.py:783     done_event()       -> return
24 stateless.py:899-942 max_iterations exhausted -> tool-less wrap-up call, or CODE_ITERATION_BUDGET
25 runtime.py:499       turn_succeeded = saw_done and not saw_fatal_error
26 runtime.py:517-594   finally: serialize exchanges, persist, telemetry
27 runtime.py:613       _persist_turn_state() -> session_repo DONE/ERROR + save_session
```

**Stage-by-stage, with authority and persistence:**

| Stage | Module | Function | Inputs | Outputs | State mutated | Persisted | Authority | Error handling |
|---|---|---|---|---|---|---|---|---|
| Session creation | `core/runtime.py` | `get_or_create_session` | prompt, config | session dict | session registry | `sessions` row | runtime | fail loud |
| Goal interpretation | — | **absent** | raw prompt | raw prompt | — | — | — | — |
| Planning | — | **absent** (model may call `plan_task`) | — | `PlanStore` | plan rows | `task_plans` (empty) | model-initiated | none |
| Provider round | `core/provider_stream.py:75` | `guarded_provider_stream` | messages | stream chunks | retry counters | none | stream guard | retry + backoff, honest truncation |
| Tool dispatch | `tool_executor.py:653` | `execute` | name, args | `tool_result` | repeat cache, breakers | `audit.jsonl` | 19 gates | denial verdicts |
| Observation intake | `core/stateless.py:1914` | `_normalize_tool_result` | raw result | `tool_result` event | `messages` | none | — | — |
| Verification | `core/verification.py:119` | `rejection`/`resolved` | tool results | block/allow | guard steps | none | **blocks `done`** | nudge (bounded) |
| Completion | `core/stateless.py:783` | `done_event` | — | event | — | `done` row | — | — |
| Replan | — | **absent** | — | — | — | — | — | — |

---

## 5. Current Graph Model

### 5.1 What the graph actually is

`wisp/graph/` is a **deterministic, durable execution engine for a statically-compiled task DAG**.
It is *not* a persistent representation of a control loop, because its topology is a **frozen value**.

Evidence:

- `Graph` holds immutable tuples: `tuple[GraphNode, ...]`, `tuple[EdgeMapping, ...]` (`graph/types.py:194-201`).
- `GraphNode` is `@dataclass(frozen=True)` (`graph/types.py:115`).
- The executor **never constructs a `Graph` or `GraphNode`** — grep confirms all construction sites are
  compile-time (`planner.py`, `optimizer_passes.py`, `compat.py`, `reference.py`, `coding_graphs.py`, `dsl.py`).

**The graph is an execution graph, not a dataflow graph.** Dataflow is a secondary optional annotation
(`EdgeMapping.mapping` may be empty, `graph/types.py:160`), while `reason` is mandatory and
validator-rejected if missing (`graph/validator.py:82-83`). Nodes are units of *execution authority*
(agent / function / join / router / verifier / gate / approval).

### 5.2 Node model and identity

`GraphNode` (`graph/types.py:115-132`):
`id, type, contract, function, join_policy, join_param, join_timeout_s, routes, default_route, cycle, config`.

- **Identity = the `id` string.** Uniqueness enforced at `graph/validator.py:56-64`.
- Because the dataclass is frozen, **identity cannot change in place**. A node is "recreated" only by
  constructing a new `GraphNode` at build time.
- **No node is ever created or deleted during execution.**
- Historical versions are not preserved as graph objects; the *run* history is preserved as rows.

### 5.3 Dependencies

`EdgeMapping` (`graph/types.py:148-166`, alias `GraphEdge`): `from_node, to_node, reason, mapping, condition`.

- **Static.** Held in the frozen `Graph` tuple; the executor only reads them
  (`graph/executor.py:430,433,508,695,733,811,843,953,972`).
- **Cannot be added or removed at runtime.** The only edge removal is a compile-time optimizer pass
  (`graph/optimizer_passes.py:32-80`, filtered at `:78-79`).

### 5.4 Readiness — computed, not stored

`ready_nodes(graph, state)` (`graph/scheduler.py:25-44`):

- iterate nodes **sorted by id** (`:33`) — fully deterministic, never heuristic, never model-driven;
- skip anything not `PENDING`; entrypoint always ready (`:36-37`);
- non-entry roots without edges stay pending (`:41`);
- otherwise `_predicates_satisfied` (`:86-108`): conditional-only targets require a taken label and a
  settled-ok predecessor; unconditional targets use the join policy (STREAMING releases per branch
  `:100-101`, else all predecessors terminal `:102-108`).

`SchedulerState` (`graph/scheduler.py:16-22`) holds only `statuses`, `taken`, `consumed`.
**No leases, no heartbeat, no worker identity** — it is a single in-process drive loop.

### 5.5 Every status enum, verbatim

The target brief listed twelve candidate states (`PENDING/READY/CLAIMED/RUNNING/WAITING/SUCCEEDED/
VERIFIED/FAILED/RETRYING/BLOCKED/INVALIDATED/SUPERSEDED/NEEDS_HUMAN`). **Wisp has none of those
vocabularies as a single enum.** What actually exists:

**`NodeStatus`** (`graph/types.py:27-34`) — 7 values:
`PENDING="pending"`, `RUNNING="running"`, `SUCCESS="success"`, `FAILURE="failure"`,
`TIMEOUT="timeout"`, `CANCELLED="cancelled"`, `SKIPPED="skipped"`.
**There is no `READY`** — readiness is computed, not stored.

**`RunStatus` (graph)** (`graph/types.py:37-44`) — 7 values:
`QUEUED`, `RUNNING`, `AWAITING_APPROVAL`, `PAUSED`, `SUCCEEDED`, `FAILED`, `CANCELLED`.
`PAUSED` (`:41`) has **no writer** in the graph subsystem (reachability UNVERIFIED).

**`RunState` (durable runs)** (`runs/record.py:17-25`) — 8 values:
`QUEUED, PLANNING, RUNNING, AWAITING_APPROVAL, PAUSED, SUCCEEDED, FAILED, CANCELLED`, with legal
transitions at `runs/record.py:30-40` and legacy aliases (`pending`→QUEUED, `completed`→SUCCEEDED)
at `:51-54`. **This is a second, divergent run-state vocabulary** — but it is unwired, so the
divergence is latent, not live.

**Proposal statuses** (`graph/planner.py:331-332`) — 9 values:
`PROPOSED, VALIDATED, POLICY_CHECKED, APPROVAL_REQUIRED, APPROVED, INVALID, REJECTED, EXPIRED, EXECUTED`.

**Verifier decisions** — a tuple, not an enum: `VALID_DECISIONS = ("ALLOW","REJECT","RETRY","ESCALATE")`
(`graph/verifier.py:19`).

**`Phase`** (`core/graph/phases.py:22-33`) — 9 values, the closest thing to the target loop:
`INIT, RETRIEVE_CONTEXT, SPECULATIVE_PLAN, AWAIT_APPROVAL, EXECUTE_SANDBOX, VERIFY_DIAGNOSTICS,
REDUCE, RECOVER, TERMINATE`.

### 5.6 First-class vs absent in the graph domain

| Concept | Status | Evidence |
|---|---|---|
| Goal | **ABSENT** | `objective` is only a planner-IR string (`graph/planner.py:54`) |
| Observation | **ABSENT** | no type |
| Evidence | **PARTIAL** — weakly typed `list[str]` | `VerificationResult.evidence` (`graph/types.py:289`), `GateResult.evidence` (`:301`) |
| Verification result | **EXISTS** | `VerificationResult` (`graph/types.py:285-294`) |
| Artifact | **EXISTS** | `GraphArtifact` (`graph/types.py:305-316`) |
| Failure | **EXISTS** | `NodeFailure` (`graph/types.py:273-282`) |
| Hypothesis | **ABSENT** | — |

---

## 6. Current State Machine

### 6.1 System A — the interactive turn (the live one)

```
IDLE
 └─ run_turn() ─────────────► TURN_ACTIVE            (runtime.py:321)
      ├─ provider round ─────► STREAMING             (stateless.py:377)
      │    ├─ tool calls ────► GATING                (stateless.py:479,585)
      │    │     ├─ denied ──► TOOL_DENIED (terminal outcome, no retry)
      │    │     └─ allowed ─► EXECUTING             (tool_executor.py:653)
      │    │           └──────► OBSERVING            (stateless.py:840-883)
      │    └─ no tool calls ─► COMPLETION_CHECK      (stateless.py:714)
      │          ├─ wrote_code ∧ ¬verified ∧ floor unspent ─► NUDGE ─► TURN_ACTIVE
      │          ├─ wrote_code ∧ verified ─► RESOLVED ─► auto-capture
      │          └─ otherwise ─► DONE                (stateless.py:783)
      ├─ max_iterations ─────► BUDGET_WRAPUP ─► DONE or CODE_ITERATION_BUDGET
      └─ timeout ────────────► CODE_TURN_TIMEOUT
```

For each transition:

| Transition | Initiator | Validation | Persistence | Atomicity | Replayable | Crash behavior | Tests |
|---|---|---|---|---|---|---|---|
| IDLE→TURN_ACTIVE | `runtime.run_turn:321` | per-session lock `:362` | `session_events(user_message)` `:403` | row insert | yes | replay if incomplete `:365` | `test_agent_runtime.py` |
| STREAMING→GATING | `stateless.py:432-641` | schema/role/gate checks | none | — | no | lost | `test_agent_runtime.py` |
| GATING→EXECUTING | `ApprovalGate.check_decision:54` | hard DENY never prompts `:89-95` | audit on decision | — | no | lost | `test_approval*.py` |
| EXECUTING→OBSERVING | `stateless.py:840-883` | none | **none** | — | no | **lost** | — |
| →NUDGE | `verification.py:180` | floor unspent | none | — | no | lost | `test_verification_loop.py:110-123` |
| →DONE | `stateless.py:783` | `guard.resolved()` | `session_events(done)` `:636` | row insert | partial | — | `test_verification_contract.py` |

**The critical property: no transition between EXECUTING and DONE is durable.** The turn's tool calls,
results, and observations exist only in the in-memory `messages` list until the `finally` block
(`runtime.py:517-594`) writes a **whole-session snapshot**. There is no per-transition journal.

### 6.2 System B — the durable graph run

```
QUEUED ─► RUNNING ─┬─► SUCCEEDED      (all sinks ok/benign; scheduler.py:126-134)
                   ├─► FAILED
                   ├─► CANCELLED      (operator cancel, executor.py:233-242)
                   └─► AWAITING_APPROVAL ─► RUNNING
   node: PENDING ─► RUNNING ─┬─► SUCCESS
                             ├─► FAILURE ─► PENDING (retry, :636) | FAILURE
                             ├─► TIMEOUT
                             ├─► CANCELLED
                             └─► SKIPPED
```

Every transition is a **direct dict write** inside `_drive` / `_on_settled` / `_resolve_join` /
`_skip_dead`. **There is no single transition API.** Persistence is centralized behind `GraphStore`
(`graph/store.py:132-284`), but **no multi-table transaction** exists — SQLite per-statement, with
`INSERT OR REPLACE` checkpoints (`graph/store.py:228-231`).

### 6.3 System C — the target-shaped loop (unwired)

`core/graph/phases.py:22-33` defines exactly the phase vocabulary the target architecture describes,
and `core/graph/loop.py:128` (`ExecutionGraph`) drives it with `max_iterations=25` (`:33,152-162`),
reverting files and entering `RECOVER` on oscillation (`:180-196`).

It is disowned in its own docstring: `core/graph/__init__.py:2-5` — *"Experimental — NOT wired into
`WispAgentCore.turn` ... delete if no caller appears."* Verified: the **only** importer is
`tests/test_architectural_upgrade.py`. Its config flag `graph_oscillation_guard` is defined
(`config.py:256,544,799-800`) and **never read by any loop**.

### 6.4 Target Persistent Graph Loop state machine

See `WISP_GRAPH_DOMAIN_MODEL.md` §5 and `WISP_TARGET_ARCHITECTURE.md` §5. Summary:

```
PENDING ─► READY ─► CLAIMED ─► RUNNING ─┬─► OBSERVED ─► VERIFYING ─┬─► SUCCEEDED ─► (dependents READY)
                                        │                          ├─► FAILED
                                        │                          └─► INCONCLUSIVE
                                        ├─► WAITING  (dependency / approval / human)
                                        ├─► BLOCKED  (predecessor failed)
                                        └─► CANCELLED
        any ─► INVALIDATED  (evidence invalidated by a later mutation)
        any ─► SUPERSEDED   (replanned replacement exists)
```

The three differences that matter: **`READY` is materialized** (not recomputed), **`OBSERVED` and
`VERIFYING` are distinct from `RUNNING`** (execution does not imply verification), and
**`INVALIDATED`/`SUPERSEDED` exist at all** (the graph can change its mind).

---

## 7. Direct Mutation Inventory

### 7.1 Graph state (System B)

| Mutation | File:LINE | Function | Caller | Validated? | Persisted? | LLM-reachable? |
|---|---|---|---|---|---|---|
| status→RUNNING | `graph/executor.py:309` | `launch` | `_drive:484` | timeout finite `:285` | `node_run:313` | indirect |
| status→PENDING (retry) | `graph/executor.py:636` | `_on_settled` | `settle_one:380` | retry policy `:611-615` | `node_run:637` | no |
| status→result.status | `graph/executor.py:643` | `_on_settled` | `settle_one` | `_cap_result:592` | `node_run:688` | no |
| status→SKIPPED (upstream fail) | `graph/executor.py:436` | `_drive` | loop | `blocked_by_failure:422` | `node_run:442` | no |
| status→SKIPPED (dead) | `graph/executor.py:858,867` | `_skip_dead` | `_drive:501` | terminal preds `:864` | **no** | no |
| status→SUCCESS/CANCELLED (approval) | `graph/executor.py:471` | `_drive` | loop | explicit `is True:454` | **no** | operator |
| status→FAILURE (bad timeout) | `graph/executor.py:286` | `launch` | `_drive:484` | `_finite_timeout:285` | **no** | no |
| status→TIMEOUT (join) | `graph/executor.py:751` | `_resolve_join` | `_drive:481` | join_timeout `:737` | **no** | no |
| status→SUCCESS (join) | `graph/executor.py:779` | `_resolve_join` | `_drive:481` | `evaluate_join:771` | **no** | no |
| status→FAILURE (join) | `graph/executor.py:791` | `_resolve_join` | `_drive:481` | `settled_all:788` | **no** | no |
| status→FAILURE (cycle bound) | `graph/executor.py:707` | `_on_settled` | `settle_one` | `_cycle_bound:706` | **no** | **yes (verdict)** |
| status→PENDING (correction replay) | `graph/executor.py:716` | `_on_settled` | `settle_one` | `_in_cycle:703` | **no** | **yes** |
| `results.pop` (replay) | `graph/executor.py:717` | `_on_settled` | `settle_one` | cycle membership `:703` | **no** | **yes** |
| `taken.pop` (replay) | `graph/executor.py:718` | `_on_settled` | `settle_one` | `:703` | **no** | **yes** |
| `taken[node]=label` | `graph/executor.py:646` | `_on_settled` | `settle_one` | label cap `:917-923` | checkpoint `:491` | **yes (verdict/router)** |
| `attempts[nid]=n` | `graph/executor.py:308` | `launch` | `_drive` | monotonic | `node_run` | no |
| `results[nid]=…` | `graph/executor.py:287,302,306,437,472,642,708,752,780,792,859,869` | multiple | drive | `_cap_result` | `node_run` | no |
| `_approvals.pop(nid)` | `graph/executor.py:453` | `_drive` | loop | `is True` | no | no |
| `_cancelled.add` | `graph/executor.py:234` | `cancel` | CLI/SDK | terminal guard `:238` | run status `:239` | no |
| `_forget_run` (reclaim) | `graph/executor.py:256-261` | `_forget_run` | `_drive:549` | terminal only | no | no |
| stale attempt→cancelled | `graph/executor.py:372-375` | `settle_one` | — | generation check `:360` | `node_run:372` | no |
| checkpoint write | `graph/executor.py:458,491` | `_drive` | loop | `_snapshot:992` | SQLite | no |
| run status transitions | `graph/executor.py:141,226,239,398,409,456,539` | `run/resume/cancel/_drive` | — | terminal vocab `:238` | SQLite | no |
| node_run rows | `graph/executor.py:313,372,442,637,688` | — | — | `_cap_result` | SQLite | no |
| events | `graph/executor.py:877-890` | `_event` | everywhere | `scrub:880` | SQLite | no |
| **node INSERT (compile)** | `graph/optimizer_passes.py:346-367` | `_try_insert` | `verification_pass:254` | `_profile_ok:370-404` | proposal/IR | **yes (planner)** |
| **edge REMOVE (compile)** | `graph/optimizer_passes.py:78-79` | `dependency_pass` | `optimize_graph:220` | `_removable:83-105` | proposal/IR | **yes** |
| **concurrency clamp (compile)** | `graph/optimizer_passes.py:481-488` | `resource_pass` | `optimize_graph` | `_wall_safe:492` | proposal/IR | no |
| graph build (IR) | `graph/planner.py:150` | `compile_ir` | `propose`/`execute_proposal` | `validate_graph:156` | proposal | **yes** |
| proposal status | `graph/store.py:272-276` | `set_proposal_status` | CLI `:302` | status check `:286` | SQLite | no |

### 7.2 Session / turn state (System A)

| Mutation | File:LINE | Function | Persisted? | LLM-reachable? |
|---|---|---|---|---|
| append user message | `core/runtime.py:401` | `run_turn` | `session_events` | no |
| append assistant message | `core/stateless.py:859` | `_turn_inner` | snapshot only | model output |
| append tool messages | `core/stateless.py:861-883` | `_turn_inner` | snapshot only | model-triggered |
| verification nudge append | `core/stateless.py:766` | `_turn_inner` | snapshot only | no |
| budget nudge append | `core/stateless.py:909` | `_turn_inner` | snapshot only | no |
| steering append | `core/stateless.py:896` | `_turn_inner` | snapshot only | external |
| compaction rewrites messages | `core/runtime.py:914-916` | `maybe_compact` | snapshot | no |
| `_touched_files` | `core/runtime.py:665` | `_persist_turn_state` | snapshot | no |
| `_turn_counts` | `core/runtime.py:678` | `_record_session_memory` | agent memory | no |
| `_approval_states` | `core/runtime.py:710` | run_turn | no | approval flow |
| `_steering_inbox` | `core/runtime.py:789` | run_turn | no | external |
| session event INSERT | `core/session_repo.py:26` | `append` | SQLite | no |
| memory upsert | `core/runtime.py:700` | `_record_session_memory` | `~/.config/wisp/` | tool `remember` |

### 7.3 Answers to the mutation questions

- **How many direct mutation sites exist?** Graph: **~30 distinct sites** (7.1), of which **11 are
  unpersisted**. Turn/session: **~13 sites** (7.2), of which **only 3 are durable**.
- **Which are centralized?** *Persistence* is centralized (`GraphStore`, `SessionRepository`).
  *Semantics* are not: there is no transition API in either system.
- **Which are distributed?** All node-status writes in `graph/executor.py`; all message writes in
  `core/stateless.py`.
- **Which bypass validation?** The 11 unpersisted graph status writes (`graph/executor.py:436,471,751,
  779,791,858,867` and the `_skip_dead` family) bypass persistence entirely; `_approvals.pop` and
  `_cancelled.add` bypass any recorded provenance.
- **Which can be invoked by an LLM-driven path?** Only five: `taken[node]=label` (`:646`) and the four
  correction-cycle mutations (`:707,716,717,718`). **The model cannot add, remove, or rewire nodes.**
- **Which can be triggered by subagents?** None directly — subagents cannot reach `wisp/graph/`
  (no import edge; `multi_agent/dag.py` is a separate, disjoint DAG).
- **Which are durable?** `graph_runs`, `graph_node_runs`, `graph_artifacts`, `graph_events`,
  `graph_checkpoints`, `graph_proposals`, plus `sessions` / `session_events` / `audit_log`.
- **Which exist only in memory?** All of `SchedulerState`, `results`, `attempts`, `cycle_counts`,
  `_cancelled`, `_approvals`, `_join_wait`, `_join_wait_reason`, and the entire in-flight `messages`
  list for the current turn.

---

## 8. Authority Model

### 8.1 The agent tool path (well-governed)

`ToolExecutor.execute` (`tool_executor.py:653-980`) applies **19 gates in order**:

| # | Gate | Location |
|---|---|---|
| 1 | Dangerous-command block (early) | `:674-679` → `_utils.py:76` |
| 2 | Policy hard-DENY | `:686-699` (`infra/security.py:66-79`) |
| 3 | Layered `authorize()` | `:701-722` (`auth/decision.py:42`) |
| 4 | Repeat-call guard | `:724-733` |
| 5 | Fetch-failure breaker | `:735-742` |
| 6 | Pre-tool hooks | `:744-749` |
| 7 | Plan-mode guard | `:751-757` |
| 8 | Dangerous-command (2nd copy) | `:759-765` |
| 9 | Permission-mode guard | `:767-773` |
| 10 | Approval gating | `:775-845` |
| 11 | Event pre-hooks (PRE_BASH/PRE_FILE_WRITE) | `:847-859` |
| 12 | Dispatch/execute | `:861-921` |
| 13 | Audit | `:923-946` |
| 14 | Metrics | `:948-949` |
| 15 | Skill capture (depth 0) | `:951-960` |
| 16 | Repeat-cache record | `:962-969` |
| 17 | Fetch-breaker outcome | `:971-975` |
| 18 | Post-tool hooks | `:977-978` |
| 19 | Yield `tool_result` | `:980` |

With no executor wired, the fallback is **risk-gated to reads only** (`core/stateless.py:1861-1878`).

### 8.2 `authorize()` — 6 layers

`auth/decision.py:42-148`, returning `AuthorizationDecision` (`:23-30`):
`allowed, reason, controlling_layer, approval_required, obligations`.

| Layer | Owns | Location |
|---|---|---|
| L0 | org policy bundle (approval matrix, MCP allowlist) | `:47-67` |
| L1 | principal capabilities | `:69-73` |
| L2 | workspace trust (QUARANTINED/READ_ONLY) | `:75-85` |
| L3 | risk vs sensitivity | `:87-93` |
| L4 | **argument scan** (protected paths) | `:95-108` |
| L5 | approval requirement from permission mode | `:110-148` |

**Call sites:** `tool_executor.py:707` (agent path, per call); `tools/registry.py:938`
(direct `execute_tool`, fail-closed). **The REST server does not call it** — `server/deps.py:385-437`
consults only `SecurityPolicy.check`.

### 8.3 The two decision models are disjoint, not ranked

| Model | Layers | Owns |
|---|---|---|
| `auth/decision.authorize()` | 6 | capability, sensitivity, **argument scan**, org policy, approval |
| `infra/security.SecurityPolicy.check()` | 4 (`infra/security.py:190-224`) | mode→tool mapping (`infra/policy_engine.py:144`), hooks, trust, approval |

They are **not subset-related**. Rules living only in `authorize()` — L4 protected paths, L1
capabilities, L0 org policy — are **invisible to REST**. The agent composes *both*
(`policy_hard_deny` + `authorize()` + approval); REST composes *one*. This is the measured,
ratcheted divergence `tests/test_authorization_parity.py` pins
(`PHASE_10_AUTHORIZATION_PARITY.md`, `CONTEXT.md:109-153`).

### 8.4 The authority table

| Component | May mutate | Gate | Persisted | LLM-reachable |
|---|---|---|---|---|
| `write_file`/`edit_file`/`fs_mutate` | files | authorize L2/L4 + approval | `audit.jsonl` | yes |
| `run_bash`/`exec_sandbox` | processes | danger ×2 + hard-deny + approval + sandbox | audit | yes |
| git tools | git | hard-deny + forced approval | audit | yes |
| `rewind`/`git_checkpoint` | files | always-gated + hook-dir guard | **in-memory only** | yes |
| `remember` | memory JSON | authorize + approval (ask_all only) | yes | yes |
| `capture_skill` | `SKILL.md` | authorize + write-tools | yes | yes |
| spawn/fanout/orchestrate | child agents | authorize + approval + depth/branch caps | audit | yes |
| MCP tools | arbitrary external | forced approval | audit | yes |
| REST `/api/context` | `.wisp/rules.md` | **none** (API key only) | yes | no (HTTP) |
| REST `/api/git/commit` | git | **none** (API key only) | yes | no (HTTP) |
| REST `DELETE /api/hooks` | hooks | **none** | yes | no (HTTP) |
| Org policy L0 | — | **never invoked** | n/a | n/a |
| `derive_subagent` | — | **never called** | n/a | n/a |
| Graph mutation | graph rows | `validate_graph` + fingerprint | SQLite | **no tool exposes it** |

### 8.5 Where uncontrolled mutation can occur

1. **Org policy unwired.** `policy=` is passed at neither production construction site
   (`composition.py:134-142` verified; `acp_session.py:208`). L0 (`auth/decision.py:47-67`) is
   therefore always a no-op. `load_local`/`load_managed` callers are `policy/cli.py:50,134` + tests;
   `merge_all` has no runtime caller. `ReproManifest.policy_bundle_id` has no producer, and
   `tests/test_m4_governance_wiring.py:196` is a **tripwire asserting it stays unset**.
2. **Subagent narrowing unused.** `derive_subagent` (`auth/principal.py:62`) is called **only in
   tests** (`test_auth_principal.py`, `test_auth_decision.py`). The executor authorizes every call
   with `local_principal(...)` (`tool_executor.py:708`), which has `capabilities=None` = **unbounded**.
3. **REST/agent parity gap.** Ungated mutating routes: `routes/context.py:38-55`,
   `routes/git.py:67-81`, `routes/hooks.py:162-173`.
4. **Registry security unwired.** `execute_tool(security_policy=…)` (`tools/registry.py:907,962`) has
   **no caller**; `ToolRegistry.execute` is production-unused.
5. **`_skip_authorize=True`** (`tool_executor.py:1345`) — trusts the caller's prior authorize.
6. **Docker sandbox tier skips the danger-list** (`sandbox/__init__.py:168-200`; only cwd containment).
7. **In-memory rollback.** `CheckpointStore` is session-scoped and non-durable
   (`tools/checkpoints.py:6-10`); process death loses all checkpoints.
8. **Subagents are not narrowed.** One shared `ToolExecutor` (`composition.py:172`), shared workspace
   by default (`task.py:122`), shared SQLite store.

**Desired authority model** (target): see `WISP_PROPOSAL_PROTOCOL.md`. Reasoning emits proposals;
a single validator decides legality; execution emits observations; verification emits evidence;
only a graph controller writes state.

---

## 9. Planner Analysis

**Verdict: PARTIALLY SEPARATED — and in the live loop, absent.**

| Planner | Location | Nature | In the live turn path? |
|---|---|---|---|
| `wisp/graph/planner.py` | `:82-107` propose → `compile_ir:135-159` | model → IR → deterministic compiler, gated by `narrow_ir:162-246`, `validate_graph:156`, explicit `approve is True:299` | **NO** |
| `wisp/planner.py` | `Task` `:66`, `PlanStore` | model-invoked `plan_task`/`mark_step_done`/`update_plan` (`tools/plan.py:12,38,54`; schemas `registry.py:535,550,565`) | tools are live; **plan state never reaches the prompt** |
| `wisp/coding.py` | `TaskContext` `:40`, `decide_strategy:115` | keyword strategy selection → graph template | via REPL graph template only (`cli/repl.py:803`) |
| `multi_agent/dag.py` | `TaskDAG:37`, `DAGScheduler:146` | Kahn validation, topo levels | via `orchestrate_dag` tool |

**The decisive evidence that planning is not wired into the live loop:**

`_build_system_prompt` calls `PromptContext.from_legacy(...)` **without** `active_plan` or `plan_mode`
(`core/stateless.py:1238-1247`). `PlanState` (`context_assembler.py:199`) and the
`## PLAN MODE ACTIVE` prose (`context_assembler.py:409-424`) therefore **never enter the core
prompt**. `plan_mode` survives only as a write-block in `tool_executor._check_plan_mode`
(`tool_executor.py:1167-1169`).

**There is no goal-interpretation step.** The raw prompt is appended to `messages`
(`core/stateless.py:266`) and sent to the provider (`:377`). The only rewrite in the codebase is
`AgentAdapter._expand_continuation` (`transport/cli.py:591`), which expands bare "continue"-type words.

---

## 10. Executor Analysis

| Executor | Location | Concurrency | Notes |
|---|---|---|---|
| Turn loop | `core/stateless.py:346` | sequential iterations | model-driven; tools may run in parallel within an iteration |
| Tool executor | `tool_executor.py:653` | thread pools (`_tool_pool`, `_network_pool`) | 19 gates; bounded pools |
| Graph executor | `graph/executor.py:264-550` | single-threaded async drive; each node an `asyncio.Task` `:324`; global semaphore `:270-271`; per-provider semaphores `:276-280` | `settle_one` uses `asyncio.wait(FIRST_COMPLETED, timeout=0.5)` `:327-385` |
| Node kinds | `graph/executor.py:553-602` | FUNCTION/GATE/ROUTER-with-function run **local registered functions, never a model** (`:557-575`); AGENT/VERIFIER/ROUTER-classifier go through the injected `runner` (`:577`) → `SubagentNodeRunner` (`graph/runner.py:27-85`) |

**Does the executor change graph semantics?** In System B, **no** — it only mutates status maps, never
topology. In System A, the "executor" *is* the model, so semantics and execution are the same act.

**Idempotency:** graph nodes carry an idempotency key (`graph/executor.py:295`); the turn loop has
**none** (`repeat_guard` at `tool_executor.py:729-731` is in-memory and per-turn).

---

## 11. Verifier Analysis

**Verdict: INTERMIXED in the live loop; PARTIALLY SEPARATED in the graph.**

### 11.1 The live loop plans, acts, and judges itself

A single `WispAgentCore._turn_inner` invocation:
- plans (decides the next action from the message list),
- acts (calls tools via `stateless.py:812`),
- judges (`guard.rejection()` `:744`, `guard.resolved()` `:771`).

`tests/test_verification_loop.py:110-123` (`test_edit_then_finish_is_nudged_not_done`) **pins this
exact same-invocation loop**. The guard is deterministic bookkeeping over the same model's own tool
results; no second model or context judges.

### 11.2 The graph has a separate node — but the same model

- `verifier.py:1-10` states verification "must be ... independent from generation (different context,
  ideally different model)".
- `NodeType.VERIFIER` (`graph/types.py:22`) is a genuinely distinct node; OPT-002 inserts one
  (`graph/optimizer_passes.py:221-270`).
- **But** the verifier's judgment is produced by the same injected `runner` used for agent nodes
  (`graph/executor.py:577,585-591`), and `_try_insert` may reuse the graph's model/provider
  (`optimizer_passes.py:353-354`). Nothing enforces a different model at runtime.

**Concrete example of the intermixing:** `graph/executor.py:585-591` validates the *shape* of the
verdict only. Whether the verdict is *right* is the model's word.

---

## 12. Evidence Analysis

### 12.1 The graph layer has real provenance

`GraphArtifact` (`graph/types.py:305-316`) records
`artifact_id, run_id, node_run_id, type, content_hash, uri, producer, created_at`; persisted in
`graph_artifacts` + `.wisp/artifacts/<type>-<hash>.json` (`graph/artifacts.py:56-92`), readable via a
**hash-verified** `ArtifactStore.get` (`:94-110`) and announced by `graph.artifact_created` events.

Provenance is genuine: producer + node_run + run + timestamp. Evidence **can go stale**, and staleness
is handled correctly — the turn guard nulls prior evidence on any later mutation
(`core/verification.py:149`), pinned by `test_verification_contract.py:72-78`.

### 12.2 The turn layer conflates every concept

For a successful **turn**, the only "evidence" is the tool-result text in session history
(`core/stateless.py:823-837`): no artifact, no hash, no producer record. `ChangeTracker` records file
mutations with timestamp/agent_id/sizes (`change_tracker.py:19-37,49-106`) but is **not wired to
verification**.

| Concept | Graph layer | Turn layer |
|---|---|---|
| Claim | model output | model output |
| Observation | node result | raw `role:"tool"` string |
| Evidence | `GraphArtifact` + hash | **conflated with observation** |
| Artifact | `GraphArtifact` | absent |
| Verification result | `VerificationResult` | **absent** |
| Failure | `NodeFailure` | `OutcomeClass` on a status string |

**Where the conflation lives:** `core/stateless.py:1914` (`_normalize_tool_result`) — a raw tool
result string becomes the observation *and* the evidence *and* the verification input, with no
intervening record.

### 12.3 The one genuine single authority

`OutcomeClass` (`core/events.py:280-290`) — 8 values: `SUCCESS, ERROR, DENIAL, POLICY_DENIAL,
TIMEOUT, CANCELLATION, INVALID, UNKNOWN` — with `OUTCOME_BY_STATUS` (`:296-304`),
`TERMINAL_OUTCOME_CLASSES` (`:308-314`), `classify_result` (`:366-372`), `is_error_outcome` (`:375`),
`is_terminal_outcome` (`:380`), `is_denial_text`/`is_denial_outcome` (`:385-404`).
Enforced as the sole classifier by an AST ban in `tests/test_outcome_classification_authority.py`
(`:264-279,362-378`). This is the model to imitate elsewhere.

---

## 13. Recovery Analysis

| Mechanism | Exists? | Detection point | Decision maker | Budget | State transition | Evidence | Termination | Tests |
|---|---|---|---|---|---|---|---|---|
| Provider stream retry | YES | transient 429/5xx/socket, empty, stall | `provider_stream.py:75-102` | `max_attempts` + jittered backoff | none (retry in place) | stall event `:261-268` | attempts exhausted → honest truncation | `test_ollama_client_retry.py` |
| Turn-level transient retry | YES | exception in provider stream | `stateless.py:650-702` | `iteration < 2` | none | error event | → fatal error | — |
| Graph node retry | YES | FAILED node, idempotent | `graph/executor.py:611-641` | `retry_policy.max_attempts` (default 1) | `FAILURE`→`PENDING` `:636` | node row | attempts exhausted | `test_graph_engine.py` |
| Correction-edge replay | YES | conditional label on cycle member | `graph/executor.py:692-727` | `cycle.max_iterations` ≤5 | `→PENDING` + results pop | node row | cycle bound `:706` | `test_graph_invariants.py` |
| Verifier RETRY decision | YES | verifier verdict | `_label_for:908-926` | — | routed edge | verdict | — | `test_graph_verification.py` |
| File rollback / rewind | YES | agent calls `tool_rewind` | agent | 20/file, 10 MB/ws | restore | **in-memory only** | — | `test_checkpoints.py` |
| Oscillation revert | **EXISTS BUT UNWIRED** | 1-cycle / 2-cycle diff hash | `core/graph/loop.py:112-125` | `max_iterations=25` | `RECOVER` `:180-196` | none | — | `test_architectural_upgrade.py:81-90` |
| Graph resume | YES | process restart | `graph/executor.py:149-231` | graph-hash pin | resume | checkpoints | — | `security/test_graph_resume.py` |
| Turn crash-recovery replay | YES (partial) | last event ≠ DONE | `core/runtime.py:363-373` | — | reset messages | **only `user_message` events** | — | `test_journal_recovery.py` |
| Subagent timeout retry | YES | timeout | `subagent_orchestrator.py:852-880` | 1 extra round ×1.5 | — | — | — | `test_subagent_orchestrator.py` |
| Subagent transient retry | YES | transient, not denial | `subagent_orchestrator.py:996-1113` | `max_retries` ≤5 | — | — | — | as above |
| Subagent schema repair | YES | invalid schema | `subagent_orchestrator.py:1464-1500` | shared budget | — | — | unsafe → refuse | `test_schema_validator.py` |
| Circuit breaker (provider) | YES | consecutive failures | `infra/circuit_breaker.py:24-38` | threshold 5, recovery 30 s | open/closed | — | — | `test_stateless_circuit_breaker.py` |
| Circuit breaker (subagent) | **EXISTS BUT UNWIRED** | 3 failures / 120 s | `multi_agent/_circuit_breaker.py:15-38` | manual reset | — | — | — | none |
| **Local replan** | **ABSENT** | — | — | — | — | — | — | — |
| **Global replan** | **ABSENT** | — | — | — | — | — | — | — |
| Diagnostic task | PARTIAL | model-invoked | `tools/diagnose.py:13-21`, `error_diagnosis.py:219-290` | — | — | — | — | — |
| Human escalation | PARTIAL | verifier ESCALATE / APPROVAL node | `graph/verifier.py:19`, `graph/executor.py:449-478` | — | `AWAITING_APPROVAL` | — | — | `test_graph_terminality.py` |

### 13.1 Failure taxonomy

| Class | Status | Code |
|---|---|---|
| Transient | YES | `core/transport.py:443-570` (`is_transient_error`/`is_transient_status`); local fallback `stateless.py:655-668` |
| Tool | YES | `classify_result` → ERROR (`core/events.py:366-372`) |
| Implementation / verification / dependency / environment | **PARTIAL** | `NodeFailure.failure_code` is a **free string** (`graph/types.py:273-283`); benchmark codes `TEST_FAILURE`, `OUT_OF_SCOPE_FILE_CHANGE`, `SCHEMA_INVALID`, `POLICY_DENIED` (`graph/verifier.py:39-72`). **No closed vocabulary.** |
| Invalid assumption | **ABSENT** | — |
| Repeated / stagnation | **PARTIAL** | only `VerificationFloorGuard.repeat_count` (`core/verification.py:135-136`) |
| Security / policy | YES | `OutcomeClass.POLICY_DENIAL` (`core/events.py:286`) |

---

## 14. Stagnation Analysis

**Can Wisp detect "doing work but not making progress"? Not in the live path. The detector exists and
is orphaned.**

`OscillationTrap` (`core/graph/loop.py:112-125`) detects exact 1-cycle repeats and 2-cycle
oscillations of **diff hashes** — a genuine progress signal. `ExecutionGraph.run` reverts files and
enters `RECOVER` (`:180-196`). It is exported (`core/graph/__init__.py:15`).

**Verified unwired:** the only importer is `tests/test_architectural_upgrade.py:81-90`.
`config.graph_oscillation_guard` is defined (`config.py:256,544,799-800`) and **never read**.

The live loop's only anti-spin is `SHORT_REPEAT_NUDGE` (`core/verification.py:180-187`) and the
in-memory per-turn `repeat_guard` (`tool_executor.py:729-731`).

**What a progress signal would need** (all four inputs already exist, unconnected):
1. per-turn action digest trail — `guard.steps` (`core/verification.py:131`) exists;
2. verification result *history* — currently **overwritten** (`:151`);
3. artifact/content hashes per turn — `GraphArtifact.content_hash` exists but is not fed to the turn loop;
4. a monotonic progress metric — absent.

---

## 15. Context Engineering Analysis

### 15.1 How the prompt is built

`_build_system_prompt` (`core/stateless.py:1109-1285`) → `ContextAssembler.build`
(`context_assembler.py:329/374`), priority-sorted (`:389-450`, sort at `:499`):

`context_files`(-1) → `default_system`(0) → `workspace`(0) → mandatory_skill/active_plan/plan_mode/
plan_context(1) → role_extra/skills_block/memory_block(2) → project_context/code_index/
recent_summaries/git_context/repo_map(3).

Then per-turn appends: query-relevant files `:1264`, compaction notice `:1269`, operating context
`:1275`, environment `:1281`.

**Purity:** `ContextAssembler.build` is pure over a frozen `PromptContext` but carries a mutable
16-entry LRU (`context_assembler.py:321-324,473-477`). `_build_system_prompt` is **not pure** — it
reads a module global `_SYSTEM_PROMPT_CACHE` (`stateless.py:72,1191`), disk mtimes, git, memory, and
environment every turn. The message list is mutable and mutated mid-turn.

### 15.2 Selection and caps

No embedding/retrieval step in the prompt path. Selection is deterministic (extension + `_SKIP_DIRS`
scan, `repo_map.py:32-65,620-724`) + heuristic (keyword substring scoring, `top_k=5`,
`repo_map.py:415-466`) + model-controlled (file bodies enter via tool calls).

| Cap | Value | Location |
|---|---|---|
| Assembled system prompt | 6000 tokens | `context_assembler.py:49` |
| Repo map section | 1200 tokens | `stateless.py:1472-1475` |
| Boot seed | 8000 chars | `core/context/boot.py:37` |
| Tool payload | 200 KB total / 8 KB historical / 50 KB recent | `context_pruner.py:78-90`; `context_manager.py:37-42` |
| Repo map entries | 200 | `repo_map.py:175` |
| Compaction trigger | **message count**, not tokens | `core/runtime.py:857-868` |

### 15.3 Staleness

System-prompt cache keyed on mtimes of `.wisp/rules.md`, `conventions.md`, memory file, `SESSIONS_FILE`
(`stateless.py:1134-1155`) — memory mtime changes each turn, so effective per-turn invalidation.
RepoMap invalidated on git HEAD change or any tracked mtime newer than cache ts, **sampled over the
first 50 files** (`repo_map.py:781-801`). TTLs: git 2 s, lint 10 s, module summary 30 s
(`stateless.py:1460,1557,1669`); code_index/tree-sitter 30 s; `core/context/repomap` 30 s.

### 15.4 Trust boundary — ABSENT

**No code distinguishes untrusted repository content from trusted system instructions.** Repo text
enters as plain system-prompt sections (`## Codebase Map`, `## Project guidelines`, `## Cross-Session
Memory`) or as `role:"tool"` messages — **no delimiters, no escaping, no provenance tags**.

The only defenses are prose and authorization: grounding prose (`context_assembler.py:67-74`), a skill
guardrail footer (`:459-467`), an agent-role "UNTRUSTED WEB DATA" note (`multi_agent/roles.py:128,215`),
and an eval scenario (`eval/scenarios.py:30-37`). Boot guidelines (AGENTS.md/CLAUDE.md) are injected
with truncation only, no sanitization (`core/context/boot.py:100-118,229-241`).

### 15.5 Compaction — four implementations, one live

| Implementation | Location | Status |
|---|---|---|
| `prune_live_session` (byte budget) | `core/context_manager.py:108`; called `runtime.py:386-390` | **LIVE** |
| `prune_messages` (pure, pre-dispatch) | `core/context_pruner.py`; called `stateless.py:371,921,967` | **LIVE** |
| `Compactor.compact` (LLM, keep_recent=6) | `core/compaction.py`; `runtime.maybe_compact` `:857-911` | **LIVE** |
| 3-tier policy (micro / RollingSummary / full) | `core/context/compactor.py` | **UNWIRED** (tests only) |
| `SemanticCompressor` | `semantic_compressor.py` | reachable only via a deferred import in `infra/session_dto.py:67` (session export), not the turn path |

### 15.6 Graph context — none

**No graph/task-state serialization into the prompt.** Legacy `session["graph_state"]` hydration was
removed (`runtime.py:394-395`). `PlanState` exists in the assembler but is never populated (§9).

---

## 16. Repository Intelligence Analysis

`repo_map.py` produces `RepoMapEntry(path, name, kind, line, signature, importance, dependencies,
summary)` (`:133-153`).

| Aspect | Finding | Evidence |
|---|---|---|
| Indexed | files + symbols + dependency graph + PageRank importance | `repo_map.py:593-618,1761+` |
| Generated | tree-sitter (`_extract_symbols_ts`) with a regex fallback | `repo_map.py` |
| Updated | **on demand only** — no watcher | `build(use_cache, fast_mode)` |
| Queried | `get_relevant_files` (keyword), `get_dependencies`/`get_dependents` | `:415-490` |
| Persisted | `.wisp/repo_map.json` with `_meta{timestamp,git_hash,files,skeleton}` | `:761-844` |
| Authoritative? | **Advisory** | — |

**Critical finding:** the turn path calls `build(use_cache=True, fast_mode=True)`
(`core/stateless.py:1469`), and `fast_mode` short-circuits at `repo_map.py:218-245`. **The turn path
therefore always receives the skeleton — a file-path list with `importance=0.5` and `kind:"file"` —
and the full symbol/PageRank map is never built in the main loop.** Verified on disk: the persisted
`.wisp/repo_map.json` has `_meta.skeleton: true` with 200 entries, all `kind:"file"`.

`code_index.py` / `tree_sitter_index.py` are a **separate** in-memory symbol index (30 s TTL, not
persisted), used by `search_symbols` and `workspace.py:189,382`.
`core/context/repomap.py` is an independent PageRank RepoMap with token-budget binary search —
**UNWIRED** (tests only).

`semantic_index.py` = SQLite `.wisp/semantic_index.db` (`:69`), tables `files`/`chunks`/`embeddings`
(`:97-133`), embeddings via local Ollama `nomic-embed-text` (`:312-336`), cosine similarity in numpy
(`:512-619`). Query path is **model-invoked** (`tools/search.py:50-90`) and the server route
(`server/routes/codebase.py:21-61`) — **not wired into the turn path**. Staleness is handled properly:
mtime diff → `STATE_STALE` → refuses to answer (`semantic_index.py:451-508`).

**How the repo map enters context:** automatically — `_build_repo_map` (`stateless.py:1462-1481`) →
`ContextAssembler` section `repo_map` (priority 3, deduped against code_index at
`context_assembler.py:447-450`). Boot also injects a skeleton (`core/context/boot.py:194-225`).
`PromptContext.code_index` is never populated by `stateless.py` (`:1238-1247`), so the
`code_index_summary` section is **dead in the main path**.

**Target role:** the repo map is the correct *derived* intelligence layer — it should stay advisory,
gain a symbol-level mode on the turn path, and become an input to node proposal rather than a prompt
dump. See `WISP_CONTEXT_ARCHITECTURE.md` §7.

---

## 17. Subagent Analysis

### 17.1 Structure

Two tool families route through `ToolExecutor`: blocking `spawn` (`tool_executor.py:1528`), `fanout`
(`:1895`), `orchestrate_{vote,map_reduce,chain,dag}` (`:1818-1828`); non-blocking `spawn_background`
(`:1720`) → `BackgroundAgentManager.launch` (`multi_agent/background.py:264`); lifecycle
`subagent_{wait,list,result,send,cancel}` (`tools/subagent_tools.py:26-219`).

**A typed contract exists** — `SubagentContract` (`multi_agent/task.py:57-174`) and `SubagentResult`
(`:213-271`). The *tool boundary* is ad-hoc dicts.

`wisp/core/subagent/` is an **empty namespace package** — only stale `.pyc` files for
`coordinator`/`pool`/`protocol`; **zero `.py` sources**, and nothing imports it. Treat as dead bytecode.

### 17.2 The eight dimensions

| Aspect | Current implementation | Structured? | Evidence |
|---|---|---|---|
| Input | `task: str` free text; `output_schema` is output-only | **No** | `multi_agent/task.py:57`; `registry.py:245,281` |
| Context boundary | **Fresh session**, history cleared; resume reloads full history | Implicit | `_runner.py:214-239,654-655` |
| Permissions | Child **inherits parent's permission_mode**; no child approval handler; `auto_approve=False` forced | Partial | `_runner.py:760-768`; `tool_executor.py:1597,1701,2036` |
| Tool narrowing | `_effective_child_tools` ∩ `filter_allowed_for_mode` | Yes | `_runner.py:23-42,487-490` |
| Isolation | **Opt-in** git worktree; otherwise in-process on the parent's loop sharing the parent's `ToolExecutor` | **Weak** | `task.py:122`; `_worktree_manager.py:50-119`; `composition.py:172` |
| Output | `SubagentResult.output` free text; JSON only if caller opts in | Partial | `multi_agent/task.py:213-271` |
| Failure | `success=False`; **prose-marker** retries | **Weak** | `subagent_orchestrator.py:715-718,933,1577` |
| Cancellation | `asyncio.timeout`, 2 s reap join, parent-deadline clamp | Partial | `_runner.py:251`; `:46-84`; `subagent_tools.py:68-79` |
| Fanout bounds | branch 3 / depth 2 / pool 4 / running 8 | Yes | `tool_executor.py:1948-1973`; `background.py:29` |

### 17.3 Silent global mutation paths

1. **Parent files** — `worktree_isolated=False` default; children write directly to the parent tree.
2. **Shared `ToolExecutor`** — one instance for root and all children (`composition.py:172`).
3. **Shared SQLite store** — children create/save sessions in the parent DB (`_runner.py:239,266`).
4. **`SharedContext`** — an **untyped key/value dict** visible to all siblings (`shared_context.py:69`).
5. **Provider cache** — shared per orchestrator (`_runner.py:148`).
6. **Telemetry ring** — one global buffer (`background.py:113`; `composition.py:198`).
7. **Persistence JSONL** — append-only global file (`subagent_orchestrator.py:173-192`).
8. **Git operations** — `git add -A`, `git apply`, `git checkout -f`, branch delete
   (`_worktree_manager.py:225-230,267-298`).
9. **Contract mutation** — `_cache_context` stamped; `progress_callback` overwritten; `_shared_context`
   assigned into caller contracts (`subagent_orchestrator.py:781,1052-1055`; `background.py:356`).
10. **Result objects rewritten post-hoc** (`subagent_orchestrator.py:914,922,928`; `_runner.py:295-305`).
11. **Partial patch application** — non-conflicting partial application is **not transactional**
    (`subagent_orchestrator.py:919-924`; `_worktree_manager.py:144-201`).

### 17.4 Two latent defects found

- **`derive_subagent` is never called in production** (`auth/principal.py:62`) — so a subagent runs with
  `local_principal(...)` (`tool_executor.py:708`), `capabilities=None` = **unbounded**. Subagent
  narrowing is implemented, tested, and inert.
- **DAG node budget is attached then dropped.** `subagent_orchestrator.py:1316` writes
  `task.metadata`, but `SubagentContract` has **no `metadata` field**. `_runner._budget_from_contract`
  reads `getattr(contract, "metadata", None)` (`_runner.py:78`), so it never sees it — contradicting
  the comment at `_runner.py:54-67` which claims the fix landed. (This is the Phase 10 fix F1
  reappearing in a second location.)

### 17.5 Graph coupling

**None.** No `wisp.graph` import exists anywhere in `multi_agent/`. `multi_agent/dag.py` is a
**separate, disjoint DAG** (`TaskDAG:37`, `DAGScheduler:146`). Meanwhile `graph/runner.py:124`
independently constructs its own `SubagentOrchestrator`. **There are two graph systems and two
orchestration paths, and they never meet.**

---

## 18. Memory Analysis

| Class | Where it lives | Lifetime | Scope | Persistence | Mutators | Provenance |
|---|---|---|---|---|---|---|
| Working | `session["messages"]` (`runtime.py:309-317`; `SessionView` `session_view.py:16-68`) | turn/session | per-session | **in-memory only** | runtime, core, `prune_live_session` | none |
| Task | `task_plans` (`task/manager.py:60-63`); `BackgroundAgentEntry._entries` (`background.py:103`) | process/run | per-task | SQLite (**empty**) / memory | TaskManager | partial (task id) |
| Execution history | `sessions.messages` snapshot (`store.py:347`); `session_events` (partial); `audit_log` (`infra/audit.py:222-283`); `.wisp/audit.jsonl`; `.wisp/subagent_results.jsonl` | durable | session/run | SQLite + JSONL | runtime; tool_executor; orchestrator | audit hash chain: yes; snapshots: no |
| Repository knowledge | `.wisp/repo_map.json` (`repo_map.py:200,763`); `.wisp/semantic_index.db` (`semantic_index.py:69`) | durable, regenerated | workspace | files/SQLite | repo_map, semantic_index | only `_meta` git_hash + mtime |
| Decision | `memory.py` (`~/.config/wisp/memory.json`); `AgentMemory` summaries (`~/.config/wisp/agent_memory/sessions.jsonl`); `audit_log` | durable | global/workspace/session | JSON/JSONL | `add_fact`/`upsert` | partial (added/access_count; **no actor**) |
| Verified knowledge | `capture_resolved_skill` → `.wisp/skills/auto/<slug>/SKILL.md` (`skill_capture.py:166-194`), only on RESOLVED turns | durable | workspace | files | skill_capture | task text + date only |
| Failure | `session_events` error rows; `subagent_results.jsonl.error`; compaction `error_context` (`compaction.py:39`) | durable | session/run | SQLite/JSONL | runtime/orchestrator | minimal |

**Note:** `memory.py` uses a **2 s debounced save** (`_SAVE_DEBOUNCE_SECONDS=2.0` `:49`,
`_schedule_save` `:128-153`) — facts added less than 2 s before a crash are lost.

**Nothing in this taxonomy is a graph.** There is no node-level or task-level durable memory that a
control loop could read back.

---

## 19. Persistence Analysis

### 19.1 What is durable today

| Store | Contents | Live rows (verified) |
|---|---|---|
| `sessions` | full transcript snapshot, **overwritten each turn** | populated |
| `session_events` | append-only, **only 3 kinds ever written** | `user_message:33, done:15, error:6` — **zero tool events** |
| `audit_log` | tool decisions + args_summary | 689 rows |
| `.wisp/audit.jsonl` | blocked/allowed decisions | 1618 lines |
| `.wisp/subagent_results.jsonl` | subagent results | 690 lines |
| `.wisp/repo_map.json` | skeleton repo map | 200 entries |
| `graph_*` (6 tables) | run/node/artifact/event/checkpoint/proposal | **absent from the live DB** |
| `background_runs`, `run_transitions`, `idempotency`, `task_plans` | durable run layer | **all empty** |
| `trace_spans` | trace spans | **empty** |

### 19.2 The durable run layer is not on the production path — verified

- `BackgroundAgentManager` is constructed **without** `run_store` at both production sites:
  `composition.py:192` and `tool_executor.py:1666` (verified by direct read).
- With `run_store=None`, `_run_store` stays `None` (`background.py:95`) and every `_persist_*` returns
  early (`background.py:165-166,188-189`); the `Scheduler` is never built (`background.py:98`).
- The production turn path (`entry.py:551/802` → `core/runtime.py:321`) **never imports `wisp.runs`**.
- Confirmed empirically: `background_runs` = 0, `run_transitions` = 0.

The durable run layer is reached only by the `wisp task` CLI (`task/cli.py:39`) and tests.

### 19.3 Other unwired persistence

- `wisp/contracts/` — no production producer/consumer. `from wisp.contracts` appears only in
  `contracts/*` itself, `runs/store.py:13` (one enum), and tests.
- `wisp/trace/` — the only `SQLiteTraceStore` instantiation is `trace/cli.py:30`. `trace_spans` = 0.
  `replay_plan` is explicitly dry-run with no executor (`trace/export.py:24-33`).
- `wisp/runs/compensation.py` — `EditRecord`, `rollback_preview`, `reversibility()`; module docstring
  states *"No tool wiring"*. Referenced only by `runs/__init__.py:11`.
- `ReproManifest` (`runs/repro.py:12-24`) — **no producer**; `policy_bundle_id` is never assigned
  anywhere in the repository (verified by grep), and `tests/test_m4_governance_wiring.py:196` asserts it.

---

## 20. Replay Analysis

**What survives, and what does not:**

| Failure mode | Survives | Lost | Evidence |
|---|---|---|---|
| Process crash mid-turn | `user_message` event; prior session snapshot; audit log | current turn's assistant content, **all tool calls and results**, injected context | `runtime.py:403-408` vs `:517-594` |
| Agent restart | sessions snapshot; audit; repo_map | in-flight turn, core cache, steering | `runtime.py:309-318` |
| Task restart | `wisp task` row (if the CLI was used) | task execution (no executor exists) | `task/manager.py:23-32` |
| Partial execution | tool side effects **on disk** | any record of them until the `finally` persists | `runtime.py:517-594` |
| Tool failure | error `tool_result` in memory only | **durable tool-result event (never written)** | `session.py:45-50` |
| Network failure | provider error event (streamed) | not persisted unless fatal | `runtime.py:490-494,636-640` |
| Timeout | error event; partial messages | unsaved tool sequence | `runtime.py:501-515` |
| Cancellation | `CANCELLED` on `background_runs` **if a run_store were set** | in production: **nothing persisted** | `background.py:549-561` |

**State that exists ONLY in memory:** live `session["messages"]`; `BackgroundAgentManager._entries`;
`AgentRuntime._session_locks` / core cache; the core's message list; `SkillCapture._steps`;
steering queues; `CompactionResult`; run admission counters; **all `CheckpointStore` snapshots**.

**Can Wisp reconstruct execution state from durable records? Partially, and not to the crash point.**
It can rebuild the last *completed* turn from `sessions.messages`. It cannot rebuild the in-flight
turn, because:
- `session_events` contains only `user_message` / `done` / `error` — **no tool events**;
- `Session.apply` **ignores `TOOL_CALL`** (`core/session.py:84-117`), so replay cannot rebuild tool
  execution even if the events existed;
- `background_runs` / `run_transitions` / `idempotency` / `trace_spans` are empty.

**Duplicate-execution risk — real.** If the process dies after a tool's side effect but before the
`finally` block persists (`runtime.py:517-594`), the result is unrecorded. On restart,
`was_last_turn_complete` is false, so the runtime replays `session_events`
(`runtime.py:363-376`) — which contain only the user message — and `session["messages"]` resets to the
bare prompt. The disk side effect is invisible and **the turn re-runs**. There is **no durable
idempotency**: the `idempotency` table is empty; `Scheduler.memoize`/`already_done`
(`runs/scheduler.py:57-64`) is unwired; the only guard is the in-memory per-turn `repeat_guard`.

A stale comment at `runtime.py:596` ("Cache result for idempotency (1h TTL)") has **no code following it**.

---

## 21. Event / State Architecture

| Property | Status |
|---|---|
| Events | **Yes, but in-flight only.** `AgentEvent` is yielded and discarded (`runtime.py:439-451`); no persistence sink. |
| Snapshots | Yes — whole-session JSON overwritten each turn (`store.py:347-375`). |
| State transitions | Present in the graph (`graph_runs`/`graph_node_runs`) and in `RunRecord`, but the latter is unwired. |
| Append-only records | `session_events` (3 kinds), `audit_log`, `.wisp/audit.jsonl`, graph events + hash-chain audit. |
| Execution history | Partial — no tool-level history in the live path. |
| Durable observations | **No.** Observations are raw strings in a mutable snapshot. |
| Replayable state | Graph runs: yes (resume + idempotency). Turns: **no**. |

**Is the graph the source of truth?** For System B, the *persisted rows* are the source of truth and the
in-memory `Graph` is an immutable input. For System A there is no graph at all.

**Comparison against the target model:**

```
TARGET:  Immutable Event History → Materialized Graph State → Current Execution View
WISP(B): Graph (frozen input) + run rows (mutable) + in-memory status maps
WISP(A): (nothing)             → (nothing)               → the message list
```

The minimum evolution is **not** to replace the message list with an event log wholesale. It is to
make **turn-level state transitions append-only** so that the message list becomes a *view* over
durable state rather than the state itself.

---

## 22. Architectural Mapping

| Target Concept | Current Wisp Implementation | Location | Status | Evidence | Gap |
|---|---|---|---|---|---|
| **Goal** | none first-class; raw prompt | `core/stateless.py:266` | **IMPLICIT** | prompt appended verbatim | no Goal type, no acceptance criteria attached |
| **Goal Interpreter** | none | — | **MISSING** | only `_expand_continuation` (`transport/cli.py:591`) | no decomposition or intent step |
| **Planner** | graph planner + plan tools + strategy selector | `graph/planner.py:82`; `tools/plan.py`; `coding.py:115` | **PARTIAL** | plan state never reaches the prompt (`stateless.py:1238-1247`) | not on the live path; no replanning |
| **Typed Graph** | `Graph`/`GraphNode`/`EdgeMapping` | `graph/types.py:115-201` | **EXISTS** | frozen dataclasses | immutable — no runtime topology change |
| **Graph State** | `graph_*` tables + `SchedulerState` | `graph/store.py:132`; `scheduler.py:16` | **PARTIAL** | 11 status writes unpersisted | state not durable per transition |
| **Graph Controller** | `GraphExecutor._drive` | `graph/executor.py:264` | **PARTIAL** | no transition API; direct dict writes | no mutation validation layer |
| **Executor** | turn loop + ToolExecutor + GraphExecutor | `stateless.py:346`; `tool_executor.py:653`; `graph/executor.py:264` | **EXISTS** | — | three executors, one wiring |
| **Tool Layer** | 42 tools, 19 gates | `tools/registry.py`; `tool_executor.py:653` | **EXISTS** | — | strongest subsystem in the codebase |
| **Observation** | raw tool-result strings | `stateless.py:1914` | **MISSING** | no type | conflated with evidence |
| **Verifier** | floor guard + graph verifier nodes | `core/verification.py:119`; `graph/verifier.py:22` | **PARTIAL** | same model judges itself | not independent |
| **Evidence** | `GraphArtifact` (graph only) | `graph/types.py:305`; `artifacts.py:56-92` | **PARTIAL** | hash-verified, provenance present | absent in the turn path |
| **Recovery** | retry / repair / rollback / resume | see §13 | **PARTIAL** | no replan anywhere | local + global replan absent |
| **Context Engineering** | `ContextAssembler` + pruner + compactor | `context_assembler.py:329` | **PARTIAL** | **no trust boundary** | no graph context; not a first-class subsystem |
| **Repository Intelligence** | `repo_map.py` + 4 sibling indexes | `repo_map.py:197` | **EXISTS** | skeleton-only in the turn path | advisory; symbol mode unused live |
| **Memory** | 7 classes, no graph memory | see §18 | **PARTIAL** | no node/task durable memory | no provenance on snapshots |
| **Delegation** | `SubagentContract`/`Result` + orchestrator | `multi_agent/task.py:57` | **PARTIAL** | free-text task; prose-marker retries | no structured contract; narrowing unwired |
| **Stagnation Detection** | `OscillationTrap` | `core/graph/loop.py:112` | **IMPLICIT** | **unwired**; config flag never read | no live progress signal |
| **Persistence** | sessions + audit + graph rows | see §19 | **PARTIAL** | run layer unwired | no per-transition journal |
| **Replay** | graph resume + partial session replay | `graph/executor.py:149`; `session_repo.py:84` | **PARTIAL** | `Session.apply` ignores TOOL_CALL | turn replay impossible |
| **Budgets** | explicit, distributed | see §24 | **EXISTS** | — | no unified budget object |
| **Cancellation** | asyncio cooperative | `stateless.py:299`; `executor.py:233` | **EXISTS** | — | ambiguous partial state on cancel |
| **Human Intervention** | approval gate + WebSocket + APPROVAL nodes | `core/approval_gate.py:54`; `graph/executor.py:449` | **EXISTS** | — | REST approval channel missing |

**Status tally: EXISTS 7 · PARTIAL 11 · IMPLICIT 2 · MISSING 2 · UNKNOWN 0.**

---

## 23. Existing Strengths (must become foundations, not casualties)

| Mechanism | What it already solves | Why it must remain | Target mapping | Small eventual change |
|---|---|---|---|---|
| **`ToolExecutor` 19-gate pipeline** | every model effect passes one choke point | the only reason the agent path is defensible | becomes the *Effect* half of the proposal protocol | accept a validated `ToolCall` proposal instead of raw args |
| **`authorize()` 6-layer narrowing** | capability/sensitivity/argument/org decisions, each naming its layer | richer than any replacement; `controlling_layer` is exactly the provenance the target wants | becomes the legality validator for proposals | receive the org bundle (wire L0) |
| **`pathsec.is_protected_path`** | one canonical protected-path predicate, 3 call sites | eliminated a real privilege-escalation class | unchanged | none |
| **`OutcomeClass` taxonomy** | one classifier, AST-enforced, 8 classes | the pattern every other subsystem should copy | becomes the observation classifier | add `INCONCLUSIVE` if verification needs it |
| **`Graph` + `validator.py`** | typed, validated, cycle-checked topology | the target's graph should *be* this, not a new one | the graph model itself | allow controlled runtime mutation |
| **`GraphStore` + `audit.py` hash chain** | durable, provenance-bearing, tamper-evident state | directly satisfies "evidence should have provenance" | the persistence substrate for the loop | add per-transition rows |
| **`GraphExecutor` drive loop** | deterministic, bounded-concurrency, resumable execution | proven at 1,149 LOC with a deep test suite | the executor for the loop | accept dynamically inserted nodes |
| **`SubagentContract`/`SubagentResult`** | typed delegation envelopes already exist | no need to invent a contract layer | the delegation proposal/result types | make input structured; wire narrowing |
| **`SubagentTelemetryBuffer`** | dual-bounded rings + secret masking | correct bounded-state design | observability substrate | none |
| **`VerificationFloorGuard`** | blocks unverified completion; invalidates evidence on mutation | already enforces evidence invalidation | the seed of a real verifier | become one check among several |
| **`RepoMap` + PageRank + semantic index** | real repository intelligence | the target's derived-knowledge layer | advisory input to proposal | expose symbol mode on the turn path |
| **`SessionRepository` + `session_events`** | an existing append-only store on the live path | already wired — extend, don't replace | the event log | write tool/observation events |
| **`ContextAssembler` + 3 live pruners** | deterministic, budgeted, cached assembly | solid base | context subsystem | add trust tagging + graph context |
| **Bounded retries everywhere** | `max_attempts`, backoff, cycle bounds, fanout caps | budgets are already explicit | budget model | unify into one budget object |
| **The test suite (~5,374 tests)** | architectural invariants pinned, not just functions | the ratchet that keeps this honest | invariant tests | add loop-level E2E |

---

## 24. Budget Analysis

| Budget | Class | Value | Location |
|---|---|---|---|
| `max_iterations` | budget | 50 (config) / 30 (fallback) | `stateless.py:296`; `config.py:148-155,644-645` |
| `turn_timeout` | timeout | 1800 s (max 7200) | `stateless.py:219,299`; `config.py:156-163,648-649` |
| Provider first-token deadline | timeout | 90 s | `stateless.py:2040` |
| Provider chunk deadline | timeout | 90 s | `stateless.py:2046` |
| Stream attempts | budget | 3 | `stateless.py:2075` |
| Tool timeout | timeout | 300 s | `config.py:661-663` |
| Repeated-call cap | budget | `max_reflections` 3 | `config.py:171-178` |
| Verification floor | budget (surrender) | `min_turns=5`, `max_nudges=2` | `verification.py:123-124` |
| Context | budget | 200 KB payload; 50 messages; 128 000 tokens | `context_pruner.py:78-90`; `runtime.py:271-272` |
| Subagent token ceiling | budget | 2 000 000 | `config.py:653-655`; `composition.py:181-183` |
| Subagent `ResourceBudget` | budget | max_tokens / wall / tool_calls | `multi_agent/_runner.py:71-88` |
| Graph steps | budget | 5 | `config.py:793-794`; `core/graph/loop.py:136` |
| Graph run/node budgets | budget | tokens / cost / runtime / concurrency / depth / nodes / retries | `graph/executor.py:403-417,1062-1069`; `types.py:176-191` |
| Cycle bound | budget | ≤5 | `types.py:140-145`; `executor.py:706-710` |
| Join timeout | timeout | per-node | `executor.py:737-770` |
| Approval timeout | timeout | 60 s | `transport/websocket.py:30` |
| Subagent fanout | budget | branch 3 / depth 2 / pool 4 / running 8 | `tool_executor.py:1948-1973`; `background.py:29` |
| Telemetry rings | budget | 500 events / 256 KB | `telemetry.py:54-56` |

**Assessment: explicit and extensive, but distributed.** There is **no unified budget object** and no
single place that answers "how much is left". Termination is success-based, budget-based,
failure-based, timeout-based and cancellation-based — **all five**, with no precedence rule.

**Gap:** graph growth, task depth beyond 2, and *cost of replanning* are unbounded concepts, because
replanning does not exist.

---

## 25. Cancellation Analysis

| Layer | Mechanism | Location |
|---|---|---|
| Turn | `asyncio.timeout(turn_timeout)` | `stateless.py:299` |
| REPL / Ctrl-C | `make_repl_sigint_handler` cancels the turn task | `entry.py:295-311,521-524,559` |
| Provider stream | cancellation-first | `provider_stream.py:222-228` |
| Approval gate | re-raises `CancelledError` untouched | `approval_gate.py:112-116,144` |
| Background agent | handles `CancelledError` | `background_agent.py:149-156` |
| Shutdown | cancels live children | `composition.py:374` |
| Graph | `_cancelled` set + run status | `graph/executor.py:233-242,391-402` |
| Subagent | `request_cancel_live`/`cancel_live_tasks`, 2 s reap join | `subagent_orchestrator.py:438-475,46-84` |
| Deadline publication | `wisp.tools.context.turn_deadline` ContextVar | `stateless.py:224`; `tools/context.py:70-75` |

**Nature:** **cooperative**, not forceful. **Persisted:** only in the graph (run status); in the turn
path, cancellation persists **nothing**. **Replayable:** no. **Idempotent:** no.

**Can timed-out work leave ambiguous state? Yes — three ways:**
1. A tool may have completed its side effect before `asyncio.timeout` fired; the result is never recorded.
2. A shared-workspace subagent may have written files; there is **no rollback on cancel**
   (`subagent_orchestrator.py:919-924` reverts only on *conflict*).
3. `CheckpointStore` is in-memory, so a timeout-plus-crash loses the rollback path entirely.

---

## 26. Crash Recovery — conceptual simulation

| Crash point | What Wisp reconstructs | Ambiguous state | Duplicate-execution risk | Lost work |
|---|---|---|---|---|
| **While planning** | prior completed turn; the user message | none (planning is not persisted) | none | the plan (in memory) |
| **While executing** | prior completed turn; the user message | **which tools ran** | **HIGH** — turn re-runs from the prompt | all tool calls/results this turn |
| **After a tool call, before recording** | nothing about the call | **the disk side effect is invisible** | **HIGHEST** — the side effect repeats | the result; the record of the mutation |
| **During verification** | prior completed turn | whether the code was verified | moderate — verification re-runs | the verification result |
| **During retry** | nothing (retry state is in-memory) | retry count | moderate | the retry budget spent |
| **During repair (nudge)** | nothing | whether a nudge was already issued | low — nudges are re-derivable | nudge budget |
| **During replanning** | n/a — **replanning does not exist** | — | — | — |

**The structural cause:** there is no journal between *effect* and *record*. `session_events` receives
`user_message` at turn start (`runtime.py:403`) and `done`/`error` at turn end (`:636`), and **nothing
in between**. The turn's interior is a black box on disk.

**Minimum fix (documented, not implemented):** write a `tool_call` event *before* dispatch and a
`tool_result` event *after*, then make the turn's resumption path replay those. The event kinds
already exist and are **never called**: `SessionEvent.tool_call_event` / `tool_result_event` /
`assistant_message` / `compacted` (`core/session.py:41-54`).

---

## 27. Architectural Gaps, Prioritized

### P0 — Architectural correctness (blocking)

| # | Gap | Evidence | Why P0 |
|---|---|---|---|
| P0-1 | **No durable execution state for the live loop.** Turn interior is not journaled; success is event-derived. | `runtime.py:499`; `session_events` has 3 kinds | Every other gap depends on having state to reason over. Nothing else can be built correctly first. |
| P0-2 | **No runtime graph mutation.** Topology frozen; no expand / invalidate / supersede. | `graph/types.py:194-201`; executor never constructs a Graph | This *is* the "persistent graph loop" property. Its absence is the definitional gap. |
| P0-3 | **No proposal boundary.** Model output reaches effects directly. | `stateless.py:812` → `tool_executor.py:653` | Without it, "validation determines legality" cannot be stated, let alone enforced. |
| P0-4 | **Verification is self-assessment.** One invocation plans, acts, judges. | `verification.py:119`; `test_verification_loop.py:110-123` | "Observations are not proof of success" is violated at the core. |

### P1 — Durability / safety

| # | Gap | Evidence |
|---|---|---|
| P1-1 | No idempotency for tool effects → duplicate execution on crash | `idempotency` table empty; `runtime.py:596` stale comment |
| P1-2 | Org policy layer never loaded (L0 always `None`) | `composition.py:134`; `acp_session.py:208` |
| P1-3 | Subagent capability narrowing never invoked | `derive_subagent` — tests only |
| P1-4 | Rollback is non-durable | `tools/checkpoints.py:6-10` |
| P1-5 | Cancellation/timeout can leave ambiguous partial state | §25 |
| P1-6 | REST mutating routes ungated (context / git / hooks-delete) | `routes/context.py:38`; `routes/git.py:67`; `routes/hooks.py:162` |
| P1-7 | Subagent DAG node budget attached then dropped | `subagent_orchestrator.py:1316` vs `task.py:57` |

### P2 — Intelligence

| # | Gap | Evidence |
|---|---|---|
| P2-1 | No goal interpretation; no acceptance criteria | `stateless.py:266` |
| P2-2 | No replanning (local or global) | absent everywhere |
| P2-3 | No structured failure taxonomy | `NodeFailure.failure_code` free string |
| P2-4 | No trust boundary between repo content and instructions | §15.4 |
| P2-5 | Plan state never reaches the prompt | `stateless.py:1238-1247` |
| P2-6 | Repo map is skeleton-only on the live path | `stateless.py:1469`; `repo_map.py:218-245` |
| P2-7 | No graph context in the prompt | `runtime.py:394-395` |

### P3 — Optimization

| # | Gap |
|---|---|
| P3-1 | No unified budget object; five termination modes with no precedence |
| P3-2 | Compaction by message count rather than tokens |
| P3-3 | Two disjoint graph systems and two orchestration paths |
| P3-4 | Duplicate dangerous-command checks (`tool_executor.py:674` and `:760`) |

### P4 — Future capability

| # | Gap |
|---|---|
| P4-1 | Hypothesis nodes / evidence dependencies |
| P4-2 | Cascading invalidation |
| P4-3 | Multi-model verification (genuinely independent verifier) |
| P4-4 | OTLP trace export wiring |

### Dependency order

```
P0-1 (durable turn state)
   └─► P0-3 (proposal boundary) ──► P0-4 (independent verification)
          └─► P0-2 (runtime graph mutation)
                 └─► P2-2 (replanning) ──► P2-3 (failure taxonomy)
P1-* are independent of the P0 chain and can proceed in parallel.
P2-4, P2-6, P3-* are independent.
```

---

## 28. Migration Dependencies and Recommended Sequence

Full phase-by-phase specification is in `WISP_MIGRATION_PLAN.md`. The dependency logic:

| Step | Depends on | Unlocks |
|---|---|---|
| 0. Wire the orphaned durable layer (`runs/`, contracts, trace) to the turn path | — | observable state for every later step |
| 1. Journal turn-level transitions (tool_call/tool_result events) | 0 | replay, idempotency, crash recovery |
| 2. Introduce the proposal boundary around `ToolExecutor` | 0, 1 | validation, provenance, audit |
| 3. Promote verification to a separate proposal consumer | 2 | independent verification, evidence |
| 4. Materialize a task graph from the durable state | 1, 2 | a graph to mutate |
| 5. Enable runtime graph mutation (expand / invalidate / supersede) | 4 | the Persistent Graph Loop property |
| 6. Add recovery escalation (retry → repair → replan → escalate) | 3, 5 | self-correction |
| 7. Stagnation detection (wire `OscillationTrap` to the live loop) | 1, 5 | bounded autonomy |
| 8. Context engineering as a first-class subsystem (trust + graph context) | 4 | safe context |
| 9. Structured delegation | 2, 5 | reliable parallelism |

**Every step preserves current behavior**: each is additive, independently testable, and reversible by
disabling one flag. No step requires rewriting the turn loop, the tool executor, the graph engine, or
the authority layer.

---

## 29. Risks

| Risk | Severity | Why | Mitigation |
|---|---|---|---|
| **Unwired-layer trap repeats** | High | 8 more complete-but-unreachable subsystems were found; the next one may be introduced by this migration | every phase must ship a *reachability* test, not just a unit test |
| **False-absence conclusions** | High | `CONTEXT.md:543` documents **nine** occasions where a tool reported absence as death | verify every "missing" claim by driving the real path before acting on it |
| **Two-graph divergence** | Medium | `wisp/graph/` and `multi_agent/dag.py` are disjoint; a third graph would be worse | the target graph must be `wisp/graph/`, extended — never a new package |
| **Dual run-state vocabularies** | Medium | `graph/types.RunStatus` (7) vs `runs/record.RunState` (8) | canonicalize before wiring either |
| **Behavior change in the default permission mode** | Medium | G1: 6 of 36 (route, mode) pairs diverge in the default `auto_edit` mode | decision required, not a patch (`PHASE_10_AUTHORIZATION_PARITY.md`) |
| **Duplicate execution during migration** | Medium | no idempotency today | land P1-1 before enabling replay |
| **Test-suite brittleness** | Low | the desktop suite is flaky as a suite; long pytest runs are reaped | report per-subsystem results; never claim a full-suite pass |
| **Scope creep into a rewrite** | High | the target vocabulary invites renaming | Principle 11: evolve, and prove replacement is necessary before replacing |

---

## 30. Test Architecture

### 30.1 Coverage of the 25 required topics

| Topic | Status | Evidence |
|---|---|---|
| Graph creation | COVERED | `test_graph_engine.py`, `test_graph_proposals.py`, `security/test_graph_dsl.py` |
| Dependency resolution | COVERED | `test_graph_engine.py`, `test_graph_optimizer.py`, `test_graph_invariants.py` |
| Readiness | COVERED | `test_graph_engine.py`, `test_graph_invariants.py` |
| State transitions | COVERED | `test_runs_record.py`, `test_canonical_execution_state.py` |
| Dynamic expansion | **MISSING** | no code path and no test |
| Invalidation | PARTIAL | `test_verification_contract.py:72-78`; `test_verification_loop.py:139-164` |
| Supersession | COVERED | `security/test_graph_resume.py:161-177` |
| Planning | COVERED | `test_planner.py`, `test_graph_planner.py`, `security/test_graph_planner_adversarial.py` |
| Execution | COVERED | `test_graph_engine.py`, `test_coding.py`, `test_agent_runtime.py` |
| Verification | COVERED | `test_verification_loop.py`, `test_verification_contract.py`, `security/test_graph_verification.py` |
| Evidence provenance | COVERED | `security/test_graph_artifacts.py`, `security/test_graph_audit.py`, `test_audit_trail.py` |
| Retry | COVERED | `test_retry_integrity.py`, `test_ollama_client_retry.py` |
| Repair | PARTIAL | `reliability/test_13j1_fanout_contract_repair.py`; subagent schema repair only |
| Rollback | COVERED | `test_runs_compensation.py`, `test_checkpoints.py`, `test_journal_recovery.py` |
| Replanning | **MISSING** | no code, no test |
| Delegation | COVERED | `test_spawn_fanout.py`, `test_subagent_e2e.py`, `test_subagent_orchestrator.py` |
| Context construction | COVERED | `test_context_assembler*.py`, `test_operating_context.py`, `test_boot_context.py` |
| Repository intelligence | COVERED | `test_repo_map_seeds.py`, `test_repo_map_pagerank.py`, `test_code_index.py`, `test_semantic_index.py` |
| Stagnation | PARTIAL (unwired) | `test_architectural_upgrade.py:21-90` |
| Cancellation | COVERED | `test_dag_cancel.py`, `test_graph_terminality.py:469-590` |
| Timeout | COVERED | `test_tool_timeout_leaks.py`, `test_bash_termination.py` |
| Crash recovery | COVERED | `test_journal_recovery.py`, `test_runs_recover.py`, `security/test_graph_resume.py`, `reliability/test_killpoints.py` |
| Replay | COVERED | `reliability/test_replay.py` (but `test_13h4` records deterministic replay "NOT ESTABLISHED", `:424-439`) |
| False success | COVERED | `test_verification_contract.py`, `reliability/test_13h4_success_semantics.py`, `reliability/test_13h5_success_derivation.py` |
| Partial failure | COVERED | `test_graph_terminality.py:246-263`, `test_fanout_resilience.py` |
| Repeated failure | PARTIAL | `test_verification_loop.py:204-219` — repeat nudge only |

### 30.2 Missing architectural tests

1. **No E2E control-loop test.** No test drives Goal → Plan → Graph → Execute → Observe → Verify →
   Fail → Recover → Replan → Execute → Verify → Complete. The graph tests exercise the engine; the
   loop tests exercise a single turn; **nothing tests the loop as a loop.**
2. **No reachability test for the unwired layers.** `test_m4_governance_wiring.py` and
   `test_unwired_controls_inventory.py` are tripwires for *specific* items, but there is no general
   invariant that a durable subsystem is reachable from the turn path.
3. **No test that the turn's tool calls are durable** (because they are not).
4. **No test that verification is independent** (because it is not).
5. **No test for idempotency on crash-replay.**
6. **No test for stagnation in the live loop.**

### 30.3 The future E2E verification path (to design, not implement)

```
Goal (with acceptance criteria)
  ↓
Plan (proposal) ──► validated ──► graph materialized
  ↓
Execute node ──► Observation ──► Evidence (provenance: node_run, producer, hash)
  ↓
Verify ──► VerificationResult
  ├─ PASS ──► node SUCCEEDED ──► dependents READY ──► ... ──► GOAL COMPLETE
  └─ FAIL ──► failure classified
        ↓
      Recover: Retry ─► Repair ─► Local Replan ─► Global Replan ─► Escalate
        ↓
      Graph mutated (nodes invalidated / inserted) ──► Execute again
        ↓
      Stagnation check (progress signal) ──► if flat, escalate or terminate honestly
```

Each arrow must be an assertable, durable transition — that is the definition of done for the
migration.

---

## 31. Required Final Executive Report

```
CURRENT WISP
    │
    ▼
WHAT ALREADY MATCHES
    • Tool execution governance (19 gates, one choke point)
    • Authorization narrowing (6 layers, each naming its controlling layer)
    • Outcome classification (one AST-enforced authority)
    • A typed, validated, durable graph engine with hash-chained audit and artifacts
    • Deterministic readiness and bounded-concurrency graph execution
    • Repository intelligence (RepoMap + PageRank + semantic index)
    • Bounded budgets, retries, fan-out and cancellation
    • Typed delegation envelopes (SubagentContract / SubagentResult)
    │
    ▼
WHAT IS PARTIAL
    • Verification (a heuristic floor guard, not independent verification)
    • Evidence (real in the graph, absent in the turn loop)
    • Recovery (retry/repair/rollback exist; replanning does not)
    • Context engineering (assembly is solid; trust boundary and graph context are absent)
    • Persistence (durable stores exist; the durable run layer is not wired)
    • Delegation (typed envelopes exist; inputs are free text; narrowing is unwired)
    │
    ▼
WHAT IS MISSING
    • A Goal concept with acceptance criteria
    • Goal interpretation / decomposition
    • Runtime graph mutation (expand, invalidate, supersede, replan)
    • Observation and Evidence as first-class types in the live loop
    • Durable turn-level state transitions (a journal)
    • Independent verification
    • Stagnation detection on the live path (the detector exists, unwired)
    • A trust boundary between repository content and system instructions
    • Idempotency for tool effects
    │
    ▼
WHAT MUST CHANGE
    1. Journal turn transitions (tool_call / tool_result events) — P0
    2. Introduce a validated proposal boundary around ToolExecutor — P0
    3. Promote verification to an independent consumer of observations — P0
    4. Materialize a task graph from durable state and allow controlled mutation — P0
    5. Wire the org policy bundle (L0) and subagent capability narrowing — P1
    6. Add durable idempotency and a durable rollback path — P1
    7. Add recovery escalation including replanning — P2
    8. Establish a trust boundary in context assembly — P2
    │
    ▼
WHAT SHOULD NOT CHANGE
    • The 19-gate ToolExecutor pipeline and its gate order
    • authorize() and its six layers
    • pathsec.is_protected_path
    • The OutcomeClass taxonomy
    • wisp/graph/'s types, validator, store, audit and artifacts
    • The GraphExecutor drive loop's determinism and bounds
    • The repository intelligence layer
    • The bounded retry / fan-out / telemetry designs
    • The existing test ratchets
    │
    ▼
MIGRATION ORDER
    0 wire durable layer → 1 journal → 2 proposal boundary → 3 verification
    → 4 task graph → 5 runtime mutation → 6 recovery → 7 stagnation
    → 8 context → 9 delegation
```

### Q1 — What parts of Wisp already implement the Persistent Graph Loop?

The **durable graph engine** (`wisp/graph/`) implements the *loop's substrate*: a typed graph
(`graph/types.py:115-201`), deterministic readiness (`graph/scheduler.py:25-44`), a bounded
concurrency drive loop (`graph/executor.py:264-550`), durable run/node/artifact/event state
(`graph/store.py:132-284`), a hash-chained audit trail (`graph/audit.py:72-121`), a validation gate
(`graph/validator.py`), and bounded cycle-correction replay (`graph/executor.py:692-727`). The
**authority and tool layers** (`tool_executor.py:653`, `auth/decision.py:42`, `pathsec.py:40`) and
**`OutcomeClass`** (`core/events.py:280-290`) implement the "validation determines legality" and
"execution produces observations" principles faithfully. The **experimental phase loop**
(`core/graph/loop.py:128`) implements the literal phase vocabulary and a stagnation detector — but is
unwired.

### Q2 — What parts only look similar but are architecturally different?

1. **The turn loop looks like a control loop.** It has iteration, observation and termination — but
   there is **no state**: `WispAgentCore` is nominally stateless and the turn's state is a mutable
   message list. It is a ReAct loop, not a state machine.
2. **The `plan_task` tools look like a planner.** They write a `PlanStore` — but plan state never
   reaches the prompt (`stateless.py:1238-1247`). The planner is decorative.
3. **`multi_agent/dag.py` looks like the graph.** It is a *separate* DAG with no import edge to
   `wisp/graph/`, and `graph/runner.py:124` builds its own orchestrator. Two graphs, no bridge.
4. **`runs/` looks like the durable run layer.** It is a complete state machine with stores,
   leases and idempotency — constructed without its store and never reached.
5. **`VerificationFloorGuard` looks like verification.** It is deterministic bookkeeping over the
   same model's own output, and it explicitly permits surrender.
6. **`ContextAssembler` looks like context engineering.** It is a priority-sorted string builder with
   no trust boundary and no graph context.

### Q3 — Where are the current authority boundaries?

Three, and they do not coincide:
- **Agent path:** `ToolExecutor.execute` (`tool_executor.py:653`) — 19 gates, composing
  `policy_hard_deny` + `authorize()` + approval.
- **REST path:** `require_tool_allowed` (`server/deps.py:385-437`) — consults **only** `SecurityPolicy`;
  three mutating routes carry no gate at all.
- **Graph path:** `validate_graph` + fingerprint/workspace pins (`graph/executor.py:117,122,131,155-188`),
  reachable only from the CLI/SDK.

### Q4 — Where can uncontrolled state mutation occur?

1. Org policy L0 is never invoked (`composition.py:134`; `acp_session.py:208`).
2. Subagent capability narrowing is never invoked (`derive_subagent` — tests only), so children run
   with an unbounded principal (`tool_executor.py:708`).
3. REST: `POST /api/context` (`routes/context.py:38`), `POST /api/git/commit` (`routes/git.py:67`),
   `DELETE /api/hooks/{name}` (`routes/hooks.py:162`) — API-key auth only.
4. `_skip_authorize=True` at `tool_executor.py:1345`.
5. The Docker sandbox tier skips the dangerous-command list (`sandbox/__init__.py:168-200`).
6. Eleven unpersisted graph status writes (`graph/executor.py:436,471,751,779,791,858,867`).
7. The turn's interior — tool effects are unrecorded between `user_message` and `done`.
8. Subagent partial patch application is non-transactional (`subagent_orchestrator.py:919-924`).

### Q5 — What is the current graph lifecycle?

`Graph` (frozen, built at compile time) → `GraphExecutor.run()` → `QUEUED`→`RUNNING` → drive loop
selects `ready_nodes` by sorted id → per-node `PENDING`→`RUNNING`→`SUCCESS`/`FAILURE`/`TIMEOUT`/
`CANCELLED`/`SKIPPED` → bounded retry re-queue and cycle-correction replay → all sinks terminal →
`SUCCEEDED`/`FAILED`/`CANCELLED`. Persistence via `GraphStore`. **The topology never changes; only
status maps do.**

### Q6 — What is missing for a formal persistent graph model?

`READY` as materialized state; `OBSERVED`/`VERIFYING` distinct from `RUNNING`; `INVALIDATED` and
`SUPERSEDED`; runtime node creation and deletion; runtime edge insertion/removal; cascading
invalidation; a single validated transition API; per-transition durability; and a Goal node with
acceptance criteria.

### Q7 — What is missing for independent verification?

A `VerificationRequest` type; a verifier that is not the acting invocation (`verification.py:119`
self-judges); acceptance criteria as data; a policy for *which* checks are required; a
`VerificationResult` on the turn path (it exists only in the graph); and enforcement that a
different model/context performs the check.

### Q8 — What is missing for structured evidence?

An `Evidence` type on the live path; provenance (producer, node_run, timestamp) for turn-level
results; a durable observation record; and separation of Claim / Observation / Evidence / Artifact /
VerificationResult / Failure, which are currently **all the same string** at
`core/stateless.py:1914`.

### Q9 — What is missing for explicit recovery?

Local replan; global replan; a closed failure taxonomy (`NodeFailure.failure_code` is a free string);
recovery budgets with precedence; durable rollback (`CheckpointStore` is in-memory); and the
escalation ladder — Retry → Repair → Rollback → Local Replan → Global Replan → Diagnostic → Human.

### Q10 — What is missing for context engineering?

A trust boundary (absent — no delimiters, escaping or provenance tags); graph/task context (absent);
plan context (assembled but never populated); deterministic *selection* (currently scan + keyword +
model); token-based compaction (currently message-count); and a single place that decides what the
model sees.

### Q11 — What is missing for reliable delegation?

A structured task input (today: free text); a mandatory structured result; a declared
context-boundary contract; per-contract capability declarations **that are actually applied**
(`derive_subagent` is unwired); a typed error taxonomy (today: prose markers); a wired circuit breaker
(`multi_agent/_circuit_breaker.py` is dead); transactional effects in shared workspaces; and typed
inter-agent messages (`SharedContext` is an untyped dict).

### Q12 — What is missing for replay/crash recovery?

A per-transition journal (tool_call / tool_result events — the kinds exist at
`core/session.py:41-54` and are never called); `Session.apply` support for `TOOL_CALL`
(`core/session.py:84-117` ignores it); durable idempotency (the table is empty); a durable rollback
path; and a `RunRecord` for normal turns (the durable run layer is constructed without its store).

### Q13 — What is the minimum implementation sequence required?

**0** wire the durable layer → **1** journal turn transitions → **2** proposal boundary around
`ToolExecutor` → **3** independent verification → **4** materialize a task graph from durable state →
**5** runtime graph mutation → **6** recovery escalation → **7** stagnation detection → **8** context
trust + graph context → **9** structured delegation. Full specification:
`WISP_MIGRATION_PLAN.md`.

### Q14 — Which existing components should become foundations rather than being replaced?

`ToolExecutor` (19 gates) · `authorize()` (6 layers) · `pathsec.is_protected_path` · `OutcomeClass` ·
`wisp/graph/` types + validator + store + audit + artifacts · `GraphExecutor` drive loop ·
`SubagentContract`/`SubagentResult` · `SubagentTelemetryBuffer` · `VerificationFloorGuard` ·
`RepoMap` + PageRank + semantic index · `SessionRepository` + `session_events` · `ContextAssembler`
+ the three live pruners · the bounded retry/fan-out/budget designs · the entire test ratchet suite.

---

## 32. Success Criteria Check

| Criterion | Status |
|---|---|
| Entire Wisp architecture inspected | ✅ 7 subsystem sweeps + direct verification |
| Actual execution path traced | ✅ §4 (27 hops) |
| Current graph semantics documented | ✅ §5 |
| Current lifecycle / state machine documented | ✅ §6 |
| Every direct graph mutation investigated | ✅ §7 (≈30 sites) |
| Authority boundaries documented | ✅ §8 |
| Planner/executor/verifier boundaries documented | ✅ §9-11 |
| Verification architecture documented | ✅ §12 + `WISP_VERIFICATION_ARCHITECTURE.md` |
| Evidence / provenance documented | ✅ §12 |
| Recovery architecture documented | ✅ §13 + `WISP_RECOVERY_ARCHITECTURE.md` |
| Stagnation behavior documented | ✅ §14 |
| Context engineering documented | ✅ §15 + `WISP_CONTEXT_ARCHITECTURE.md` |
| Repository intelligence documented | ✅ §16 |
| Subagent architecture documented | ✅ §17 + `WISP_SUBAGENT_ARCHITECTURE.md` |
| Memory architecture documented | ✅ §18 |
| Persistence documented | ✅ §19 |
| Replay behavior documented | ✅ §20 |
| Cancellation and timeout documented | ✅ §25 |
| Budget and termination documented | ✅ §24 |
| Existing strengths identified | ✅ §23 |
| Architectural gaps identified | ✅ §27 |
| Target architecture documented | ✅ `WISP_TARGET_ARCHITECTURE.md` |
| Proposal boundary documented | ✅ `WISP_PROPOSAL_PROTOCOL.md` |
| Graph domain model documented | ✅ `WISP_GRAPH_DOMAIN_MODEL.md` |
| Migration plan documented | ✅ `WISP_MIGRATION_PLAN.md` |
| Test gaps documented | ✅ §30 |
| **No implementation code was changed** | ✅ audit only |

**STOPPED.** No implementation. No refactor. No test change. The next phase awaits review.
