# PHASE — THE LAYER B BOUNDARY (Deliverable 1, ADR-0060)

**Deliverable:** decide what Layer B is — the driver, or a record — and close the question
that M11 and M8 name from opposite sides.
**Outcome:** **DECIDED — Position A. The turn loop is the driver; the graph is a record, and
the boundary is permanent.** Position B is rejected on measurement, not on preference.
**Baseline:** `HEAD` = `0504811` (no drift — 29 working-tree entries, matching `CONTEXT.md` §8).
**Report of record; the decision is ADR-0060; the guards are
`tests/reliability/test_layer_b_boundary.py` (16) plus `test_node_identity.py`'s contract.**

---

## 1. The driver questions, answered by measurement

All five were driven, not read. Two probes are committed as working artifacts under
`.workbuddy-ai/memory/post-m13-layer-b/`.

### 1.1 What does `wisp/graph/executor.py` get imported by, and from where?

`reachability_probe.py` — an AST import-graph over `wisp/**` that classifies every edge
**module-level or function-level**, because the reachability here is lazy.

| importer | site | what it is |
|---|---|---|
| `wisp/core/doctor.py` | `_check_graph_integrity()` | a **diagnostic** — imports `GraphExecutor` to assert it is importable and `validate_graph` callable |
| `wisp/graph/runner.py` | `default_executor()` | the only construction site |
| `wisp/graph/cli.py` | `_main()` | `wisp graph run\|resume\|execute` |
| `wisp/graph/api.py` | module level | Layer B's own typed SDK (`GraphHandle`) — **no importer** |
| `wisp/sdk.py` | `execute_proposal()` | the SDK path |
| `wisp/coding.py` | `run_coding_template()` | the coding template path |

`GraphExecutor` is imported **exactly once inside `wisp/`** — by `core/doctor.py`. Everything
that constructs one goes through `graph/runner.py::default_executor`.

**ADR-0019 / ADR-0021's claim.** Both say *"Layer B's executor has zero references from
`core/runtime.py` or `core/stateless.py`."* Measured:

| root | result |
|---|---|
| `wisp.core.stateless` | **true** — its import closure contains no `wisp.graph.*` module at all |
| `wisp.core.runtime` | **false as literally worded** — it reaches `wisp.graph.executor` via `get_doctor_report()` → `core.doctor.last_report` → `_check_graph_integrity()`, a pre-flight report for UI layers |

**The substantive claim survives; the literal one does not** (F103). The guard states both
halves, and the second is a *measurement*, not a reading: cutting the doctor edge must leave
the closure empty, and the test asserts the doctor edge exists so the exclusion is not
hiding an absence.

### 1.2 What does the executor's `run()` produce?

`GraphExecutor.run` is `async` → `_drive(...)` → `_final(...)`, a dict of
`{run_id, graph_id, status, error, …}` (`executor.py:1046`). Parsed, the module holds **no
identifier and no string literal** naming `messages`, `transcript`, `history` or
`conversation` — only a comment at `:806`: *"inputs: explicit mapping + artifact refs, never
whole transcripts"*. **The executor produces no message list**; ADR-0029's constraint is
intact, re-driven rather than cited.

### 1.3 Does `dag.py`'s retirement become forced or optional?

Under Position A it is **optional** — already `DEPRECATE` (`PHASE_DAG_RETIREMENT.md`), and
now with its blocker re-scoped (§3 R4).

### 1.4 What happens to `test_the_graph_still_does_not_drive_execution`?

It becomes the **permanent contract** (R2), and its reversal condition is stated in the test
itself. Its subject was narrower than the boundary — it scans `runtime.py` for
`.ready_ids`/`.ready_nodes` only — so the wider property (the import boundary, and the turn
engine too) is asserted in `test_layer_b_boundary.py`.

### 1.5 What happens to `dag.py`'s measured divergence?

It is **resolved by the decision, as an accepted difference** — see R4. It is no longer a
blocker awaiting a reconciliation.

---

## 2. The substantive question — the transition, named

The brief calls this the substantive question: *"Does the graph get constructed before the
turn and driven through it? Does the executor own the turn loop's iteration?"* Driven by
`transition_probe.py`:

| | |
|---|---|
| `wisp.graph.types.Graph` is `frozen=True` | a node **cannot** be appended mid-run — `FrozenInstanceError` |
| `GraphExecutor`'s public surface | `run`, `resume`, `cancel`, `register_function` — **no** growth API, by name or by AST |
| `GraphExecutor.run(empty_graph)` | **refused** — `invalid graph: graph has no nodes`, before any work |
| `TaskGraph → Graph` lowering in `wisp/` | **none.** `compat.py` lowers `TaskDAG → Graph`; nothing lowers Layer A's graph |

**The turn loop discovers its work as the model streams.** It cannot know which tools it will
call before the model speaks. `GraphExecutor.run(graph, inputs)` requires the **complete,
validated** graph up front, and `Graph` is immutable with no mid-run growth. So *"every tool
call is a node transition"* requires an executor that grows a graph **while driving it**, and
this tree does not have one.

**This is a different blocker from ADR-0029's, and it is stronger.** ADR-0029 found the strong
reading (*"the transcript projects from the graph"*) inexpressible because the graph carries
no payload. This finds Position B inexpressible because **the graph cannot be known before the
turn and cannot change during it**. ADR-0029's constraint is a second, independent reason — a
naive B would also have to copy the transcript into the nodes — but the first reason is
structural and does not mention payload at all.

---

## 3. The decision (ADR-0060)

**R1 — Layer A is the driver.** The iteration belongs to `WispAgentCore.turn` /
`AgentRuntime.run_turn`.

**R2 — the boundary is permanent, not an open item.** *"The graph drives execution"* is
rejected as a **target**, not deferred. M11's tripwire becomes the contract.

**R3 — the executor's callers are named, and none is the turn loop.** `graph/cli.py`,
`graph/api.py`, `graph/runner.py` (inside Layer B), and `sdk.execute_proposal`,
`coding.run_coding_template`, `core.doctor`'s integrity check (outside it). A new caller is a
decision, not an edit.

**R4 — `multi_agent/dag.py` remains the deprecated legacy entry point**, and its divergence
is an **accepted difference**. `empty` and `disconnected` inputs are legal for
`orchestrate_dag` and illegal for a compiled single-entrypoint graph because the two answer
different questions. **The removal is not owed by this decision**; choosing which definition
of a valid DAG wins is a change to a live model-callable tool and is its own decision.

### Why B is rejected rather than deferred — three independent measured costs

1. **The transition is not expressible** (§2). B is a different executor, not more work.
2. **It would need Layer A's mutation vocabulary inside Layer B.** Runtime growth lives in
   `core/task_graph.py` together with seven node states ADR-0021 kept out of `NodeStatus` —
   because `graph/scheduler.py::is_finished` (`:132-134`) and `_predicates_satisfied`
   (`:103-106`) list the settled statuses **explicitly**, so a new terminal status makes
   `is_finished` return `False` forever. **Re-verified at HEAD**, and unaffected by F102.
3. **It would put a second durable record on the turn path.** `GraphExecutor.run` calls
   `store.create_run(...)` (`executor.py:137`) into `GraphStore`'s **own** SQLite database
   (`graph/store.py:112-127`) and enforces a workspace-containment check the tool lacks.
   ADR-0019 exists precisely to keep one append-only journal.

### What the decision does not do

It does not claim `dag.py` is not a weaker duplicate — `PHASE_DAG_RETIREMENT.md` measured it
as equal-or-superior in eleven of twelve rows. It claims the **reconciliation is not owed**:
under Position A the duplicate is not on the critical path to anything, and re-pointing it
would change a live tool's accepted inputs.

---

## 4. The four non-violations, asserted

Each is pinned in `tests/reliability/test_layer_b_boundary.py`, and each was checked by
**breaking its property** and confirming the guard fails (§5).

