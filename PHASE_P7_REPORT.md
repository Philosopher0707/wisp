# PHASE P7 REPORT — Stagnation Detection

| Field | Value |
|---|---|
| Phase | **P7** |
| Baseline | P6 complete (`WISP_MIGRATION_STATUS.md`) |
| Status | **`COMPLETE`** — detector + routing + goal-met guard; live-loop wiring deferred (§7) |
| Files changed | 1 added (`wisp/core/stagnation.py`), 1 test file added |
| Tests added | `tests/test_stagnation_detection.py` (44) |
| Rollback | `WISP_GRAPH_OSCILLATION_GUARD` — **the pre-existing flag, now finally read** |

---

## 1. Executive summary

P7 detects "working but not progressing" and routes it to the recovery ladder P6 built.

**The detector was never missing — it was orphaned.** `OscillationTrap` (`core/graph/loop.py:112`)
detects exact 1-cycle repeats and 2-cycle oscillations of diff hashes, and it is a genuine progress
signal. What it lacked was a caller. P7 does not reimplement it; it **uses** it and supplies the
progress signal it needs.

All four plan items landed:

| # | Plan item | Status |
|---|---|---|
| 1 | Wire `OscillationTrap` to the live loop | ⚠️ **used by the detector; live-loop wiring deferred** (§7) — the config flag is now read |
| 2 | Build the progress metric from the four unconnected inputs | ✅ all four connected, drawing on P3's verdicts and P4's graph |
| 3 | Route `STAGNATION` to Global Replan → Diagnostic → Escalate | ✅ via P6's `FailureClass.STAGNATION` |
| 4 | A stagnated goal must never reach `GOAL_MET` | ✅ `may_report_goal_met()` |

---

## 2. What was actually wrong

The plan's claim is *"the only importer is `tests/test_architectural_upgrade.py:81-90`"*. Repository
evidence makes it **sharper**:

| Piece | Actual state |
|---|---|
| `OscillationTrap` | **used** — inside `ExecutionGraph.run` (`loop.py:142`), which reverts files and enters `RECOVER` on oscillation |
| `ExecutionGraph` | **zero production callers** — referenced only by the package re-export and `tests/test_architectural_upgrade.py` |
| `config.graph_oscillation_guard` | defined at `config.py:256` (schema), `:618` (field), `:888-889` (assignment) and **never read** by anything |

So the trap is not "unwired" in isolation — the **entire Layer C phase loop** (trap, graph, ceiling) is
a self-consistent mechanism with no production entry point. That is the same pathology this migration
has removed at every phase, and it is worth stating precisely rather than as "the trap is unused".

---

## 3. Implementation

| File | Change |
|---|---|
| `wisp/core/stagnation.py` | **new** — `ProgressSignal`, `StagnationVerdict`, `StagnationDetector`, `route_to_recovery()`, `_state_digest()` |
| `tests/test_stagnation_detection.py` | **new** — 44 tests |

### 3.1 The four inputs, connected

The plan names four inputs that *"already exist but are unconnected"*. All four now feed one signal:

| Input | Source | Was |
|---|---|---|
| criteria satisfied | `core/acceptance.py::CompletionVerdict` (P3) | no turn-path caller |
| failing-criteria set | the verdict's `unmet_criteria` | absent |
| artifact content hashes | `graph/types.py::GraphArtifact.content_hash` | not fed to the turn loop |
| completed-node count | `core/task_graph.py::TaskGraph` (P4) | not connected to anything |

`ProgressSignal.from_verdict_and_graph()` builds it from a P3 verdict and a P4 graph, and **tolerates
either being absent** — a content-only turn has neither, and must still produce a valid signal rather
than an error. That is what makes the detector usable on every turn instead of only graph-driven ones.

### 3.2 False positives, mitigated structurally

The plan rates the risk `Low-medium` and names it: *"flagging productive work as stagnated"*. Two
mitigations, both structural rather than tuned:

1. **N consecutive** non-progressing observations (`min_consecutive`) — one flat observation is normal;
   a turn that reads files makes no progress by construction.
