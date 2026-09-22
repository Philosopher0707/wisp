# WISP — MIGRATION PLAN

**Phase 0 design document. Conceptual only — nothing here is implemented.**

Incremental evolution from the current architecture to the Persistent Graph Loop
(`WISP_TARGET_ARCHITECTURE.md`).

**Governing constraints (from the brief and from repository evidence):**

- Preserve existing behavior at every step. No step may break a current capability.
- Every step is independently testable and independently verifiable.
- Every step is reversible by disabling one flag.
- No rewrites. The turn loop, `ToolExecutor`, the authority layer, `wisp/graph/` and `RepoMap` are
  foundations.
- No renaming to resemble the research report (brief §34).
- Every step ships a **reachability test**, not only a unit test — because the audit found **eight**
  complete, tested, unreachable subsystems, and adding a ninth would be the worst possible outcome.

---

## 0. Dependency Graph

```
  P0  Wire the durable layer
       │
       ├─► P1  Journal turn transitions ──┬─► P2  Proposal boundary ──┬─► P3  Independent verification
       │                                   │                          │
       │                                   │                          ├─► P4  Task graph from durable state
       │                                   │                          │        │
       │                                   │                          │        └─► P5  Runtime graph mutation
       │                                   │                          │                 │
       │                                   │                          │                 ├─► P6  Recovery ladder
       │                                   │                          │                 │
       │                                   │                          │                 └─► P7  Stagnation detection
       │                                   │                          │
       │                                   │                          └─► P9  Structured delegation
       │                                   │
       └─► P8  Context trust boundary  (independent — can start immediately)
```

**Parallelizable:** P8 is independent of the P0 chain. Within the chain, P1 and P2 can overlap once P0
lands. P3, P4 and P8 can proceed concurrently after P2.

**Critical path:** P0 → P1 → P2 → P4 → P5 → P6.

---

## Phase 0 — Wire the Orphaned Durable Layer

### Objective

Make the durable state that already exists **reachable from the live turn path**, so that every later
phase has state to reason over.

### Prerequisites

None. This is the entry point.

### Components Affected

| Component | File | Change |
|---|---|---|
| `BackgroundAgentManager` | `composition.py:192`, `tool_executor.py:1666` | pass the existing `SQLiteRunStore` |
| `SessionRepository` | `core/session_repo.py:26` | already wired — extend the event kinds written |
| `SQLiteTraceStore` | `trace/store.py:23` | instantiate on the runtime path |
| `RunRecord` | `runs/record.py:87` | created for normal turns |
| `Session.apply` | `core/session.py:84-117` | handle `TOOL_CALL` |

### Expected Changes

1. Pass a `run_store` at both `BackgroundAgentManager` construction sites
   (`composition.py:192`, `tool_executor.py:1666`) — today `run_store=None` means every `_persist_*`
   returns early (`background.py:165-166,188-189`) and the `Scheduler` is never built
   (`background.py:98`).
2. **Canonicalize `RunStatus` (7 values, `graph/types.py:37-44`) and `RunState` (8 values,
   `runs/record.py:17-25`) before wiring either.** The audit found them divergent but harmlessly so
   *because the layer is unwired*; wiring without canonicalizing would create a live divergence — the
   exact defect class Phase 10 spent its budget on. `tests/test_canonical_execution_state.py` already
   polices one vocabulary; extend it.
3. Wire `SQLiteTraceStore` so spans are emitted from the runtime (today only `trace/cli.py:30`
   instantiates it; `trace_spans` is empty).
4. Write `SessionEvent.tool_call_event` / `tool_result_event` — the kinds exist
   (`core/session.py:41-54`) and are **never called**.
5. Make `Session.apply` handle `TOOL_CALL` so replay can rebuild tool execution.

### Tests Required

| Test | Asserts |
|---|---|
| `test_durable_run_layer_reachable.py` | a normal turn creates a `RunRecord` row (the reachability test) |
| `test_run_state_canonical.py` | one vocabulary; all legacy values coerce |
| `test_trace_spans_emitted.py` | a turn produces spans |
| `test_session_events_tool_kinds.py` | tool_call/tool_result events are written |
| `test_session_replay_tool_calls.py` | `Session.apply` rebuilds a tool call |

### Migration Risk

**Low.** Purely additive: new rows are written; no existing read path changes. The only real risk is
the state-vocabulary canonicalization — mitigated by doing it *first* and behind a coercion shim.

