# WISP — MIGRATION STATUS

**Living ledger for the Persistent Graph Loop migration.**

| Field | Value |
|---|---|
| Baseline commit | `83b10af6b5336b2b65361b47c723f87347633c3b` — *refactor: canonicalize run state, containment, and error classification* |
| Branch | `main` |
| Working tree at P0 start | 33 modified tracked files, 50 untracked (pre-existing; not caused by this migration) |
| Plan of record | `WISP_MIGRATION_PLAN.md` |
| Decision log | `WISP_ARCHITECTURE_DECISIONS.md` |
| Execution mode | Continuous self-directed implementation |

### Commits

| Commit | Scope |
|---|---|
| `cfe6f0f` | `docs:` audit (9 documents), migration plan, ledger, decision log, P0–P2 reports — 14 files |
| `744d081` | `feat:` P0 + P1 + P2 implementation and tests — 17 files, 119 new tests |

> **Scope caveat on `744d081`.** `wisp/config.py`, `wisp/composition.py`,
> `wisp/core/runtime.py` and `wisp/tool_executor.py` already carried uncommitted changes from before
> this work. They are included because they are interleaved with this work in the same hunks, and the
> commit body says so explicitly. Separating them would require reverse-engineering changes this
> migration did not make.

**Status vocabulary:** `NOT STARTED` · `IN PROGRESS` · `COMPLETE` · `BLOCKED` · `PARTIAL` · `SUPERSEDED`

---

## 1. Phase ledger

| Phase | Name | Prereq | Status | Report |
|---|---|---|---|---|
| **P0** | Wire the orphaned durable layer | — | `COMPLETE` (item 6 deferred to P1) | `PHASE_P0_REPORT.md` |
| **P1** | Journal turn transitions | P0 ✅ | `COMPLETE` (item 3 deferred to P2) | `PHASE_P1_REPORT.md` |
| **P2** | Introduce the proposal boundary | P1 ✅ | `COMPLETE` | `PHASE_P2_REPORT.md` |
| **P3** | Independent verification | P2 ✅ | `COMPLETE — stage 3a` (3b is a separate, measured decision) | `PHASE_P3_REPORT.md` |
| **P4** | Task graph from durable state | P3 ✅ | `COMPLETE` (item 5 deferred) | `PHASE_P4_REPORT.md` |
| **P5** | Runtime graph mutation | P4 ✅ | `COMPLETE` (item 5 deferred) | `PHASE_P5_REPORT.md` |
| **P6** | Recovery ladder | P5 ✅ | `READY TO START` | — |
| **P2** | Introduce the proposal boundary | P1 | `NOT STARTED` | — |
| **P3** | Independent verification | P2 | `NOT STARTED` | — |
| **P4** | Task graph from durable state | P2 | `NOT STARTED` | — |
| **P5** | Runtime graph mutation | P4 | `NOT STARTED` | — |
| **P6** | Recovery ladder | P5 | `NOT STARTED` | — |
| **P7** | Stagnation detection | P5 | `NOT STARTED` | — |
| **P8** | Context trust boundary | — (independent) | `NOT STARTED` | — |
| **P9** | Structured delegation | P2 | `NOT STARTED` | — |

Critical path: **P0 → P1 → P2 → P4 → P5 → P6**. P8 is independent and may start at any time.

---

## 2. P0 — Wire the Orphaned Durable Layer

### 2.1 Objective

Make durable state that **already exists and is already tested** reachable from the live turn path, so
every later phase has state to reason over.

### 2.2 Reconnaissance result — the defect is at the composition boundary only

The Phase 0 audit described P0 as "wire the orphaned durable layer". Repository evidence **narrows**
that considerably: the durable layer is not partially built. It is **fully built, fully tested, and
completely unreachable from production**.

| Component | Implementation state | Test state | Production reachability |
|---|---|---|---|
| `wisp/runs/record.py` — `RunState`, `LEGAL_TRANSITIONS`, `coerce_state` | complete | `test_runs_record.py`, `test_canonical_execution_state.py` | ✅ reachable (via `runs/__init__`) |
| `wisp/runs/store.py` — `SQLiteRunStore` | complete (148 LOC) | `test_runs_store.py` | ❌ **only** constructed by `wisp/task/cli.py` (standalone CLI) |
| `wisp/runs/scheduler.py` — `Scheduler` | complete (64 LOC) | `test_runs_scheduler.py` | ❌ **never constructed** — `background.py:98` builds it only when `run_store is not None`, and it never is |
| `wisp/multi_agent/background.py` — `_persist_create`, `_persist_status`, `recover()` | complete (652 LOC) | `test_runs_recover.py`, `test_background_agents.py:591` | ❌ all `_persist_*` return early at `background.py:165-166, 188-189` |
| `wisp/trace/span.py`, `wisp/trace/store.py` — `Span`, `SQLiteTraceStore` | complete | `test_trace_spans.py` | ❌ `SQLiteTraceStore` constructed only by `wisp/trace/cli.py:30` (**read-only viewer**) |
| `wisp/infra/tracing.py:74` — `new_span()` | complete | — | ❌ **never called anywhere in the tree** |
| `wisp/core/session.py` — `SessionEvent.tool_call_event` / `tool_result_event` / `assistant_message` / `compacted` | complete factories | `test_13h4`, `test_13h5` | ❌ **never called** — only `user_message`, `error`, `done` are ever appended |