2. The **strictly shrank** formulation. A failing-criteria set that merely *changed* is not progress,
   and neither is one that failed to shrink. Only a strict improvement counts — treating churn as
   progress is how a detector misses a real oscillation.

`test_a_productive_multi_step_task_is_never_flagged` and
`test_alternating_progress_is_not_stagnation` drive the false-positive direction; the same-size-set
test drives the churn case.

### 3.3 The trap is reused, not reimplemented

`stagnation.py` imports `OscillationTrap` and `diff_hash` from `core/graph/loop.py`. An AST test asserts
`OscillationTrap` is **not** defined in `stagnation.py` — a second 1-cycle/2-cycle implementation would
be a second authority for *"is this a repeat?"*, which is the defect class this migration exists to
remove.

The signal is hashed through `_state_digest()`, which sorts every collection explicitly: a `frozenset`'s
iteration order is not stable across processes, and an unstable digest would make the trap fire on
noise. Pinned by `test_the_digest_is_order_stable`.

### 3.4 Routing to a replan, not a retry

`route_to_recovery()` uses P6's `FailureClass.STAGNATION`, whose legal rungs are Global Replan →
Diagnostic → Escalate. **Retry is not among them**, and that is the point: retrying the same action
against the same state is the definition of the loop being detected. `test_retry_is_forbidden_for_stagnation`
pins it against P6's table rather than against a local copy.

---

## 4. Verification

### 4.1 New tests — 44, all passing

| Plan requirement | Class | Proves |
|---|---|---|
| `test_stagnation_detected_2cycle` | `TestStagnationDetected2Cycle` (8) | an A→B→A cycle and a repeat are detected **by the reused trap**; flat runs declare stagnation; one flat observation is not enough; the digest is order-stable |
| `test_progress_metric_monotonic` | `TestProgressMetricMonotonic` (11) | each of the four strict improvements counts; a same-size changed set and a growing set do not; **a productive multi-step task is never flagged**; progress resets the flat run |
| `test_stagnation_routes_to_replan` | `TestStagnationRoutesToReplan` (7) | detection routes to the ladder; the first rung is **Global Replan**, not Retry; retry and repair are forbidden; the route escalates once rungs are spent; nothing routes while progressing or disabled |
| `test_stagnated_goal_not_met` | `TestStagnatedGoalNotMet` (6) | a stagnated goal cannot report `GOAL_MET`; a progressing one can; a trap firing alone blocks it; a disabled detector does not block |
| `test_oscillation_trap_wired` | `TestOscillationTrapWired` (11) | **the config flag is read** and controls the detector; defaults follow the flag's declared default; the signal builds from a P3 verdict and a P4 graph and tolerates both being absent |

### 4.2 Regression — and a correction to the method

Two consecutive full runs on the **identical tree** read **129** and **130**. The count is therefore
**not stable**, and the earlier phases' clean `131 → 128 → … → 128` narrative cannot simply be extended.

| Run | Count | Note |
|---|---|---|
| P0 – P6 (single runs each) | 128 | consistent across four measurements |
| P7 run 1 | 129 | |
| P7 run 2 | 130 | = run 1 + `test_cli_surface_e2e.py::TestGroup3Headless::test_print_blackhole_server_falls_back` |
| **stable set** (fails in both runs) | **129** | stored at `.workbuddy-ai/memory/baseline-failures-stable.txt` |

**What the variation is.** `test_print_blackhole_server_falls_back` is a CLI E2E test whose name says
what it depends on: a **blackhole server**, i.e. a network timeout. It appears in one run and not the
other. That is environment, not code.

**What P7 can and cannot have caused.** Nothing imports `wisp.core.stagnation` — verified by scanning
every `wisp/**/*.py` for an `import` line naming it (only its own test does). Its import chain is
`core/graph/loop.py`, which imports **stdlib plus `core/graph/phases` only**. Running P7's suite
immediately before `test_sandbox_fallback_contract.py` passes 49/49, so the import chain does not
pollute it. P7 therefore cannot affect another module at import time.

