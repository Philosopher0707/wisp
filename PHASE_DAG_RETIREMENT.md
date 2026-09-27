# PHASE — DAG RETIREMENT (Deliverable 2, item M8)

**Deliverable:** retire `multi_agent/dag.py` into `wisp/graph/`.
**Outcome:** **SURVEYED AND DECIDED — `DEPRECATE`, not remove.** The removal is blocked on a
**measured semantic divergence**, not on work. The residual is **open and tripwired**.
**Baseline:** `40cfa52` (no drift; §0 of `PHASE_GATE_ENABLEMENT.md`).

---

## 1. The caller map

| Caller | Site | What it uses |
|---|---|---|
| `wisp/tools/orchestration.py` | `:166` | `TaskDAG`, `TaskNode` — `orchestrate_dag` builds a DAG from the tool spec, calls `dag.validate()`, then `orch.run_dag(dag, max_parallelism=…)` (`:214`) |
| `wisp/multi_agent/subagent_orchestrator.py` | `:1307` | `DAGScheduler` — inside `SubagentOrchestrator.run_dag` (`:1287`) |
| `tests/test_dag_cancel.py` | `:14` | `DAGScheduler`, `TaskDAG`, `TaskNode` — incl. outer-cancellation |
| `tests/test_spawn_fanout.py` | `:462` | `DAGResult` |
| `tests/test_spawn_fanout.py` | `:638` | `TaskDAG`, `TaskNode` |
| `wisp/graph/validator.py:4`, `wisp/graph/__init__.py:9` | prose | *"mirrors `wisp.multi_agent.dag.TaskDAG.validate` (Kahn)"* — a documentation reference, not an import |

**Two production callers. Five test references. Two prose references.** No other path reaches `dag.py`.

## 2. The behaviour map

Every behaviour `dag.py` provides, against `wisp/graph/`:

| Behaviour | `dag.py` | `wisp/graph/` | Disposition |
|---|---|---|---|
| node + dependency declaration | `TaskNode` / `TaskDAG.add_node` | `GraphNode` / `Graph` (`types.py:116,195`) + `dsl.graph_from_dict` | equivalent (graph adds types, contracts, schemas) |
| unknown-dependency detection | `validate()` — **but mis-reports it as a cycle** (§4) | `_validate_structural` (`validator.py:42`) — names the real cause | **superior**; the `dag.py` behaviour is a *defect*, pinned |
| cycle detection (Kahn) | `validate()` (`dag.py:80-107`) | `_validate_cycles` (`validator.py:106`) | equivalent on the shared property (§4) |
| **"is this a valid DAG?"** | non-empty? **no**; reachability? **no** | non-empty **required**; every node reachable from the entrypoint **required** | **DIVERGENT — the blocker.** §3 |
| topological levels | `topological_levels()` — precomputed partition | `scheduler.ready_nodes` — dynamic | equivalent in effect, different shape |
| level-by-level parallel execution | `DAGScheduler.execute` (semaphore) | `GraphExecutor.run` (`executor.py:115`) | **superior** — dynamic scheduling, retries, joins, bounded cycles |
| per-node timeout | `timeout_per_node` | `_guard` (`executor.py:900`) + `RetryPolicy` | equivalent (graph adds retry policy) |
| dataflow between nodes | `node.metadata["_dep_results"]` | `EdgeMapping` (`types.py:149`) + `_resolve_path`/`_assign_path` | equivalent (graph is declarative) |
| descendants of a failed node never run | `_block_descendants` (`dag.py:219`) | `scheduler.blocked_by_failure` (`scheduler.py:47`) | equivalent |
| per-node budget | via `node.metadata["budget"]` (in the orchestrator) | `BudgetPolicy` (`types.py:80`) + `_over_budget` | equivalent |
| result object | duck-typed `_FallbackResult` | `NodeResult` / `NodeFailure` | equivalent (graph is typed) |
| run result | `DAGResult` | `GraphRun` (`types.py:342`) | equivalent |
| durability / audit / artifacts | **none** | `GraphStore`, `GraphSecurityAuditor`, `ArtifactStore` | **graph only** — a capability, not a duplicate |