**Exact construction sites with the missing argument:**

| File:line | Current | Required |
|---|---|---|
| `wisp/composition.py:192` | `BackgroundAgentManager(self.subagent_orchestrator)` | `run_store=` the store |
| `wisp/tool_executor.py:1666` | `BackgroundAgentManager(self.subagent_orchestrator)` | `run_store=` the store |

`UnifiedStore` (available as `CompositionRoot.store`, `composition.py:70`) already exposes every
primitive the durable layer needs: `bg_create` (586), `bg_get` (603), `bg_update` (631),
`bg_append_transition` (659), `bg_list_transitions` (671), `bg_claim_lease` (684), `idem_get` (697),
`idem_put` (704), `trace_append` (718), `trace_list` (752), `task_plan_put` (770), `task_plan_get`
(783), `bg_list` (790). **No new store primitives are required.**

### 2.3 Item-by-item status

| # | Plan item | Status | Evidence |
|---|---|---|---|
| 1 | Pass `run_store` at both `BackgroundAgentManager` sites | `COMPLETE` | `composition.py` `_create_run_store()` + `run_store=self.run_store`; `tool_executor.py` `run_store` ctor param + lazy fallback |
| 2 | Canonicalize `RunStatus` / `RunState` | `COMPLETE — no work required` | §2.5 (corrected finding, ADR-0003) |
| 3 | Wire `SQLiteTraceStore` + emit spans from the runtime | `COMPLETE` | `composition.py` `_create_trace_store()`; `runtime._record_turn_spans()`; `new_span()` now has a caller |
| 4 | Write `tool_call` / `tool_result` session events | `COMPLETE` | `_serialize_tool_exchanges(..., journal=)` returns events; `runtime._journal_turn_events()` |
| 5 | Handle `TOOL_CALL` in `Session.apply` | `COMPLETE` | `session.py` `TOOL_CALL` case + audit trail + `case _:` guard |
| 6 | A normal turn creates a `RunRecord` row | `DEFERRED to P1` | see §2.7 |

### 2.4 Rollback flags

Every P0 change is gated by one flag. Off → byte-for-byte today's behavior.

| Flag | Env var | Default | Gates |
|---|---|---|---|
| `durable_runs` | `WISP_DURABLE_RUNS` | `true` | `SQLiteRunStore` construction + per-turn `RunRecord` lifecycle |
| `session_event_fidelity` | `WISP_SESSION_EVENT_FIDELITY` | `true` | `tool_call` / `tool_result` / `assistant_message` event writes |
| `turn_spans` | `WISP_TURN_SPANS` | `true` | turn + tool-call span emission |

All three are read with `getattr(config, name, True)`, matching the established `thin_tools` precedent
(`core/stateless.py:1189`) so `SimpleNamespace` test doubles keep working.

### 2.5 Corrected audit finding — item 2 needs no work

The audit asserted `RunStatus` (7 values, `wisp/graph/types.py:37`) and `RunState` (8 values,
`wisp/runs/record.py:17`) were "divergent" and needed canonicalization **before** wiring.

**Repository evidence disproves this.** Executed:

```
RunStatus: ['awaiting_approval','cancelled','failed','paused','queued','running','succeeded']
RunState : ['awaiting_approval','cancelled','failed','paused','planning','queued','running','succeeded']
subset? True | extra: ['planning']
all coerce OK: True
```

`RunStatus` is a **strict subset** of `RunState` — the only asymmetry is `PLANNING`, which exists solely
in `RunState`. Every `RunStatus` value coerces cleanly through `coerce_state()`. **No coercion shim is
required, and wiring cannot create a divergence.** Recorded as ADR-0003.

This is the second instance of the directive's warning ("never assume a component is missing merely
because a document says it might be") — the first being `test_canonical_execution_state.py`, which
already exists as a ratchet.

### 2.6 Completion criteria

- [x] A turn writes a `RunRecord` — **background runs only**; foreground turns deferred to P1 (§2.7).
- [x] At least one `tool_call` and one `tool_result` event per tool-using turn in `session_events`
      (verified at the serializer boundary; not exercisable end-to-end here — F8).
