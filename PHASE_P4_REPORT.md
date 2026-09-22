# PHASE P4 REPORT — Materialize a Task Graph from Durable State

| Field | Value |
|---|---|
| Phase | **P4** |
| Baseline | P3 complete (`WISP_MIGRATION_STATUS.md`) |
| Status | **`COMPLETE`** — item 5 (retire `dag.py`) deferred (§7) |
| Files changed | 3 modified, 1 added (`wisp/core/task_graph.py`) |
| Tests added | `tests/test_task_graph_materialization.py` (35) |
| Rollback | `WISP_TASK_GRAPH` (defaults **off**) |

---

## 1. Executive summary

P4 turns a turn's work into an explicit, inspectable graph — and makes that graph **persistent state
rather than a recomputation**.

The plan's third item names the crux precisely: *"`ready_nodes` computes readiness on each pass and
stores nothing. Materializing it is what makes the graph persistent state rather than a
recomputation."* Repository evidence confirms it — `graph/scheduler.py::ready_nodes` is a pure function
of `(graph, state)` with no persistence anywhere.

Four of the plan's five items landed. The fifth — retiring `multi_agent/dag.py` into `wisp/graph/` — is
**deferred with a reason** (§7).

| # | Plan item | Status |
|---|---|---|
| 1 | Reuse `wisp/graph/`'s types, validator, store and scheduler | ⚠️ **types + legality reused; the STORE is not** — ADR-0019, §3.1 |
| 2 | Map each turn's work to `GraphNode`s, one `AGENT` node per iteration | ✅ |
| 3 | **Materialize `READY`** rather than recomputing it | ✅ — the phase's substance |
| 4 | `NodeTransition` as the only write path for node state | ✅ — AST-pinned |
| 5 | Retire `multi_agent/dag.py` into `wisp/graph/` | ❌ **deferred** (§7) |

---

## 2. What was actually wrong

| Concern | Before | After |
|---|---|---|
| Node vocabulary | `graph/types.py::NodeType` / `NodeStatus` | reused **unchanged** |
| Readiness | `ready_nodes()` recomputed on every pass, stored nothing | `ready` is a **field** on each node, written by `apply_transition` |
| Write path | ~30 direct status mutations across the codebase, 11 unpersisted (Phase 0 audit) | `apply_transition()` is the only writer, and it validates |
| Persistence | `GraphStore` uses its own SQLite file | the session journal — so the graph is a **projection** of the durable record |

---

## 3. Implementation

Three files modified, one added.

| File | Change |
|---|---|
| `wisp/core/task_graph.py` | **new** — `TaskNode`, `TaskGraph`, `NodeTransition`, `LEGAL_NODE_TRANSITIONS`, `build_turn_graph()`, `materialize()`, `apply_transition()`, `replay_transitions()`, `divergences()` |
| `wisp/core/session.py` | `TASK_GRAPH` + `NODE_TRANSITION` event kinds (**audit-only**), and `rebuild_task_graph()` |
| `wisp/core/runtime.py` | materializes the turn's graph at turn end, behind `task_graph` |
| `wisp/config.py` | `task_graph` flag (env `WISP_TASK_GRAPH`), default **`false`** |
| `tests/test_task_graph_materialization.py` | **new** — 35 tests |

### 3.1 The store deviation (ADR-0019)

The plan says to reuse `wisp/graph/`'s **store**. This reuses its **types and legality discipline** but
journals through `UnifiedStore`. `GraphStore.__init__` opens **its own SQLite database**
(`graph/store.py:112-129`), and two reasons made a second database the wrong call:

1. It would fragment the durable record P0–P3 spent four phases consolidating. *"Which file holds the
   truth about this turn?"* should have one answer.
2. P4's stated risk is *"divergence between the graph and the message list"*, and the plan's own
   mitigation is to make the message list a **projection of the graph**. A graph that replays from the
   same journal as the transcript makes that possible; a graph in a separate database could only be
   *joined* to it.

**Consequence:** `Session.rebuild_task_graph()` reconstructs the graph from the log alone. The
persisted graph is a **cache**; the journal is the truth.

### 3.2 Ready is materialized — and its staleness is detectable

Materializing readiness buys inspectability at the cost of a value that can go stale. That cost is paid
openly: `divergences(graph)` reports any node whose stored `ready` disagrees with a fresh derivation.

`apply_transition` **re-materializes** on every write, so a status change cannot leave `ready` stale
behind it. The property is pinned two ways:

- `test_ready_is_a_stored_field` hand-builds a graph whose stored flag *disagrees* with what a
  derivation would produce, and shows `ready_ids()` believes the **stored** value. A recomputation
  cannot pass that test.
- `test_a_freshly_materialized_graph_has_no_divergence` and
  `test_readiness_is_rematerialized_by_a_transition` pin the integrity side.

### 3.3 `PENDING` may settle directly (ADR-0020)

The first state machine allowed only `PENDING → RUNNING | SKIPPED | CANCELLED`, and materializing a
graph **retroactively** from a finished turn immediately failed with
`illegal node transition: pending -> success`.