| | assertion |
|---|---|
| **1** | `goal.PRECEDENCE` by **content** (eight rows 0–7, row 4's fatal clause, row 6 `GOAL_MET`), `VerificationFloorGuard`'s four methods, and `turn_succeeded` still derived from `terminal_outcome` |
| **2** | `wisp/graph/executor.py` contains no `messages`/`transcript`/`history`/`conversation` identifier or string literal — ADR-0029 at the *executor*, where the existing guard checks `TaskNode` |
| **3** | `ToolExecutor.execute`'s chain is `policy_hard_deny` → `authorize` → `_get_write_tools`, **from the AST** |
| **4** | the five audit-only kinds have **no `messages` append** in their `Session.apply` branch |

Non-violation 4 was **written wrong first and the failure is kept**: the first version asserted
that `Session.apply` does not *name* the audit-only kinds — but `apply` is a `match` over
`event.event_type`, so it necessarily names every one, and the guard failed on all five. That
is a guard whose claim and whose subject had drifted apart (the F92 class). The rewrite checks
the **branch body**, with a floor asserting every kind has a branch at all.

---

## 5. Non-vacuity

`nonvacuity_probe.py` breaks each property, confirms the guard **fails**, restores the file,
confirms the bytes are **identical**, and re-runs the guard as a control.

```
mutation                                                   file                       result   ok
----------------------------------------------------------------------------------------------------
the turn engine imports Layer B's executor                 wisp/core/stateless.py     CAUGHT   yes
the runtime reaches Layer B without the doctor             wisp/core/runtime.py       CAUGHT   yes
the turn loop consults `ready_nodes`                       wisp/core/runtime.py       CAUGHT   yes
a new module imports the executor                          wisp/transport/cli.py      CAUGHT   yes
`Graph` is unfrozen (a SIZE-PRESERVING mutation)           wisp/graph/types.py        CAUGHT   yes
the executor gains `add_node`                              wisp/graph/executor.py     CAUGHT   yes
precedence row 4's outcome changes                         wisp/core/goal.py          CAUGHT   yes
the executor handles a transcript                          wisp/graph/executor.py     CAUGHT   yes
a gate leaves `ToolExecutor.execute`                       wisp/tool_executor.py      CAUGHT   yes
an audit-only branch appends to `messages`                 wisp/core/session.py       CAUGHT   yes

10/10 guards non-vacuous
```

**Two traps were handled deliberately.** `__pycache__` is purged on **both** sides of every
mutation, because `Graph`'s `frozen=True → frozen=False` mutation is **size-preserving** and a
same-second restore leaves stale bytecode holding the mutated source — the documented trap
that reports the harness's own defect as the subject's. And two mutations were **rejected
during construction** for failing for the wrong reason: `frozen=Fals` (invalid Python — an
import error, not the guard's assertion) and a field named `_probe_messages` (which an
exact-name check cannot see, so it would have proved nothing).

---

## 6. Findings

### F101 — a probe that checked the wrong paths, and reported two present files as absent

The first check of ADR-0021's three safety-net files was a shell loop over
`tests/test_graph_fuzz.py`, `tests/test_graph_races.py`, `tests/test_graph_resume.py`. It
reported the first PRESENT and the other two **ABSENT**. All three are at
`tests/security/`. **The instrument's subject was wrong** — the wrong-subject sub-case
(`CONTEXT.md` §10, instance 4), found only because a later `find` contradicted it. A
`for f in …` loop over hand-typed paths cannot report an absence, because a typo and a missing
file are the same observation.

### F102 — ADR-0021's stated blocker was FALSE, and was false when it was written

ADR-0021 says the plan's safety net *"does not exist in the repository … **none** of the three
is in the repository."* Measured:

| file | tracked | added |
|---|---|---|
| `tests/security/test_graph_fuzz.py` | **yes** | 2026-09-10, `879a5b1` |
| `tests/security/test_graph_races.py` | **yes** | 2026-09-10, `879a5b1` |
| `tests/security/test_graph_resume.py` | **yes** | 2026-09-10, `879a5b1` |

The P5 landing is `e2da7f0` (2026-09-22) — **152 commits later**. So the three files predate
the phase that claimed they were missing, and **ADR-0021's reversal condition has been
satisfied since before it was written**. They are substantive: races (fan determinism, no
duplicate execution, cancel-never-success, budget under jitter) and resume (forged success
rows, corrupt checkpoints, cancellation races).

**What this does and does not change.** ADR-0021's *decision* (the superset) stands on its
**second** reason, which is independent and which this phase re-verified:
`is_finished` lists settled statuses explicitly, so a new terminal status makes it return
`False` forever. What does not stand is the ADR's *stated precondition*, and the reversal
condition it derived from it. **ADRs are append-only, so ADR-0021 is not edited** — ADR-0060
records the correction and the reason is restated in §3. `wisp/core/task_graph.py`'s docstring
repeated the same false claim and **is** corrected (PROSE-ONLY, proven — §7).

### F103 — "zero references from `core/runtime.py`" is no longer literally true

`core/stateless.py`: zero, at any depth. `core/runtime.py`: reaches `wisp.graph.executor`
through `get_doctor_report()` → `core.doctor.last_report` → `_check_graph_integrity()`. The
substantive claim holds; the literal one does not. The guard asserts both halves, so the next
reader does not have to re-derive which half is which.

### F104 — ADR-0057 has no index row

`WISP_ARCHITECTURE_DECISIONS.md`'s Decision index jumps **0056 → 0058**. ADR-0057 — the
WebSocket approval decision, and Deliverable 2's ADR — has a heading and no row, so a reader
scanning the index cannot find it. **Corrected here**: the row is added, marked as missing
until 2026-09-25. (This is the same record-integrity class as F97/F99/F100, and it is why
ADR-0060's own acceptance criterion names the index row explicitly.)

### An F85 instance, not a new finding — the canonical block's heading is stale by 2

Both blocks' headings say **1453 (1452 pass, 1 fail)**. Measured at the start of this phase:
**1455 (1454 pass, 1 fail)**. Two tests were added to a block member after the count was
taken. That is F85's class exactly, so it is reported as an instance rather than numbered;
both headings are re-measured in this change (§7).

---

## 7. Verification

| | |
|---|---|
| **The new guard** | `test_layer_b_boundary.py` — **16 passed** |
| **Non-vacuity** | **10/10 CAUGHT**, tree restored byte-identical, control green |
| **`task_graph.py` is prose-only** | docstring-stripped AST **identical**; recursive `co_code` **identical** |
| **The affected set** (6 files) | **169 tests — 168 passed, 1 failed** — the failure is **F38**, pre-existing |
| **Regression** | §7.1 |
| **Gates** | `ruff check wisp/` → **11 errors**, unchanged (F71). `mypy` not re-run (F71: 1844 in 228 files, unchanged by every recent chain) |

### 7.1 Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <the 6 affected files> -q -p no:cacheprovider
```

**169 tests — 168 passed, 1 failed.** The failure is
`test_node_identity.py::TestANodeReferencesItsWorkUnit::test_a_parallel_round_is_journaled_as_one_exchange_per_call`
— **F38**, present in the canonical block's baseline before this change and unchanged by it.

The canonical block (§7.2) is the wider measurement. The full suite was **not** run: F36 says
it cannot run in one process on this host, so the method is weaker than a two-run intersection
and is stated as such.

### 7.2 The canonical block, re-measured

| | |
|---|---|
| **before this change** | **1455 tests — 1454 passed, 1 failed** (F38) |
| **after** | **1471 tests — 1470 passed, 1 failed** (F38) — **+16**, all in the new file |
| **new failures** | **none**; **now-passing**: none |

The block gains one file (`tests/reliability/test_layer_b_boundary.py`), and **both** headings
(`CONTEXT.md` §11 and `AGENTS.md`) are re-measured in the same change that adds it — F85.
The heading said **1453** before this phase, so the correction is 1453 → **1471** across two
causes: a stale-by-2 count (F85 instance, above) and this phase's +16.

---

## 8. Honest limits

- **Position A changes no production behaviour.** The only `wisp/` change is a docstring
  correction. So this deliverable's "implementation" is a guard, a decision and a record —
  which is the honest shape of a decision whose subject already held.
- **The transition probe's four claims are about *this* tree.** A future executor that grows a
  graph mid-drive would falsify them; the reversal-condition tests are written to fail in
  exactly that case rather than to be edited.
- **`wisp/graph/api.py` has no importer.** Layer B's own typed SDK (`GraphHandle`:
  run/wait/status/cancel/resume/trace) is reachable from nothing — another instance of the
  written-but-unwired pattern this repository keeps diagnosing. **Named, not repaired**: it is
  Layer B's surface, and whether it is a public API or dead code is not this decision's
  question.
- **`test_the_orchestrator_still_imports_dag` in `test_dag_retirement_contract.py` is a bare
  string scan** (`assert "from .dag import DAGScheduler" in src`). It asserts *presence* of an
  exact line, so it is far less hazardous than an absence-detecting string scan, and this
  phase did not change it. Noted because it is the same instrument class this report's F101
  belongs to.
- **The divergence's other two costs were not re-driven** — that `GraphExecutor.run` creates a
  durable run per call (re-verified: `executor.py:137`) and enforces workspace containment
  (read), and that `validate_graph` returns more error classes. They come from
  `PHASE_DAG_RETIREMENT.md` §4 and are cited, not re-measured.