- [x] `Session.apply` reconstructs a tool call from replay (round-trip test).
- [x] `trace_spans` non-empty after a normal session.
- [x] A reachability test exists for each wired path (RULE 11) — 29 tests.
- [x] **Zero new failures** in the full suite; no existing test weakened.
- [ ] `ruff` clean; `mypy` exit 0 — **not run**; neither tool is installed in this venv.

### 2.7 Item 6 — deferred, with reason

Plan item 6 asked that a **normal turn** create a `RunRecord` row. Reconnaissance showed this
overlaps P1 ("Journal turn transitions") almost entirely: a turn-level run record is only meaningful
once turn transitions are journaled, and P1 owns that. Implementing it in P0 would mean P0 writing
transition rows that P1 then redefines.

**What P0 does deliver for item 6:** the *mechanism* is now reachable — `SQLiteRunStore` is
constructed by the composition root, injected into both `BackgroundAgentManager` sites, and proven
end-to-end to write `background_runs` + `run_transitions` rows (see
`test_manager_persists_a_run_row_end_to_end`). Background runs are durable today.

**What remains for P1:** applying the same lifecycle to foreground turns. Recorded as a P1
prerequisite rather than silently dropped — the P0 completion criteria in the migration plan are
therefore met in part, and this is stated rather than glossed.

---

## 3. P1 — Journal Turn Transitions

### 3.1 Objective

Make the turn's **interior** durable, so a crash is recoverable and a turn is replayable — and give
tool actions a durable identity so a crash cannot cause a repeated effect.

### 3.2 Reconnaissance result — the plan's component list was wrong

`WISP_MIGRATION_PLAN.md` listed `core/stateless.py` (`_turn_inner`) as P1's first affected component,
on the premise that only the engine knows about dispatch. Repository evidence shows otherwise: the
engine **already yields the `tool_call` event before dispatching** (`stateless.py:535` yields,
`:812` executes), and the runtime consumes that generator. **`stateless.py` was never modified** —
the largest risk in P1 removed by reading the code instead of the plan (ADR-0012).

### 3.3 Item-by-item status

| # | Plan item | Status | Evidence |
|---|---|---|---|
| 1 | `tool_call` before dispatch, `tool_result` after | `COMPLETE` | incremental flush in `run_turn`'s stream loop; `_closed_exchange_events()` |
| 2 | Idempotency key `hash(task_ref, tool, canonical(args))` | `COMPLETE — different instrument` | `wisp/core/action_key.py` + `action_key` on both events + `Session.unresolved_actions()` (ADR-0010) |
| 3 | Journal replaces the snapshot as the primary record | `DEFERRED to P2` | §3.4 |
| 4 | Remove/implement the stale `runtime.py` idempotency comment | `COMPLETE — removed` | replaced with an accurate pointer to ADR-0010 |

### 3.4 Item 3 — deferred, with reason

Five production consumers read the session **blob** (`UnifiedStore.load_session`): `__main__.py`,
`supervisor.py`, `sdk.py`, `acp_session.py`, `server/routes/sessions.py`. Replay is a *different*
function (`SessionRepository.load_session`) with a different shape. Switching consumers carries a real
hazard: **pre-P0 sessions have no turn body in the log**, so a naive switch makes old sessions
reconstruct *worse* than the blob does. The safe shape is journal-first with blob fallback — its own
phase, its own tests. Recorded as P2's first prerequisite.

### 3.5 The `idempotency` table was deliberately NOT wired up

Three reasons (ADR-0010): `idem_put` is first-write-wins so one key cannot hold intent *and* result;
the table has no TTL or scope; and exactly-once is unachievable for an arbitrary tool, so a row
asserting "this ran" would produce **false success** (RULE 12). The journal carries a canonical
`action_key` on both sides instead, and `unresolved_actions()` reports the genuine ambiguity rather
than papering over it.

### 3.6 Completion criteria

- [x] A turn's interior is durable mid-turn — probe test sees the closed exchange before `finally`.
- [x] The mid-turn journal is replayable and provider-valid.
- [x] Rollback by one flag; flag-off final journal is byte-identical.
- [x] No event written twice; sequences unique, increasing, gapless.
- [x] **Zero new failures** — failure set identical to P0's.
- [ ] Killpoint test against a real SIGKILL — deferred (§3.4 / report §7.2).
- [ ] Journal overhead measured — not measured; the tool path cannot execute here (F8).
- [ ] `ruff` / `mypy` — not installed.

---

## 4. P2 — Introduce the Proposal Boundary

### 4.1 Objective

Make reasoning produce **proposals** that validation disposes, instead of model output reaching
effects directly.

### 4.2 Reconnaissance result — the plan's claim narrowed (4th time)

The plan said `controlling_layer` "is **discarded**". Evidence: it is *not* discarded — it is
interpolated into denial prose at `tool_executor.py:722,725` and `tools/registry.py:948`. The sharper,
accurate finding: **the verdict is recorded only for denials, and only as prose; an allowed call leaves
no trace at all.** The audit trail's allow-side writers (`log_auto_approved`, `log_explicit_approved`)
fire on the **approval** path (`tool_executor.py:942,947`), not the authority path — so `allow`,
`approval`, and "no gate ran" were mutually indistinguishable.