**Nothing in `dag.py` is unique.** Every behaviour has an equivalent, and the graph's is equal or
superior in eleven of twelve rows.

## 3. The measured divergence — the blocker

Driven over a 9-case corpus (`.workbuddy-ai/memory/post-m13-gate-enablement/dag_equivalence_probe.py`),
comparing `TaskDAG.validate()` against `validate_graph(dag_to_graph(…))`:

| Case | `TaskDAG.validate()` | `validate_graph` | Agree? |
|---|---|---|---|
| `single`, `chain`, `diamond` | valid | valid | ✅ |
| `cycle2`, `self_cycle`, `cycle3` | INVALID | INVALID | ✅ |
| `unknown_dep` | INVALID | INVALID | ✅ |
| **`empty`** | **valid** | **INVALID** — *"graph has no nodes"* | ❌ |
| **`disconnected`** (two independent roots) | **valid** | **INVALID** — *"unreachable nodes from 'a': b"* | ❌ |

**They agree on cycles and unknown dependencies, and disagree on what a valid DAG *is*.** `wisp/graph/`
requires a single reachable entrypoint; `TaskDAG` is a general partial order and permits disconnected
components. A disconnected DAG is a **legitimate `orchestrate_dag` input** (two independent chains).

**Therefore a naive re-point is not a refactor — it is a behaviour change to a live, model-callable
tool.** Re-pointing `orchestrate_dag` onto `validate_graph` would reject inputs it accepts today.

Two further measured facts:

- **`TaskDAG.validate()` mis-reported an unknown dependency as a cycle — REPAIRED 2026-09-27.** `in_degree`
  was computed from the reverse-edge map, so an unknown dep inflated the count but could never be dequeued
  — the dependent never reached degree 0, and `unknown_dep` yielded **both** `Node 'a' depends on unknown
  'nope'` **and** `Cycle detected involving: a`. The in-degree now counts only edges whose source exists,
  so the false second message is gone and the verdict is unchanged. The `DEFECT-PIN` became a `FIXED-PIN`
  (`tests/reliability/test_dag_retirement_contract.py`), as that pin instructed.
- **`wisp/graph/compat.py::dag_to_graph` is test-only.** No production caller (`git grep` over `wisp/`:
  only `tests/test_graph_engine.py`). The intended lowering path exists and is unwired — the same
  *written-but-unwired* pattern this repository has already diagnosed as its dominant pathology.

## 4. The decision — `DEPRECATE`, not remove

**Chosen: deprecation.** `multi_agent/dag.py` stays as the declared legacy entry point; `wisp/graph/`
is declared the graph engine.

**Why not removal, and why not now.** The removal is not a re-point; it is a **semantic reconciliation**:
which definition of a valid DAG wins. Three consequences follow, and each is its own decision:

1. Adopting the graph's definition **rejects inputs `orchestrate_dag` accepts today** — a live-path
   behaviour change, and the brief forbids treating it as a cleanup.
2. `GraphExecutor.run` **creates a durable run per call** (`store.create_run`, `executor.py:137`) and
   enforces a workspace-containment check. Re-pointing `orchestrate_dag` would therefore add run records
   and a containment rule to a tool that has neither — a second, independent behaviour change.
3. `validate_graph` returns **more error classes** (contracts, governance, resources, security), so the
   tool's refusal vocabulary would widen.

**Why deprecation is the honest smaller step.** It gives the *documentation* boundary the repository needs
(one engine named, one legacy entry point named, the blocker measured and pinned) without taking a
live-path semantics decision inside a retirement task. The brief's own rule applies: *do not manufacture
a closure; report the residual.*

## 5. The change that landed

| Change | Kind |
|---|---|
| `wisp/multi_agent/dag.py` module docstring — declares the ownership boundary, names the blocker, records the defect | **PROSE-ONLY** — verified: docstring-stripped AST **byte-identical** to HEAD's |
| `tests/reliability/test_dag_retirement_contract.py` — 10 tests in 4 classes | new guard |
| `AGENTS.md` — a module-map row for `dag.py` | docs |
| `WISP_MIGRATION_STATUS.md` — §6.4's update, the item-5 row, and the M8 row | ledger |