Allowing `PENDING` to settle directly is not laxity: a retroactively materialized graph has no observed
`RUNNING` step to record, and refusing the settlement would force a caller to write a transition that
never happened — manufacturing history to satisfy a state machine. Terminal states still have **no**
outgoing edges, so `success -> running` remains refused.

### 3.4 Honest node semantics

The runtime materializes **one node per closed tool exchange, plus one terminal node** — not one per
model iteration. Iteration boundaries are not observable from the event stream, and a content-only
iteration leaves no exchange. The node count is therefore a **lower bound** on iterations, and the
runtime comment says so. Inventing nodes for iterations nobody observed would be the graph equivalent
of a fabricated success.

---

## 4. Verification

### 4.1 New tests — 35, all passing

| Plan requirement | Class | Proves |
|---|---|---|
| `test_turn_materializes_graph` | `TestTurnMaterializesGraph` (8) | a real turn persists a `TASK_GRAPH` event with nodes; the flag off records none; nodes are `AGENT`, chained, round-trippable |
| `test_ready_materialized` | `TestReadyMaterialized` (9) | readiness is **stored** (a stale flag is believed), divergence is detectable, a transition re-materializes, materialize is idempotent and non-mutating |
| `test_single_transition_api` | `TestSingleTransitionApi` (8) | **AST**: `status` is written only inside `apply_transition`, in this module *and* across `wisp/`; illegal/stale/unknown transitions refused; every status is in the machine |
| `test_transition_persisted` | `TestTransitionPersisted` (3) | transitions reach the session log **and** the SQLite row; sequence numbers stay gapless |
| — | `TestGraphIsAProjection` (5) | replay rebuilds the graph from the log; order-independent; the graph never touches the transcript; the flag defaults off |
| `test_dag_retired_into_graph` | — | **not written** — item 5 deferred (§7) |

### 4.2 Regression

| Run | Failures + errors |
|---|---|
| HEAD baseline | 131 |
| P0 / P1 / P2 / P3 | 128 |
| **P4** | **128** |

Failure set `diff`-compared against P3's. P4 introduced **0 new failures**.

---

## 5. Honest limits

- **The graph is a lower bound on iterations** (§3.4), not a faithful model of the engine's loop.
- **`task_graph` defaults off**, so in a default configuration nothing is materialized. The flag is off
  because the message list remains authoritative — the plan's rollback contract — and because, like
  `record_verdict`, it would add records to every existing caller's log.
- **No divergence test against the live message list.** P4's stated risk is graph↔transcript
  divergence; the mitigation (message list as a projection of the graph) is not implemented, only
  *enabled* by journalling both from one log. That is P5's territory.
- **`ruff`/`mypy` not installed.**
- **128 pre-existing failures remain.** P4's claim is exact: it adds none.

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| A turn's work is fully represented as persisted graph rows | ✅ `TASK_GRAPH` + `NODE_TRANSITION` events; rows verified in SQLite |
| One transition API; the structural test proves no bypass | ✅ AST-pinned in-module and tree-wide |
| `test_graph_engine.py`, `test_graph_invariants.py` and `test_canonical_execution_state.py` pass | ✅ all three pass (they are in the 128-set only if listed; none are) |
| No regression | ✅ **0 new failures** |
| Rollback by one flag | ✅ `WISP_TASK_GRAPH`, default **off** |
| New code reachable (RULE 11) | ✅ end-to-end persistence test drives a real turn |
| `ruff` / `mypy` | ❌ not installed |

---

## 7. Item 5 deferred — retiring `multi_agent/dag.py`

The plan calls for retiring `dag.py` into `wisp/graph/` as *"a strictly weaker duplicate (no durability,
no audit, no artifacts, less validation)."*

**Not done, and not because it is large.** `dag.py` is on the **live subagent path** (`fanout` dispatches
through it), and the migration has just established that the graph's durable story is a *projection of
the session journal* — a property `dag.py`'s callers do not have. Retiring it means re-plumbing
`fanout`'s execution onto `wisp/graph/`'s executor, which is a change to a **working, load-bearing**
path with its own fanout regression suites (`test_13j1_fanout_contract_repair.py`, currently 13
pre-existing failures).

Doing that at the end of a long session, on a path whose test suite is already red for environmental
reasons, would make it impossible to tell a regression I caused from one that was already there. That
is precisely the condition under which the migration's own rule applies: **do not build downstream
against an unstable foundation.**

Recorded as migration item **M8** rather than silently dropped.

---

## 8. Next phase

**P5 — Runtime Graph Mutation.** Its P4 prerequisite is met: the graph exists, readiness is
materialized, and there is exactly one write path.

Carried forward:

1. **`dag.py` retirement** (§7, item M8) — needs a green fanout suite first.
2. **Journal-first reconstruction with blob fallback** (P1 §7.1, item M2).
3. **Killpoint integration** (P1 §7.2, item M3).
4. **Revisit ADR-0004** for the proposal/verdict/graph records (item M4).
5. **P3 stage 3b** — blocked on a working tool path (`jsonschema`), item M1.