### 4.3 Item-by-item status

| # | Plan item | Status | Evidence |
|---|---|---|---|
| 1 | Wire the existing `ToolRequest` | `COMPLETE` | `wisp/core/proposal.py` `build_proposal()`; journaled as a `PROPOSAL` event |
| 2 | Wrap dispatch in a proposal carrying intent/provenance/idempotency | `COMPLETE` | `ToolRequest` carries `idempotency_key` (P1 `action_key`); provenance = the verdict row |
| 3 | Record the authorization verdict (allow **and** deny) | `COMPLETE` | `_audit_authorization()` (ADR-0013) |
| 4 | Do not re-implement any gate | `HONORED` | one insertion after the fork; 7-case corpus byte-identical |
| 5 | Emit a `ProposalOutcome` for every proposal, including rejections | `COMPLETE` | `build_outcome()` → `OUTCOME` event; a refusal is first-class |

### 4.4 The safety net earned its place

Writing `test_gate_order_corpus.py` **before** the change (as the plan requires) surfaced an
undocumented ordering fact: **a `read_only` denial is decided by the policy-engine gate, which runs
before the `authorize()` consult — so it names no controlling layer.** Pinned with an explanatory
comment. ADR-0014.

### 4.5 The inversion was not performed — deliberately (ADR-0015)

P2's objective reads two ways: (1) **invert** — insert a proposal stage between the model and the
gates; or (2) **record** — the gates already *are* validation, so make their disposition observable.
The plan leans (1) and lists `core/stateless.py` as affected, but the gates run **inside**
`ToolExecutor.execute`, downstream of the dispatch site the plan proposes wrapping — a boundary there
would sit *before* validation with no way to observe it. The migration's own constraint settles it:
*"The proposal layer adds a record, not a decision procedure."* P2 implements (2).

### 4.6 Two real bugs caught by the new tests (both mine)

1. `_closed_exchange_events` **hardcoded `journal=True`**, so the incremental writer ignored its own
   flag and wrote transcript events with `session_event_fidelity` off — breaking the one-flag-per-
   concern rollback contract (ADR-0002).
2. `journal_fidelity` was read **inside the `finally` block** but used in the stream loop above it →
   `UnboundLocalError` on every tool-using turn. Fixed by reading all three durable-record flags at
   **one** site: a flag read in two places is a flag that can disagree with itself.

### 4.7 Completion criteria

- [x] Every tool effect has a recorded proposal with a verdict and a layer
- [x] Gate order and outcomes provably unchanged on the corpus
- [x] No second authorization implementation introduced (AST-pinned: `execute()` consults `authorize()` exactly once)
- [x] Rollback by one flag, independent of the P0/P1 flags (independence pinned)
- [x] **Zero new failures** — failure set identical to P1's
- [x] New code reachable (RULE 11) — both call edges AST-pinned
- [ ] `ruff` / `mypy` — not installed

### 4.8 Records are audit-only

The transcript is rebuilt from `ASSISTANT_MESSAGE(tool_calls=…)` + `TOOL_RESULT`. If `PROPOSAL` or
`OUTCOME` also appended to `messages`, **replay would duplicate every tool reply**. `Session.apply`
records them in dedicated lists and never touches `messages` — pinned by `TestAuditOnly`.

---

## 5. P3 — Independent Verification (stage 3a)

### 5.1 Objective

Make "this succeeded" a **verdict against criteria backed by evidence**, produced by a component that
is not the one that acted.

### 5.2 The finding that shapes the design

The plan says to generalize the nearest existing analogue. Both existing verdict vocabularies were
examined and **neither can express the required verdict**:

| Existing | Why it cannot |
|---|---|
| `VerificationFloorGuard.resolved()` | a boolean; cannot say "I could not tell", and it is the **actor's own** bookkeeping |
| `VerificationResult.decision` (`graph/verifier.py:19`) | `("ALLOW","REJECT","RETRY","ESCALATE")` — a **router's** vocabulary; every value presumes a verdict was reached |

So the verdict vocabulary is new and total (`PASS`/`FAIL`/`INCONCLUSIVE`) and routing is **derived**
(`route_for()`), keeping the graph vocabulary as an output rather than a competitor. `INCONCLUSIVE`
routes to `RETRY`, never `ALLOW`.

### 5.3 Item-by-item status