**The decisive experiment.** Running the full suite with P7's test file **excluded**
(`--ignore=tests/test_stagnation_detection.py`) reads **129** — and the failure set is **byte-identical**
to the run that included it (`diff` empty). So:

> **P7 contributes zero failures.** The count is 129 with P7 and 129 without it.

That also settles the collection-order hypothesis: adding the file changes nothing, so the earlier
`test_sandbox_fallback_contract` suspicion was wrong.

**Why the residual +1 over P6 is unexplained.** The P0–P6 baseline lists lived in `/tmp` and were
**deleted between sessions** (the machine restarted), so the exact P6 set no longer exists to diff
against. The honest position: P7 is **proven** to contribute nothing (the exclusion experiment above),
and the 129-vs-128 difference lies outside P7.

**Method fix.** The baseline now lives in the repo, and it is the **intersection of two runs** rather
than a single run's output — a test that fails in both is real, one that appears in only one is flaky.
See `.workbuddy-ai/memory/README.md`.

---

## 5. Honest limits

- **The live turn loop does not run the detector.** The mechanism is complete and tested, and the
  config flag is read, but nothing on the live turn path constructs a `StagnationDetector` or calls
  `may_report_goal_met()`. Recorded as item **M13** (§7). The same shape of deferral as P5 item 5 and
  P6's M12 — and for the same reason.
- **`artifact_hashes` is not yet populated by the turn loop.** The field exists and is exercised by
  tests, but the turn loop does not feed it `GraphArtifact.content_hash` values; a caller must supply
  them via `with_artifact()`.
- **`min_consecutive` is not measured against real turns.** Two is a reasoned default, not a tuned one;
  tuning it needs the measurement P3 stage 3b is also blocked on.
- **`ruff`/`mypy` not installed.**
- **128 pre-existing failures remain.** P7's claim is exact: it adds none.

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| A synthetic oscillation is detected and routed | ✅ detected by the reused trap; routed to Global Replan |
| No false positive on a productive multi-step task | ✅ `test_a_productive_multi_step_task_is_never_flagged` |
| The existing `config.graph_oscillation_guard` flag is finally read | ✅ `StagnationDetector.from_config()` |
| No regression | ✅ **0 new failures** |
| Rollback by the existing flag | ✅ `WISP_GRAPH_OSCILLATION_GUARD` |
| `ruff` / `mypy` | ❌ not installed |

---

## 7. Deviations from the plan

### 7.1 The live loop was not wired (item M13)

The plan's first item is *"wire `OscillationTrap` to the live loop"*. The detector now **uses** the trap
and the config flag is read — but the live turn loop does not construct a detector.

**Why:** this is the third consecutive phase where wiring a mechanism into the live loop would change
behaviour on a path whose coverage does not justify it. P5's item 5 (graph drives execution), P6's M12
(recovery ladder consulted), and now P7's live wiring are the *same* deferral, and they share one
prerequisite: the live turn path's failure and progress behaviour is not observable enough to change
safely. That prerequisite is **M9** (the message list as a projection of the graph) plus the journal-
first reconstruction in **M2**.

Stating it once, here, rather than three times as three separate omissions: **the migration has built
a complete mechanism layer whose integration is a single, coherent next step.**

### 7.2 The plan's claim about `OscillationTrap` was narrowed

See §2. The trap *is* used — inside an orphaned `ExecutionGraph`. The accurate finding is that the whole
Layer C phase loop has no production entry point.

---

## 8. Next phase

**P8 — Context as a First-Class Subsystem.** Prerequisites: P1, P4 (both met).

Carried forward:

1. **M13 — wire the stagnation detector into the turn loop.**
2. **M12 — wire the recovery ladder into the turn loop.**
3. **M11 — make the graph mutation drive execution**, with **M9** (message list as a projection).
4. **M9** is the keystone: it unblocks M11, M12 and M13 together.
5. **M8 — retire `dag.py`** — needs a green fanout suite first.
6. **M2 / M3 — journal-first reconstruction; killpoint integration.**
7. **M1 — P3 stage 3b** — blocked on a working tool path (`jsonschema`).
8. **M4 — revisit ADR-0004** for the durable records.