### Rollback Strategy

One flag (`WISP_DURABLE_RUNS`) gating the store construction. Off → today's behavior exactly.

### Completion Criteria

- A turn writes a `RunRecord` and at least one `tool_call` + `tool_result` event.
- `background_runs`, `run_transitions`, `trace_spans` are non-empty after a normal session.
- `test_canonical_execution_state.py` passes with one vocabulary.
- `ruff` clean; `mypy` exit 0; no regression in the existing suite.

---

## Phase 1 — Journal Turn Transitions

### Objective

Make the turn's interior durable, so that a crash is recoverable and a turn is replayable.

### Prerequisites

Phase 0.

### Components Affected

`core/stateless.py` (`_turn_inner`), `core/runtime.py`, `core/session_repo.py`, `core/session.py`.

### Expected Changes

1. Write a `tool_call` event **before** dispatch and a `tool_result` event **after**
   (`core/stateless.py:812` and `:840-883`). Today the turn's interior is a black box on disk —
   `session_events` receives `user_message` at turn start (`runtime.py:403`) and `done`/`error` at
   turn end (`:636`), and **nothing in between**.
2. Add an **idempotency key** to each action: `hash(task_ref, tool, canonical(args))`. This closes the
   duplicate-execution risk: if the process dies after a tool's side effect but before the `finally`
   persists (`runtime.py:517-594`), the turn currently re-runs from the bare prompt and repeats the
   effect. The audit found **no durable idempotency** anywhere (`idempotency` table empty;
   `Scheduler.memoize`/`already_done` unwired; the only guard is the in-memory per-turn `repeat_guard`).
3. Replace the whole-session snapshot as the *only* durable record with an append-only journal; the
   snapshot becomes a materialized view.
4. Remove the stale comment at `runtime.py:596` ("Cache result for idempotency (1h TTL)") which has
   no code following it — or implement it.

### Tests Required

| Test | Asserts |
|---|---|
| `test_turn_journal_completeness.py` | every tool call has a matching result event |
| `test_crash_replay_no_duplicate.py` | kill between effect and record → replay does not repeat the effect |
| `test_idempotency_key_stability.py` | the same call yields the same key; different args differ |
| `test_replay_reconstructs_turn.py` | the journal rebuilds the turn's message list |

### Migration Risk

**Medium.** This touches the hot path. The journal write is additional I/O per tool call — measurable
but small relative to a tool call itself. Risk is bounded by making the journal **append-only and
best-effort-with-assertion**: a journal write failure fails the turn loudly rather than silently
proceeding.

### Rollback Strategy