| # | Plan item | Status | Evidence |
|---|---|---|---|
| 1 | `AcceptanceCriteria` (DETERMINISTIC / ARTIFACT / SEMANTIC, `required`) | `COMPLETE` | `wisp/core/acceptance.py` |
| 2 | `VerificationRequest` / `VerificationResult` with `INCONCLUSIVE` | `COMPLETE` | `CompletionVerdict` + `route_for()` |
| 3 | `Evidence` with provenance, generalizing `GraphArtifact` | `COMPLETE` | `Evidence` (content-addressed, `producer`, `observations`) |
| 4 | Retain and demote `VerificationFloorGuard`; keep invalidate-on-mutation | `COMPLETE` | `floor_guard_criteria/evidence/verdict()`; `TestFloorGuardRetained` (8) |
| 5 | Structural independence (L1/L2) | `PARTIAL` | `evaluate()` takes no transcript (pinned); evidence names its producer; a second model (L3) is not implemented — the plan makes it preferred, not required |
| 6 | Completion rule requires non-invalidated evidence | `NOT DONE` | **that is stage 3b** — the plan's staging; `turn_succeeded` is unchanged (pinned) |
| 7 | Wire `change_tracker.py` into evidence | `NOT DONE` | deferred with 3b |

### 5.4 Completion criteria

- [x] `INCONCLUSIVE` is a reachable, tested outcome
- [x] A synthetic false-success scenario is blocked
- [ ] The measured `INCONCLUSIVE` rate at 3b is reported before enabling — **not measured**; requires a working tool path (F8)
- [~] `test_verification_loop.py` and `test_verification_contract.py` pass unchanged — contract passes, loop has 5 **pre-existing** failures
- [x] **Zero new failures** (confirmed by rerun)
- [x] Rollback by one flag, default **off**
- [ ] `ruff` / `mypy` — not installed

### 5.5 Stage 3a does not gate

`turn_succeeded` still derives from terminal evidence alone (13-H5), and the floor guard still owns the
completion invariant. The verdict is recorded and **nothing consumes it** — pinned by
`TestStage3aDoesNotGate`. ADR-0016.

---

## 6. P4 — Materialize a Task Graph from Durable State

### 6.1 Objective

Turn a turn's durable state into an explicit, inspectable task graph, without yet allowing it to change
during execution.

### 6.2 Reconnaissance result

The plan's third item names the crux and the repository confirms it: `graph/scheduler.py::ready_nodes`
is a **pure function of `(graph, state)`** with no persistence anywhere — readiness was recomputed on
every pass and stored nowhere. Materializing it is what makes the graph persistent state rather than a
recomputation.

### 6.3 Item-by-item status

| # | Plan item | Status | Evidence |
|---|---|---|---|
| 1 | Reuse `wisp/graph/`'s types, validator, store and scheduler | `PARTIAL` | types + legality reused unchanged; **the STORE is not** — it opens its own SQLite DB, and a second DB would fragment the durable record (ADR-0019) |
| 2 | Map each turn's work to `GraphNode`s, one `AGENT` node per iteration | `COMPLETE` | `build_turn_graph()`; the runtime materializes one node per **closed tool exchange** + one terminal node, and says so — iteration boundaries are not observable, so the count is a lower bound |
| 3 | Materialize `READY` rather than recomputing it | `COMPLETE` | `ready` is a stored field; `divergences()` detects staleness; `apply_transition` re-materializes |
| 4 | `NodeTransition` as the only write path for node state | `COMPLETE` | AST-pinned in-module **and** tree-wide |
| 5 | Retire `multi_agent/dag.py` into `wisp/graph/` | `NOT DONE` | **deferred** — §7.4 |

### 6.4 Item 5 deferred, with reason

`dag.py` is on the **live `fanout` path**. Retiring it means re-plumbing `fanout` onto `wisp/graph/`'s
executor — a change to a working, load-bearing path whose own regression suite
(`test_13j1_fanout_contract_repair.py`, 13 failures) is **already red for environmental reasons**. Doing
it now would make a regression the migration caused indistinguishable from one that was already there.
Recorded as item **M8**, not silently dropped.

### 6.5 Completion criteria

- [x] A turn's work is fully represented as persisted graph rows
- [x] One transition API; the structural test proves no bypass
- [x] `test_graph_engine.py`, `test_graph_invariants.py`, `test_canonical_execution_state.py` pass
- [x] **Zero new failures** — failure set identical to P3's
- [x] Rollback by one flag, default **off**
- [x] New code reachable (RULE 11) — end-to-end persistence test drives a real turn
- [ ] `ruff` / `mypy` — not installed

---

## 7. P5 — Runtime Graph Mutation

### 7.1 Objective

**This is the phase that creates the Persistent Graph Loop property** — the graph changes during
execution.

### 7.2 Reconnaissance result — the plan's target was wrong, with unusually strong evidence

The plan names Layer B (`graph/types.py`, `executor.py`, `scheduler.py`, `validator.py`, `planner.py`).
Three facts made that the wrong move:

| Evidence | Consequence |
|---|---|
| `graph/scheduler.py::is_finished` lists terminal statuses **explicitly** | a new terminal state makes it return `False` forever — a run that never completes |
| `test_graph_fuzz.py` / `test_graph_races.py` / `test_graph_resume.py` **do not exist** | the plan's own safety net for that change is absent (F19) |
| Layer B's executor has **zero** references from `core/runtime.py` / `core/stateless.py` | mutating it would not create the property for the live loop |

### 7.3 Item-by-item status

| # | Plan item | Status | Evidence |
|---|---|---|---|
| 1 | Add the 7 missing states | `COMPLETE — superset` | `TaskNodeState` (14), pinned as a ratchet; `SKIPPED` no longer conflates two meanings (ADR-0021) |
| 2 | `NodeCreate` + `GraphExpand` | `COMPLETE` | `create_node()`, `expand()` — acyclic by construction |
| 3 | `GraphInvalidate` with cascade | `COMPLETE` | `invalidate()` — transitive; demotes stale `SUCCESS` |
| 4 | Preserve immutability; replan creates a NEW node | `COMPLETE` | `supersede()` — old node retained as `SUPERSEDED` with a pointer; both edge directions rewired |
| 5 | Extend the executor to accept a mid-run node | `NOT DONE` | **deferred** — §7.5 |
| 6 | Graph-growth budget | `COMPLETE` | `GraphGrowthBudget`, enforced on every mutation; the plan's named risk (non-terminating expansion) terminates |

### 7.4 Completion criteria

- [x] A node can be created, executed, invalidated and superseded during a run
- [x] Cascading invalidation is correct and tested
- [x] Determinism holds across insertion orderings
- [x] Growth is bounded and the bound is enforced
- [~] `test_graph_fuzz.py`, `test_graph_races.py`, `test_graph_resume.py` pass — **the three files do not exist** (F19)
- [x] **Zero new failures** — failure set identical to P4's
- [x] Rollback structurally — every mutation is a pure function, nothing on by default
- [ ] `ruff` / `mypy` — not installed

### 7.5 Item 5 deferred — with reason

The live turn path has **no graph-driven executor to extend**: the turn loop executes tools directly,
and the P4 graph is a *record* of that work, not its driver. Making the graph drive execution is a
change of **control**, not an added capability — the point at which the message list stops being
authoritative, which P4's rollback contract preserves. It should land **with** M9 (message list as a
projection of the graph) rather than before it. Recorded as item **M11**.

---

## 8. Findings log (migration-wide)

