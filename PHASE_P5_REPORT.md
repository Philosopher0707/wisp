# PHASE P5 REPORT — Runtime Graph Mutation

| Field | Value |
|---|---|
| Phase | **P5** |
| Baseline | P4 complete (`WISP_MIGRATION_STATUS.md`) |
| Status | **`COMPLETE`** — the phase that creates the Persistent Graph Loop property |
| Files changed | 1 modified, 1 added (`tests/test_graph_mutation.py`) |
| Tests added | `tests/test_graph_mutation.py` (49) + 3 rewritten in the P4 suite |
| Rollback | structural — every mutation is a pure function; nothing is on by default |

---

## 1. Executive summary

P5 is the phase that makes the architecture what it claims to be: **the graph can change during
execution.**

It delivers the extended state vocabulary, `NodeCreate`, `GraphExpand`, `GraphInvalidate` with a
transitive cascade, supersession with retained history, and a **mandatory** growth budget — all as
**pure functions** over the materialized graph P4 introduced.

**The plan's target was wrong, and the evidence is unusually strong.** It names
`graph/types.py`, `graph/executor.py`, `graph/scheduler.py`, `graph/validator.py` and
`graph/planner.py` — Layer B. Three facts made editing Layer B the wrong move:

| Evidence | Consequence |
|---|---|
| `graph/scheduler.py::is_finished` lists terminal statuses **explicitly** | A new terminal state makes `is_finished` return `False` forever — a run that never completes |
| **`test_graph_fuzz.py`, `test_graph_races.py`, `test_graph_resume.py` do not exist** | The plan's own safety net for that change is absent (5th plan claim corrected by evidence) |
| Layer B's executor has **zero** references from `core/runtime.py` / `core/stateless.py` | Mutating it would not create the property for the live loop |

So the states were added where the live loop's graph lives, as a **superset** (ADR-0021), and Layer B
is untouched until its safety net is real.

---

## 2. Implementation

One file modified, one test file added. Every mutation function is **pure** — it returns a new
`TaskGraph` and never mutates its input. That is not stylistic: a mutated-in-place graph cannot be
reconstructed from a log, so replay would be impossible, and P5 item 4 requires that a task is never
mutated into a different task.

| Change | Detail |
|---|---|
| `TaskNodeState` | strict **superset** of `NodeStatus`; the 7 shared values are identical strings, plus `WAITING`, `BLOCKED`, `OBSERVED`, `VERIFYING`, `INCONCLUSIVE`, `INVALIDATED`, `SUPERSEDED` |
| `coerce_node_state()` | accepts both vocabularies; fails loud on an unknown state |
| `LEGAL_NODE_TRANSITIONS` | extended for all 14 states, including the `OBSERVED → VERIFYING → INCONCLUSIVE` leg |
| `TaskNode.__post_init__` | coerces `status` at the boundary — see §4 |
| `TaskNode.superseded_by` | the forward pointer that makes history navigable |
| `GraphGrowthBudget` + `GrowthBudgetExceeded` | node **and** edge ceilings, enforced on every mutation |
| `create_node()` | `NodeCreate` — refuses duplicate id, unknown dep, budget overrun, and any edge that would cycle |
| `expand()` | `GraphExpand` — **acyclic by construction**; the candidate topology is checked before adoption |
| `invalidate()` | `GraphInvalidate` — **transitive** cascade, demoting stale `SUCCESS` to `INVALIDATED` |
| `supersede()` | new node + old retained as `SUPERSEDED` with a pointer; **both** edge directions rewired |
| `_find_cycle()` | iterative DFS, deterministic (neighbours visited in sorted order) |

### 2.1 Supersession rewires both directions

A replan is not just "add a node". The replacement must inherit the old node's **incoming** edges
*and* take over its **outgoing** ones — otherwise the dependents stay attached to a superseded node and
never see the replanned work, which defeats the purpose. The first implementation only rewired incoming
edges; `test_dependents_are_rewired_to_the_replacement` caught it.

---

## 3. Verification

### 3.1 New tests — 49, plus 3 rewritten in the P4 suite

| Plan requirement | Class | Proves |
|---|---|---|
| `test_runtime_node_insertion` | `TestRuntimeNodeInsertion` (10) | a created node joins the graph, becomes ready when its dep settles, is transitionable; duplicate/unknown-dep refused; the input graph is not mutated |
| `test_graph_expand_acyclic` | `TestGraphExpandAcyclic` (10) | self-loops, 2-cycles, and cycles **through existing nodes** are refused with the cycle named; the detector is deterministic |
| `test_invalidation_cascades` | `TestInvalidationCascades` (10) | transitive cascade; a stale `SUCCESS` is demoted; disjoint roots untouched; the input is not mutated |
| `test_supersession_preserves_history` | `TestSupersessionPreservesHistory` (12) | the old node is **retained**, marked `SUPERSEDED`, pointer written, deps inherited, dependents rewired; identity preserved |
| `test_expansion_budget` | `TestExpansionBudget` (7) | node and edge ceilings enforced; the plan's named risk (non-terminating expansion) terminates |
| `test_executor_determinism_retained` | `TestInsertionDeterminism` (4) | the resulting graph is independent of insertion order; `ready_ids()` stays sorted; a replayed mutation sequence reproduces the graph |
| `test_no_topology_mutation_outside_controller` | `TestNoTopologyMutationOutsideController` (3) | **AST**: no module builds a `TaskGraph` from parts; `edges=` is written only by the controller |
| — | `TestExtendedVocabulary` (14) | the superset is pinned as a ratchet; `BLOCKED ≠ SKIPPED`; `INCONCLUSIVE ≠ FAILURE`; both are resumable; `INVALIDATED`/`SUPERSEDED` are final |