**The guard, in four parts** (each non-vacuously checked — a removed cycle check, a changed reachability
rule, and a "fixed" unknown-dep message each made the corresponding test fail):

1. **What the two agree on** — cycles and unknown deps must reach the same verdict in both. This is the
   property a future re-point may rely on, and it fails if either implementation drifts.
2. **The measured divergences**, pinned as `empty` and `disconnected`. If these start failing, the
   definitions have converged and **M8's removal is unblocked**.
3. **A `DEFECT-PIN`** for the unknown-dep-as-cycle mis-report, so the defect is documented and its repair
   trips the pin.
4. **Three tripwires on the residual** — the orchestrator still imports `.dag`, `orchestrate_dag` still
   imports `multi_agent.dag`, and `dag_to_graph` still has **no production caller**.

## 6. The residual

**M8 is not closed.** Two residuals remain open, pinned by §5's tripwires; the third was repaired:

- the removal itself, blocked on **which definition of a valid DAG wins**;
- `TaskDAG.validate()`'s unknown-dep-as-cycle mis-report — **REPAIRED 2026-09-27** (§3);
- `dag_to_graph`'s lack of a production caller.

Each tripwire fails the moment its condition changes, so the residual cannot be forgotten.

## 7. Verification

| | |
|---|---|
| **Fanout safety net** (the brief's requirement) | `test_13j1_fanout_contract_repair.py` + `test_13j_fanout_contract.py` + `test_dag_cancel.py` + `test_spawn_fanout.py` → **107 passed** |
| **The new guard** | **10 passed** |
| **The change is prose-only** | docstring-stripped AST identical to HEAD's: **True** |
| **Both guards together** | **22 passed** (this file's 10 + `test_gate_enablement_contract.py`'s 12) |
| **Gates** | `ruff check wisp/` → **11 errors**, unchanged; both new test files are clean |
| **Regression** | §8 |

### 7.1 The guard was wrong first, and the failure is recorded

`test_the_graph_lowering_has_no_production_caller` was written as a **bare string scan** over
`wisp/**/*.py`. It failed on its first full run — because this deliverable's own new docstring in
`wisp/multi_agent/dag.py` **names** `dag_to_graph` while describing the blocker. The scan counted prose
as a caller.

That is the **instrument defect** class the brief names (F41, F54, the `.pyc` purge, the previous
mission's P5/Q1): *a check that reports for a reason unrelated to its claim.* It was found because the
probe **ran**, not by reading it. Rewritten with `ast`: only an `ImportFrom` of the name, or a `Call` to
it, counts. **A string scan over a Python tree will read docstrings as code.**

## 8. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <40 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **Result** | **1219 tests — 1218 passed, 1 failed** (132.52 s) |
| **The one failure** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** — the failure set is identical in both directions |
| **Now-passing** | none |
| **Delta** | **+10** — all in `test_dag_retirement_contract.py` |

*(An intermediate run showed a second failure — §7.1's own guard. It is fixed and the final run above is
the record; the intermediate is named rather than hidden, because a failure that was edited away is the
thing a regression table must not conceal.)*

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

## 9. Honest limits

- **This is a scoped retirement, not a removal.** `multi_agent/dag.py` still exists and is still on the
  live path. §4 states why, with the measurement that forced it.
- **The corpus is nine cases.** It is sufficient to separate the two definitions (it finds both
  divergences and both shared properties) and it is committed in the probe; a larger corpus could find
  more.
- **The probe is a working artifact, not a committed instrument.** Its corpus is reproduced inside the
  guard test, so the *assertions* are committed even though the probe is not.
- **The `_reachable` rule was not investigated further.** Whether `wisp/graph/`'s single-entrypoint
  requirement is right *for the graph engine* is not M8's question, and changing it is a different
  decision.