| # | Finding | Phase | Resolution |
|---|---|---|---|
| F1 | `test_canonical_execution_state.py` already exists — audit implied the ratchet was missing | P0 | Corrected; no work |
| F2 | `RunStatus` ⊂ `RunState` — audit claimed divergence requiring a shim | P0 | Corrected; no shim needed (ADR-0003) |
| F3 | `Session.apply` has no `TOOL_CALL` case **and** no wildcard — unknown event types are silently dropped rather than failing loud | P0 | Fixed (P0.3, ADR-0005) |
| F4 | The append-only session event log contains only `user_message` / `error` / `done`. It cannot reconstruct a turn, yet `load_session()` is the documented crash-recovery replay source (`runtime.py:371`) | P0 | Fixed (P0.2) |
| F5 | `runtime.py` carried a stale comment `# Cache result for idempotency (1h TTL)` with **no code beneath it** — the idempotency cache the durable layer provides (`idem_get`/`idem_put`) is unclaimed | P0 | **Resolved in P1** — comment removed; idempotency implemented at the action level instead (ADR-0010) |
| F6 | Two disjoint decision models coexist: `auth/decision.authorize()` (6 layers) and `infra/security.SecurityPolicy.check()` (4 layers) | — | Pre-existing; out of P0 scope |
| **F7** | **`SessionRepository.append_events` was dead code that could not work.** It did `with self._store.transaction() as conn: conn.execute(...)`, but `UnifiedStore.transaction()` yields the **store**, not a connection (`infra/store.py:317-327`) — so it raised `AttributeError: 'UnifiedStore' object has no attribute 'execute'` on every call. Nothing called it, so the defect was invisible until P0 needed it | P0 | Fixed in `session_repo.py` |
| **F8** | **Six declared dependencies are missing from the venv**: `jsonschema`, `numpy`, `aiohttp`, `tiktoken`, `prompt_toolkit`, `cryptography` (all listed in `pyproject.toml`). Because `_validate_tool_args` imports `jsonschema` inside a `try` and converts the `ModuleNotFoundError` into a validation-failure string (`stateless.py:2186-2199`), **every tool call in this environment is refused as `SCHEMA_INVALID` before it can execute** — a missing dependency silently degrades into a total tool outage | P0 | **NOT FIXED — environment gap.** Cannot install: no network (SSL cert verification fails). Blocks any end-to-end tool-execution test |
| F9 | `_persist_turn_state`'s 6-parameter signature is a pinned contract: `test_13h5_success_derivation.py::TestFlagCompatibility` wraps it positionally to observe `turn_succeeded`. Widening it breaks that guard | P0 | Respected — journaling moved to a separate method instead |
| F10 | `test_13h2_determinism.py` has 6 pre-existing failures at baseline (`TokenBatch` has no `.get`, and a `0 == 6` signal-count assert). Unrelated to P0 | — | Pre-existing; logged |
| F11 | `tests/test_api_key_security.py` fails at **collection** (starlette `TestClient` needs `httpx`, also missing) | — | Pre-existing; environment gap |
| **F12** | **Baseline comparison methodology.** The working tree carried 33 pre-existing modified tracked files at P0 start. Stashing only the six files P0 touched reverts them to **HEAD**, which discards the pre-existing uncommitted Phase-10 work in those same files (e.g. `tool_executor.py::_note_fetch_outcome` delegates to `is_error_outcome` in the working tree but substring-matches in HEAD). A HEAD-based "baseline" therefore reports three ratchet failures that are artifacts of the stash, not regressions. The true baseline is *working tree minus P0*, which this ledger cannot reconstruct after the fact | P0 | Recorded; every regression delta explained individually in `PHASE_P0_REPORT.md` §4.3 |
| **F13** | **P0's first journaling implementation had a real correctness bug.** A tool call with no reply journaled no `TOOL_RESULT`, so replay rebuilt an assistant `tool_calls` block with **no following tool message** — a transcript strict providers reject. Caught by `test_13h4`/`test_13h5`. Fixed: the placeholder reply is now journaled with a `synthesized: True` flag, so replay stays provider-valid while the record stays honest | P0 | Fixed; guarded by `test_interrupted_turn_replays_into_a_provider_valid_transcript` |
| **F14** | **The layered authorization verdict was recorded only for denials, and only as prose.** `controlling_layer` is interpolated into denial messages (`tool_executor.py:722,725`; `tools/registry.py:948`). The audit trail's allow-side writers fire on the **approval** path (`tool_executor.py:942,947`), not the authority path — so `allow`, `approval`, and "no gate ran" were mutually indistinguishable. The plan's claim that the field "is discarded" was itself imprecise | P2 | Fixed for both paths (ADR-0013) |
| **F15** | **A `read_only` denial is decided by the policy-engine gate, which runs BEFORE the `authorize()` consult** — so it names no controlling layer. Undocumented before P2; surfaced by writing the gate-order corpus RED-first. Relevant to the deferred proposal-boundary work: an outcome must be recorded even for denials that never reach `authorize()` | P2 | Pinned in the corpus with an explanatory comment (ADR-0014) |
| **F16** | `contracts/tool.py`'s `ToolRequest`/`ToolResult` were **producer-less and consumer-less** (only the re-export and their own test referenced them). `contracts/policy.py`'s `PolicyDecisionEnvelope` still is. `CanonicalEvent` IS wired (`transport/renderer.py`, `contracts/adapters.py`) | P2 | **Fixed for tool** (`wisp/core/proposal.py`); `PolicyDecisionEnvelope` still unwired |
| **F17** | **`tests/test_speculative_search.py::TestOracle::test_smallest_diff_wins_ties_broken_by_speed` is flaky under the full suite.** It asserts a diff-size ranking, passes 5/5 in isolation and 3/3 at file level, has zero coupling to the P3 surface, and **appeared once in a 129-failure run and was absent from an immediate rerun of the identical code**. Confirmed flaky rather than a regression by re-running the same tree | P3 | Logged; **not** caused by P3. The suite's failure count varies by ±1 run-to-run because of it |
| **F19** | **Three tests P5's completion criteria require do not exist**: `test_graph_fuzz.py`, `test_graph_races.py`, `test_graph_resume.py`. They are the plan's own safety net for changing Layer B's node vocabulary, and their absence is why that change was not made (ADR-0021) | P5 | Verified absent; recorded |
| **F18** | `graph/scheduler.py::ready_nodes` recomputed readiness on every pass and stored nothing — so the graph was a *view*, never state, and a divergence between it and any consumer would be silent | P4 | Fixed (ADR-0019/0020) |

---

---

## 9. Change log