Flag `WISP_TURN_JOURNAL`. Off → no journal writes, snapshot-only persistence (today's behavior).

### Completion Criteria

- A killed turn is replayable to its last recorded transition.
- No duplicate tool effect in the crash-injection test.
- Journal write overhead measured and reported.

---

## Phase 2 — Introduce the Proposal Boundary

### Objective

Make reasoning produce **proposals** that validation disposes, instead of model output reaching
effects directly.

### Prerequisites

Phases 0, 1.

### Components Affected

`core/stateless.py`, `tool_executor.py`, `contracts/tool.py`, `auth/decision.py` (consume only),
`tools/registry.py`.

### Expected Changes

1. Wire the **existing** `ToolRequest` (`contracts/tool.py:15-80`) — it is already the right shape
   with statuses and block-reasons vocabularies, and has **no production producer or consumer**.
2. Wrap the dispatch at `core/stateless.py:812` in a `ToolCall` proposal carrying `intent`,
   `provenance` and `idempotency`.
3. **Record the authorization verdict.** `authorize()` already returns `controlling_layer`
   (`auth/decision.py:23-30`) and today it is **discarded**. Persist it.
4. **Do not re-implement any gate.** `ToolExecutor`'s 19 gates (`tool_executor.py:653-980`) keep their
   exact semantics and order. The proposal layer adds a *record*, not a decision procedure.
5. Emit a `ProposalOutcome` for every proposal, including rejections — a rejection is a first-class
   observable event.

### Tests Required

| Test | Asserts |
|---|---|
| `test_proposal_boundary_no_bypass.py` | **no** code path reaches a tool effect without a proposal (AST/structural) |
| `test_verdict_layer_recorded.py` | `controlling_layer` is persisted for allow and deny |
| `test_gate_order_unchanged.py` | the 19 gates fire in the same order (behaviour identical) |
| `test_rejection_observable.py` | a denial produces a recorded outcome with a reason |
| `test_proposal_idempotent.py` | re-applying a proposal is a no-op |

### Migration Risk

**Medium.** The main risk is behavioral drift in the gate chain. Mitigated by
`test_gate_order_unchanged.py`, which should be written **before** the change (RED-first) and by
asserting byte-identical outcomes on a corpus of representative calls — the technique Phase 10 used
successfully for the renderer delegation (`CONTEXT.md:424`, "Behaviour verified IDENTICAL on a
29-item corpus").

### Rollback Strategy

Flag `WISP_PROPOSAL_BOUNDARY`. Off → direct dispatch (today's behavior).

### Completion Criteria

- Every tool effect has a recorded proposal with a verdict and a layer.
- Gate order and outcomes provably unchanged on the corpus.
- No second authorization implementation introduced.

---

## Phase 3 — Independent Verification

### Objective

Make "this succeeded" a **verdict against criteria backed by evidence**, produced by a component that
is not the one that acted.

### Prerequisites

Phases 0, 1, 2.

### Components Affected

`core/verification.py`, `graph/verifier.py`, `graph/types.py` (`VerificationResult`), `benchmark/tasks.py`,
`change_tracker.py`, `core/stateless.py`.

### Expected Changes

1. Introduce `AcceptanceCriteria` (kinds: `DETERMINISTIC` | `ARTIFACT` | `SEMANTIC`; `required` flag).
   Generalize the nearest existing analogue — `benchmark/tasks.py:29` `verify` — rather than inventing.
2. Introduce `VerificationRequest` / `VerificationResult` on the turn path. The result type exists
   (`graph/types.py:285-294`) but with a **router's** vocabulary
   (`("ALLOW","REJECT","RETRY","ESCALATE")`, `graph/verifier.py:19`) that cannot express "I could not
   tell". Add `INCONCLUSIVE` and keep the routing decision derived from the verdict.
3. Introduce `Evidence` with provenance, generalizing `GraphArtifact` (`graph/types.py:305-316`) —
   which is already content-addressed, hash-verified, and carries `producer` + `node_run_id`.
4. **Retain and demote** `VerificationFloorGuard` (`core/verification.py:119-206`) to one
   deterministic check. Its `UNVERIFIED` surrender maps onto `INCONCLUSIVE`.
   **Retain its invalidate-on-mutation property unchanged** (`:149`) — it is already correct and
   pinned by `test_verification_contract.py:72-78`.
5. Enforce **structural independence (L1/L2)**: a separate context, criteria the actor never saw, and
   evidence-only judgment. A second model (L3) is preferred but not required.
6. Change the completion rule: `SUCCEEDED` requires non-invalidated evidence satisfying every
   *required* criterion. `guard.resolved()`'s current boolean
   (`wrote_code and verify_ok_after_edit is True`, `:193-196`) becomes the *cheap* check, not the gate.
7. Wire `change_tracker.py` into evidence — it already records mutations with timestamp/agent_id/sizes.

### Tests Required

| Test | Asserts |
|---|---|
| `test_criteria_required_gate.py` | no criteria → `INCONCLUSIVE`, never `SUCCEEDED` |
| `test_false_success_impossible.py` | a task cannot reach `SUCCEEDED` without evidence for every required criterion |
| `test_verifier_independence.py` | the verifier's context does not contain the actor's transcript |
| `test_evidence_provenance.py` | every evidence cites ≥ 1 observation; hash verifies |
| `test_evidence_invalidation_cascades.py` | a later mutation invalidates dependent evidence |
| `test_deterministic_first.py` | a failing deterministic check short-circuits semantic evaluation |
| `test_floor_guard_retained.py` | the existing guard behavior is preserved (no regression) |

### Migration Risk

**High — the highest in the plan.** This changes the completion semantics of the product. A stricter
gate can make previously-"successful" turns report `INCONCLUSIVE`.

**Mitigation:** ship in two stages. **3a** introduces criteria + evidence and *records* the verdict
without gating. **3b** enables the gate behind a flag, after a measurement period showing how many
turns become `INCONCLUSIVE`. This mirrors the repository's own "tripwire then ratchet" discipline.

### Rollback Strategy

Flag `WISP_VERIFICATION_GATE`. Off → the floor guard is the gate (today's behavior). The evidence
recording from 3a remains, harmlessly.

### Completion Criteria

- `INCONCLUSIVE` is a reachable, tested outcome.
- A synthetic false-success scenario is blocked.
- The measured `INCONCLUSIVE` rate at 3b is reported before enabling.
- `test_verification_loop.py` and `test_verification_contract.py` pass unchanged.

---

## Phase 4 — Materialize a Task Graph from Durable State

### Objective

Turn the durable turn state into an explicit, inspectable **task graph**, without yet allowing it to
change during execution.

### Prerequisites

Phases 0, 1, 2.

### Components Affected

`wisp/graph/` (reuse), `core/runtime.py`, `core/stateless.py`, `graph/scheduler.py`.

### Expected Changes

1. **Reuse `wisp/graph/`'s types, validator, store and scheduler.** Do not create a new graph package.
2. Map each turn's work to `GraphNode`s using the existing `NodeType` vocabulary
   (`graph/types.py:17-24`). The turn becomes a graph with one `AGENT` node per iteration.
3. **Materialize `READY`** rather than recomputing it. Today `ready_nodes` (`scheduler.py:25-44`)
   computes readiness on each pass and stores nothing. Materializing it is what makes the graph
   *persistent state* rather than a recomputation.
4. Introduce `NodeTransition` (`WISP_PROPOSAL_PROTOCOL.md` §3.2) as the **only** write path for node
   state. This replaces the audit's finding of ~30 direct mutation sites, 11 of them unpersisted, with
   one validated, journaled, idempotent write.
5. Retire `multi_agent/dag.py` into `wisp/graph/` — it is a strictly weaker duplicate (no durability,
   no audit, no artifacts, less validation).

### Tests Required

| Test | Asserts |
|---|---|
| `test_turn_materializes_graph.py` | a turn produces a persisted graph with nodes and edges |
| `test_ready_materialized.py` | `READY` is stored, not recomputed |
| `test_single_transition_api.py` | **no** direct status write exists outside the controller (AST/structural) |
| `test_transition_persisted.py` | every transition has a durable row |
| `test_dag_retired_into_graph.py` | the `dag` pattern runs on `wisp/graph/` |

### Migration Risk

**Medium-high.** The graph is a new representation of existing behavior; divergence between the graph
and the message list is the risk. Mitigated by making the message list a **projection** of the graph
rather than a parallel truth (C6 in `WISP_TARGET_ARCHITECTURE.md`).

### Rollback Strategy

Flag `WISP_TASK_GRAPH`. Off → the message list remains authoritative.

### Completion Criteria

- A turn's work is fully represented as persisted graph rows.
- One transition API; the structural test proves no bypass.
- `test_graph_engine.py`, `test_graph_invariants.py` and `test_canonical_execution_state.py` pass.

---

## Phase 5 — Runtime Graph Mutation

### Objective

**This is the phase that creates the Persistent Graph Loop property.** Enable the graph to change
during execution.

### Prerequisites

Phase 4.

### Components Affected

`graph/types.py` (frozen dataclasses), `graph/executor.py`, `graph/scheduler.py`, `graph/validator.py`,
`graph/planner.py`.

### Expected Changes

1. Add the missing states: `INVALIDATED`, `SUPERSEDED`, `WAITING`, `BLOCKED`, `OBSERVED`,
   `VERIFYING`, `INCONCLUSIVE` (`WISP_GRAPH_DOMAIN_MODEL.md` §4). Today `NodeStatus` has 7 values
   (`graph/types.py:27-34`) and `SKIPPED` conflates "predecessor failed" with "dead branch".
2. Implement `NodeCreate` and `GraphExpand` (`WISP_PROPOSAL_PROTOCOL.md` §3.1, §3.3) so the graph can
   grow mid-run. **Today node creation exists only at compile time**
   (`graph/optimizer_passes.py:346-367`); the `Graph` is a frozen value
   (`graph/types.py:194-201`) that the executor never constructs.
3. Implement `GraphInvalidate` with cascade (`§3.4`).
4. **Preserve immutability as a design principle:** a task is never mutated into a different task. A
   replan creates a **new** node and marks the old `SUPERSEDED` with a pointer. This keeps the frozen
   dataclass design *and* history, which replay requires.
5. Extend the executor with exactly one new capability: accept a newly inserted node mid-run.
   Preserve determinism (sorted-id selection, `scheduler.py:33`), join policies, concurrency bounds
   and retry semantics.
6. Add a **graph-growth budget** (audit §24: graph growth is currently unbounded because replanning
   does not exist).

### Tests Required

| Test | Asserts |
|---|---|
| `test_runtime_node_insertion.py` | a node created mid-run executes |
| `test_graph_expand_acyclic.py` | expansion cannot create a cycle |
| `test_invalidation_cascades.py` | invalidating a node invalidates dependent `SUCCEEDED` nodes |
| `test_supersession_preserves_history.py` | superseded nodes are retained with a pointer |
| `test_expansion_budget.py` | graph growth is bounded and the bound is enforced |
| `test_executor_determinism_retained.py` | insertion does not break deterministic selection |
| `test_no_topology_mutation_outside_controller.py` | structural: only the controller mutates topology |

### Migration Risk

**High.** This is the largest single change and the one that makes the architecture what it claims to
be. Risks: non-terminating expansion; determinism loss; replay divergence.

**Mitigation:** (a) the graph-growth budget is mandatory, not optional; (b) determinism is asserted by
a property test across insertion orderings; (c) every mutation is journaled, so divergence is
detectable.

### Rollback Strategy

Flag `WISP_GRAPH_MUTATION`. Off → the graph is frozen as today; execution and persistence unchanged.

### Completion Criteria

- A node can be created, executed, invalidated and superseded during a run.
- Cascading invalidation is correct and tested.
- Determinism holds across insertion orderings.
- Growth is bounded and the bound is enforced.
- `test_graph_fuzz.py`, `test_graph_races.py`, `test_graph_resume.py` pass.

---

## Phase 6 — Recovery Ladder

### Objective

Replace ad-hoc recovery with an explicit, budgeted, evidence-bearing ladder.

### Prerequisites

Phases 3, 5.

### Components Affected

`core/verification.py`, `core/provider_stream.py`, `graph/executor.py`, `tools/checkpoints.py`,
`runs/compensation.py`, `core/graph/loop.py`, `core/approval_gate.py`.

### Expected Changes

1. Introduce the closed **failure taxonomy** (10 classes,
   `WISP_RECOVERY_ARCHITECTURE.md` §1). Today `NodeFailure.failure_code` is a free string
   (`graph/types.py:273-283`) and six of the ten classes exist nowhere.
2. Implement the ladder: Retry → Repair → Rollback → Local Replan → Global Replan → Diagnostic →
   Human Escalation, with the legal-rung table and the `❌` (structurally forbidden) rules.
3. **Wire the durable rollback path.** `tools/checkpoints.py` is in-memory and session-scoped
   (`:6-10`) — process death loses every checkpoint. `runs/compensation.py` already defines
   `EditRecord`, `rollback_preview` and a `_REVERSIBILITY` map (`:40-54`) and its docstring states
   *"No tool wiring"*. **This is wiring, not design.**
4. Make escalation a **durable state**, not a blocking call. `HumanIntervention`
   (`WISP_GRAPH_DOMAIN_MODEL.md` §2.12) makes approval resumable and gives REST a channel through the
   existing `WebSocketTransport` approval flow.
5. Introduce the recovery budgets (local replans, global replans, diagnostic tasks) and the
   `BudgetGovernor` that reports them from one place.

### Tests Required

| Test | Asserts |
|---|---|
| `test_failure_taxonomy_closed.py` | exactly 10 classes; every detection maps to one |
| `test_denial_never_retries.py` | `SECURITY` and `REPEATED` forbid rung 1 |
| `test_escalation_is_terminal.py` | ladder exhaustion → `ESCALATED_TO_HUMAN`, not a hang |
| `test_durable_rollback_survives_crash.py` | rollback works after a process restart |
| `test_recovery_requires_evidence.py` | every rung cites evidence |
| `test_ladder_budget_enforced.py` | budgets bound each rung |
| `test_escalation_payload_is_audit_trail.py` | the escalation carries the full ladder history |

### Migration Risk

**Medium.** The mechanisms mostly exist; the risk is ordering and budget interaction. The `SECURITY`
no-retry rule must be verified carefully — Phase 10 removed a broken `_DENIAL_MARKERS` that failed to
enforce exactly this (`CONTEXT.md:456`).

### Rollback Strategy

Flag `WISP_RECOVERY_LADDER`. Off → today's independent mechanisms.

### Completion Criteria

- All seven rungs reachable and tested.
- Denials never retry (the Phase 10 defect class does not reappear).
- Rollback survives a crash.
- Escalation is resumable and carries its audit trail.

---

## Phase 7 — Stagnation Detection

### Objective

Detect "working but not progressing", and route it to the recovery ladder.

### Prerequisites

Phases 1, 5.

### Components Affected

`core/graph/loop.py` (`OscillationTrap`), `core/verification.py`, `config.py`, `graph/types.py`.

### Expected Changes

1. **Wire `OscillationTrap`** (`core/graph/loop.py:112-125`) to the live loop. It detects 1-cycle
   repeats and 2-cycle oscillations of diff hashes — a genuine progress signal. Verified: the only
   importer is `tests/test_architectural_upgrade.py:81-90`, and `config.graph_oscillation_guard`
   (`config.py:256,544,799-800`) is **never read**.
2. Build the progress metric from four inputs that **already exist but are unconnected**:
   - `VerificationFloorGuard.steps` (`core/verification.py:131`);
   - verification result **history** — currently **overwritten** (`:151`) and must become a list;
   - `GraphArtifact.content_hash` (`graph/types.py:305-316`) — not fed to the turn loop;
   - a monotonic progress metric — absent, to be defined (criterion satisfied / new artifact hash /
     failing-criteria set shrank / completed-node count increased).
3. Route `STAGNATION` to Global Replan (rung 5), then Diagnostic (rung 6), then Escalate (rung 7).
4. **A stagnated goal must never reach `GOAL_MET`.**

### Tests Required

| Test | Asserts |
|---|---|
| `test_stagnation_detected_2cycle.py` | an A→B→A cycle is detected |
| `test_progress_metric_monotonic.py` | genuine progress is not flagged |
| `test_stagnation_routes_to_replan.py` | detection triggers rung 5, not a retry |
| `test_stagnated_goal_not_met.py` | a stagnated goal cannot report success |
| `test_oscillation_trap_wired.py` | the config flag is read (reachability) |

### Migration Risk

**Low-medium.** The detector exists and is tested; the risk is false positives (flagging productive
work as stagnated). Mitigated by requiring **N consecutive** non-progressing evaluations and by the
"strictly shrank" formulation of the metric.

### Rollback Strategy

Flag `WISP_STAGNATION_GUARD` (the existing `graph_oscillation_guard` flag is the natural home).

### Completion Criteria

- A synthetic oscillation is detected and routed.
- No false positive on a productive multi-step task.
- The existing `config.graph_oscillation_guard` flag is finally read.

---

## Phase 8 — Context as a First-Class Subsystem

### Objective

Establish a trust boundary and make context construction deterministic and explainable.

### Prerequisites

None for the trust boundary (can start immediately). Graph context requires Phase 4.

### Components Affected

`context_assembler.py`, `core/stateless.py`, `core/context_pruner.py`, `core/context_manager.py`,
`core/context/boot.py`, `repo_map.py`, `memory.py`, `core/compaction.py`.

### Expected Changes

1. **Trust tags** (`SYSTEM` | `OPERATOR` | `REPOSITORY` | `TOOL_OUTPUT` | `EXTERNAL`) on every context
   item, with structural rules T1–T4 (`WISP_CONTEXT_ARCHITECTURE.md` §4.2). Today there is **no code
   that distinguishes untrusted repository content from trusted instructions** — only prose and the
   tool pipeline.
2. **`ContextRequest` → `Context`** contract with deterministic assembly (D1–D4), including a
   `dropped` list so truncation is explainable rather than silent (`_fit_sections` currently truncates
   with no record, `context_assembler.py:492-582`).
3. **Graph context section**, scoped to the current node (rule G1) — never a whole-graph dump. Include
   the structured failure ledger (G5) and explicit budget state (G4).
4. **Populate plan context.** `PromptContext.from_legacy` is called without `plan_*`
   (`stateless.py:1238-1247`), so `PlanState` (`context_assembler.py:199-208`) and the
   `## PLAN MODE ACTIVE` prose (`:409-424`) are **inert**. Either populate them or remove them.
5. **Serve the symbol-level repo map.** The turn path forces `fast_mode=True`
   (`stateless.py:1469`), and `fast_mode` short-circuits at `repo_map.py:218-245`, so **only the
   skeleton ever reaches the model** (verified on disk: `_meta.skeleton: true`, 200 entries, all
   `kind:"file"`). Measure, then remove the shortcut within the existing 1200-token budget.
6. **Token-based compaction.** `maybe_compact` triggers on **message count**
   (`runtime.py:857-868`) while every other budget is in tokens. Unify. Record compaction as a
   transition rather than a silent in-place rewrite (`:914-916`).
7. **Memory origin.** Facts have no origin field, so they cannot be trust-tagged. Add one.

### Tests Required

| Test | Asserts |
|---|---|
| `test_trust_tags_present.py` | every context item carries a tag |
| `test_untrusted_not_in_instruction_position.py` | T1 enforced structurally |
| `test_untrusted_cannot_alter_policy.py` | T3 — a repository item cannot change the tool set or policy |
| `test_context_deterministic.py` | same `ContextRequest` → same `Context` |
| `test_dropped_recorded.py` | truncation is reported |
| `test_graph_context_scoped.py` | only the current node's deps appear |
| `test_repo_map_symbol_level.py` | the turn path receives symbols, not only the skeleton |
| `test_compaction_token_based.py` | the trigger is tokens |

### Migration Risk

**Medium.** Trust tagging touches every context source. Mitigated by starting with tagging-only
(no behavioural change), then enforcing T1–T4 behind a flag. The repo-map change has a performance
dimension and must be measured first.

### Rollback Strategy

Flags `WISP_CONTEXT_TRUST` and `WISP_REPO_MAP_FULL`. Off → today's assembly.

### Completion Criteria

- Every context item is tagged; T1–T4 enforced.
- Assembly is deterministic and explainable.
- The symbol-level map reaches the model within budget, with the latency delta reported.
- Compaction is token-triggered and recorded.

---

## Phase 9 — Structured Delegation

### Objective

Make delegation a structured contract with enforced narrowing and transactional effects.

### Prerequisites

Phases 2, 5.

### Components Affected

`multi_agent/task.py`, `multi_agent/subagent_orchestrator.py`, `multi_agent/_runner.py`,
`multi_agent/dag.py`, `multi_agent/_circuit_breaker.py`, `auth/principal.py`, `runs/compensation.py`.

### Expected Changes

1. **Wire `derive_subagent`** (`auth/principal.py:62`). It is implemented, tested
   (`test_auth_principal.py:25-39`) and **never called in production** — so every child runs with
   `local_principal(...)`, `capabilities=None` = **unbounded** (`tool_executor.py:708`). This is the
   single most important fix in the delegation layer.
2. Structured `child_goal` (goal ref + inputs + required outputs) replacing `task: str`
   (`multi_agent/task.py:57`).
3. **Mandatory `result_schema`** — a result that fails schema is not admissible
   (`VERIFICATION` failure of the delegation).
4. **Transactional effects:** `WORKTREE` isolation applies the patch atomically or not at all;
   `SHARED` isolation records `EditRecord`s (`runs/compensation.py`) so effects roll back as a unit.
   This closes the audit's non-transactional partial-patch path
   (`subagent_orchestrator.py:919-924`).
5. **Typed failure** replacing prose markers (`_TRANSIENT_MARKERS:933`, `"timeout" in last_error:1577`).
6. **Wire or delete** `multi_agent/_circuit_breaker.py` — it is imported nowhere.
7. **Fix the latent budget defect:** `subagent_orchestrator.py:1316` writes `task.metadata`, but
   `SubagentContract` has no `metadata` field, so `_runner._budget_from_contract` (`:78`) never sees
   the DAG node budget — contradicting the comment at `_runner.py:54-67`. (Phase 10's F1 reappearing
   in a second location.)
8. **Retire `multi_agent/dag.py` into `wisp/graph/`** (also Phase 4 step 5) — one graph, not two.

### Tests Required

| Test | Asserts |
|---|---|
| `test_subagent_capabilities_narrowed.py` | a child cannot call a tool outside its declared capabilities |
| `test_structured_task_required.py` | a free-text delegation is rejected |
| `test_result_schema_admission.py` | a schema-violating result is not admitted |
| `test_shared_workspace_rollback.py` | a failed shared child rolls back its edits as a unit |
| `test_typed_subagent_failure.py` | failures carry a taxonomy class |
| `test_circuit_breaker_wired.py` | repeated child failures trip the breaker |
| `test_dag_node_budget_applied.py` | the declared node budget actually narrows |

### Migration Risk

**Medium.** Capability narrowing may break existing delegations that relied on inherited authority —
which is precisely the point, but it must be measured and staged.

### Rollback Strategy

Flag `WISP_DELEGATION_NARROWING`. Off → inherited authority (today's behavior).

### Completion Criteria

- Capability narrowing is applied and tested.
- A schema-violating result is rejected.
- Shared-workspace failure rolls back transactionally.
- One graph system.

---

## Cross-Cutting: The Reachability Requirement

**Every phase above must ship a reachability test.** The audit found eight complete, tested,
unreachable subsystems:

`wisp/runs/` · `wisp/contracts/` · `wisp/trace/` · `wisp/core/graph/` ·
`wisp/core/context/repomap.py` · `wisp/core/context/compactor.py` ·
`multi_agent/_circuit_breaker.py` · `derive_subagent`

The repository already has the right *pattern* for this — `tests/test_m4_governance_wiring.py` is a
**tripwire** that fails the moment an unwired control is wired, and
`tests/test_unwired_controls_inventory.py` ratchets the audit's own list. What is missing is a
**general** invariant.

**Recommended addition:** a structural test asserting that every durable subsystem has at least one
non-test importer reachable from the console entry point. This is the single highest-value new test in
the entire plan, because it prevents the dominant failure mode from recurring during the migration.

---

## Risk Summary

| Risk | Phase | Severity | Mitigation |
|---|---|---|---|
| Completion semantics become stricter | 3 | **High** | two-stage rollout (3a record, 3b gate) + measurement |
| Runtime graph mutation introduces non-termination | 5 | **High** | mandatory growth budget + determinism property test |
| A ninth unwired subsystem is created | all | **High** | reachability test per phase |
| False-absence conclusions | all | **High** | drive the real path before acting on any "missing" claim (`CONTEXT.md:543` records **nine** such errors) |
| Behavior drift in the gate chain | 2 | Medium | RED-first gate-order test + corpus equivalence |
| Hot-path overhead from journaling | 1 | Medium | measure; fail loud on journal write failure |
| Capability narrowing breaks live delegations | 9 | Medium | flag + measurement before default-on |
| Trust tagging changes model behavior | 8 | Medium | tagging-only stage first |
| Duplicate execution during migration | 1 | Medium | land idempotency before enabling replay |
| Two-graph divergence | 4, 9 | Medium | retire `multi_agent/dag.py` |
| False-positive stagnation | 7 | Low-Medium | N consecutive evaluations; strict progress definition |

---

## Verification Discipline

Every phase must end with, and report:

```bash
# Gates (both must be green)
/opt/anaconda3/envs/litllm/bin/ruff check wisp/
uv run --no-project --with "mypy==2.3.1" mypy

# Focused tests for the phase
env -u PYTHONPATH .venv/bin/python -m pytest <phase tests> -q -p no:cacheprovider

# Changed-subsystem regression
env -u PYTHONPATH .venv/bin/python -m pytest <affected subsystem tests> -q -p no:cacheprovider
```

**Standing rules carried forward from the engagement (`CONTEXT.md:531-538`):**

- Do **not** reopen closed work unless new evidence proves it wrong.
- Find the canonical authority first: map implementations → map consumers → identify semantic
  differences → identify the authoritative behaviour → decide if canonicalization is safe → define the
  target contract → **only then** modify.
- Do not manufacture a closure to improve a score. The goal is architectural truth.
- **Never claim the full suite passes unless it actually does.** Report the set, not the verdict.
- A canonicalization without a guard is not a canonicalization.

---

## What Must Not Be Done

| Prohibited | Reason |
|---|---|
| Rewrite the turn loop | It works; the target extends it |
| Replace `ToolExecutor`'s gate chain | It is the strongest subsystem in the codebase |
| Introduce a second authorization model | G1 measured the cost of two models (`PHASE_10_AUTHORIZATION_PARITY.md`) |
| Introduce a second outcome classifier | `OutcomeClass` is canonical and AST-enforced |
| Create a new graph package | `wisp/graph/` is the graph |
| Rename modules to match the research report | Brief §34 |
| Build a type with no consumer | The repository's dominant defect, eight instances found |
| Delete `wisp/core/graph/` without deciding | It contains the only stagnation detector — wire it, then decide |
| Implement all recovery rungs at once | Rungs 4–5 are blocked on Phase 5 |
| Add a distributed scheduler | No requirement found |