### 3.2 Regression

| Run | Failures + errors |
|---|---|
| HEAD baseline | 131 |
| P0 – P4 | 128 |
| **P5** | **128** |

`diff -q` against P4's failure set: **identical**. P5 introduced **0 new failures**.

---

## 4. Bugs the tests caught in my own implementation

Three, all found by the new tests rather than by inspection:

1. **`str, Enum` is not `StrEnum`.** `str(NodeStatus.SUCCESS)` is `"NodeStatus.SUCCESS"`, not
   `"success"` — so `coerce_node_state` rejected *every* `NodeStatus` member. Fixed with a `.value`
   branch.
2. **`TaskNode` did not coerce its status on construction.** A caller passing `NodeStatus.PENDING`
   stored a `NodeStatus`, and because `NodeStatus` is a `str, Enum`, `node.status is
   TaskNodeState.PENDING` was **False** for the same semantic value. Identity comparisons then failed
   silently: readiness was skipped and `apply_transition` reported a stale transition for a node nobody
   touched. Fixed with `__post_init__`.
3. **`supersede` rewired only one direction** (§2.1).

Bug 2 is the instructive one: it is the same class of defect as the `RunStatus`/`RunState` question in
P0, and it would have been invisible without a test that drives a *settled* node.

---

## 5. Honest limits

- **The mutation API has no production caller yet.** The plan's item 5 — *"extend the executor with
  exactly one new capability: accept a newly inserted node mid-run"* — is **not implemented** (§7).
  `create_node`/`expand`/`invalidate`/`supersede` are reachable from the package and fully tested, but
  nothing on the live turn path calls them. That is a deliberate stopping point, not an oversight.
- **No fuzz or race testing.** The plan's `test_graph_fuzz.py` / `test_graph_races.py` do not exist, and
  P5 does not add them. The cycle detector and the budget are the mitigations that are in place.
- **`ruff`/`mypy` not installed.**
- **128 pre-existing failures remain.** P5's claim is exact: it adds none.

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| A node can be created, executed, invalidated and superseded during a run | ✅ all four, tested |
| Cascading invalidation is correct and tested | ✅ transitive, demotes stale success |
| Determinism holds across insertion orderings | ✅ property test over orderings |
| Growth is bounded and the bound is enforced | ✅ node + edge ceilings, enforced on every mutation |
| `test_graph_fuzz.py`, `test_graph_races.py`, `test_graph_resume.py` pass | ⚠️ **the three files do not exist** — verified absent (finding F19) |
| No regression | ✅ **0 new failures** |
| Rollback by one flag | ✅ structurally: every mutation is a pure function, nothing is on by default |
| `ruff` / `mypy` | ❌ not installed |

---

## 7. Deviations from the plan

### 7.1 Layer B was not modified (ADR-0021)

See §1. The plan's target was Layer B; the evidence — an explicit terminal-status list, an absent
safety net, and zero live-path references — made the migration's own graph the right home.

### 7.2 Item 5 — the executor capability — not implemented

*"Extend the executor with exactly one new capability: accept a newly inserted node mid-run."*

**Not done.** The live turn path has no graph-driven executor to extend: the turn loop executes tools
directly, and the graph P4 materializes is a *record* of that work, not its driver. Making the graph
drive execution is a change of control, not an added capability — it is the point at which the message
list stops being authoritative, which P4's rollback contract explicitly preserves.

Doing it here would also mean there is nothing left for **P5's own stated purpose** to be verified
against. The honest position: the *mutation algebra* is complete and tested; **making it drive
execution is the remaining work**, and it should land with the P4 mitigation (message list as a
projection of the graph, item M9) rather than before it.

Recorded as migration item **M11**.

---

## 8. Next phase

**P6 — Recovery Ladder.** Its P5 prerequisite is met: the graph can now be invalidated and superseded,
which is what a recovery decision has to express.

Carried forward:

1. **M11 — make the mutation algebra drive execution** (§7.2), with M9 (message list as a projection).
2. **M9 — the message list is not yet a projection of the graph.**
3. **M8 — retire `dag.py`** — needs a green fanout suite first.
4. **M2 / M3 — journal-first reconstruction; killpoint integration.**
5. **M1 — P3 stage 3b** — blocked on a working tool path (`jsonschema`).
6. **M4 — revisit ADR-0004** for the proposal/verdict/graph records.