| Date | Phase | Change | Tests |
|---|---|---|---|
| 2026-09-21 | P0 | Reconnaissance complete; tracking documents created | — |
| 2026-09-21 | P0 | Implementation: run-store wiring, turn-body journaling, `TOOL_CALL` replay, trace spans (6 files) | 29 new tests |
| 2026-09-21 | P0 | Fixed `SessionRepository.append_events` (dead code, never callable) — F7 | caught by new tests |
| 2026-09-21 | P0 | Fixed journaling of interrupted calls (placeholder must be journaled, flagged `synthesized`) — F13 | `test_interrupted_turn_replays_into_a_provider_valid_transcript` |
| 2026-09-21 | P0 | Updated 6 tests that pinned the pre-migration log shape / shed-history behaviour; each preserves or strengthens its intent | 78 passed in the 3 affected files |
| 2026-09-21 | P0 | **Regression verified: 131 → 128 failures/errors, 0 new.** Report: `PHASE_P0_REPORT.md` | full suite, 3 runs diffed |
| 2026-09-22 | P1 | Implementation: incremental exchange journal, canonical action keys, `unresolved_actions()` (4 files modified, 2 added) | 32 new tests |
| 2026-09-22 | P1 | Extracted `_group_exchanges` so the incremental writer and the turn-end serializer share ONE grouping rule | `test_grouping_helper_is_sequential_and_prefix_stable` |
| 2026-09-22 | P1 | Removed the stale `# Cache result for idempotency (1h TTL)` comment (F5) — resolved by ADR-0010, not by implementing it | — |
| 2026-09-22 | P1 | **Regression verified: 128 → 128, failure set byte-identical. 0 new.** Report: `PHASE_P1_REPORT.md` | full suite, `diff -q` |
| 2026-09-22 | P2 | RED-first gate-order corpus written and made green against the UNMODIFIED implementation (12 tests) | `test_gate_order_corpus.py` |
| 2026-09-22 | P2 | `_audit_authorization()` records the layered verdict for the allow path; ONE insertion after the allow/deny fork | 10 tests; corpus re-run unchanged |
| 2026-09-22 | P2 | Structural no-bypass invariant: authority consumers + consult/record arity + no direct `TOOL_IMPLS` reach | 8 tests |
| 2026-09-22 | P2 | **Regression verified: 128 → 128, failure set byte-identical. 0 new.** Report: `PHASE_P2_REPORT.md` | full suite, `diff -q` |
| 2026-09-22 | P2 | Completed items 1/2/5: `wisp/core/proposal.py` produces `ToolRequest`/`ToolResult`; journaled as audit-only `PROPOSAL`/`OUTCOME` events | 27 tests |
| 2026-09-22 | P2 | Two real bugs caught by the new tests: hardcoded `journal=True` in `_closed_exchange_events`; `journal_fidelity` read in the `finally` but used in the stream loop | flag-independence tests |
| 2026-09-22 | P2 | **Regression verified: 128 → 128, failure set byte-identical. 0 new.** Report: `PHASE_P2_REPORT.md` | full suite, `diff -q` |
| 2026-09-22 | P3 | Stage 3a: `wisp/core/acceptance.py` (criteria, evidence, verdicts) + retain-and-demote projection of the floor guard; `VERDICT` recorded, **not** gated | 60 tests |
| 2026-09-22 | P3 | `record_verdict` defaults **off** — unlike P0–P2, it would add a record to every existing caller's log | flag test |
| 2026-09-22 | P3 | **Regression verified: 0 new.** A first run read 129; an immediate rerun of the identical tree read 128, proving the `+1` flaky (F17) | full suite, twice, `diff -q` |
| 2026-09-22 | P4 | `wisp/core/task_graph.py`: materialized readiness, one validated transition API, journal projection | 35 tests |
| 2026-09-22 | P4 | `TASK_GRAPH` + `NODE_TRANSITION` journal events (audit-only) + `Session.rebuild_task_graph()` | projection tests |
| 2026-09-22 | P4 | `task_graph` flag defaults **off** — the message list remains authoritative (the plan's rollback contract) | flag test |
| 2026-09-22 | P4 | **Regression verified: 128 → 128, failure set identical to P3. 0 new.** Report: `PHASE_P4_REPORT.md` | full suite, `diff -q` |
| 2026-09-22 | P5 | Extended node vocabulary (`TaskNodeState`, 14 states) as a **superset** of `NodeStatus`; Layer B untouched | 14 tests |
| 2026-09-22 | P5 | `create_node` / `expand` / `invalidate` (transitive cascade) / `supersede` (both edge directions) + enforced growth budget | 42 tests |
| 2026-09-22 | P5 | Three bugs found in my own implementation: `str, Enum` ≠ `StrEnum`; `TaskNode` did not coerce its status; `supersede` rewired one direction | caught by the new tests |
| 2026-09-22 | P5 | **Regression verified: 128 → 128, failure set identical to P4. 0 new.** Report: `PHASE_P5_REPORT.md` | full suite, `diff -q` |

### 9.1 Regression summary

| Run | Failures + errors |
|---|---|
| HEAD baseline (`83b10af`) | 131 |
| P0 first implementation | 134 (**6 new**) |
| **P0 final** | **128 (0 new)** |

Remaining 128 are pre-existing/environmental (missing `jsonschema` and `httpx` dominate). The true
baseline (*working tree minus P0*) could not be reconstructed — see F12 and `PHASE_P0_REPORT.md` §4.4.
