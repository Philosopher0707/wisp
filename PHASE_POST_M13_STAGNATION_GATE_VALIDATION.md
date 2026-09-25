# PHASE POST-M13 — STAGNATION GATE BEHAVIORAL VALIDATION & ENABLEMENT RECON

**Mode: VALIDATION-ONLY.** No production file changed, no shipped default changed. This phase produces
an evidence-backed enablement determination; it does not act on it.

| Field | Value |
|---|---|
| Phase | POST-M13 — stagnation gate behavioural validation |
| Authority | the ratified ADR-0035 / ADR-0036 / ADR-0037 chain |
| `HEAD` | `7c15626630f6dca8f37e27c638e59df0acb216e5` · branch `main` |
| Production changes | **0** |
| Default changes | **0** |
| New tests | **28** (`tests/reliability/test_post_m13_stagnation_gate_validation.py`) |
| Report | this file |

---

## 1. Mission Result

The complete ADR-0036 bounded completion gate was exercised under the now-ratified ADR-0037 monotonic-latch
semantics, across the full chain — observation → flat observation → `trap_fired` → predicate closed → gate →
intervention #1 → intervention #2 → bound exhausted → `done` → terminal turn.

**Every mandatory safety and contract criterion is PROVEN.** The gate intervenes at most twice per turn,
never on the last iteration, fails open on every unavailable predicate, cannot pre-empt the verification
floor, cannot change the goal state, and is per-turn local with no cross-turn contamination.

**The mechanism is validated. Enablement is not authorised** — see §15/§16: a ratified precondition on
enabling enforcement (ADR-0035 §9 → ADR-0016's measurement) does not exist, and ADR-0037 forbids the
default flip without a superseding ADR. Those are contract facts, not defects in the gate.

### ENTRY CRITERION

```
=== ENTRY CRITERION ===
ADR_0035_RATIFIED: YES
ADR_0036_RATIFIED: YES
ADR_0037_RATIFIED: YES
IMPLEMENTATION_PRESENT: YES
GATE_DEFAULT_OFF: YES
M13_LATCH_CONTRACT_ALIGNED: YES
OPEN_ARCHITECTURAL_CONTRADICTION: NO
CONTROLLED_ENABLEMENT_TEST_AVAILABLE: YES
ENTRY: PASS
========================
```

No later ADR supersedes the latch or completion-gate semantics: the decision log's highest entry is
ADR-0037, and its "Interaction with existing ADRs" table records ADR-0034/0035/0036 as standing.

---

## 2. Baseline

| Fact | Value | Source |
|---|---|---|
| `HEAD` | `7c15626630f6dca8f37e27c638e59df0acb216e5` | `git rev-parse HEAD` |
| Branch | `main` | `git rev-parse --abbrev-ref HEAD` |
| Tracked modifications | 41 (20 under `wisp/`) | `git status --porcelain` |
| Untracked | 57 before this phase, 58 after | the new test file is the only addition |
| Pre-existing WIP | the M13/POST-M13 work, uncommitted — **not** this phase's | `CONTEXT.md` §0 |

### Configuration baseline (declared defaults, `wisp/config.py::SETTINGS_SCHEMA`)

| Flag | Default | Line |
|---|---|---|
| `stagnation_gate` | **`false`** | `config.py:375` |
| `graph_oscillation_guard` | `true` | `config.py:946` |
| `goal_state` | `false` | `config.py:979` |
| `recovery_ladder` | `false` | `config.py:976` |
| `record_verdict` | `false` | `config.py:970` |
| `task_graph` | `false` | `config.py:973` |
| `max_iterations` | `50` (range 1–200) | `config.py:150`, `:791` |
| `turn_timeout` | `1800` s (range 10–7200) | `config.py:795` |
| `_MAX_STAGNATION_INTERVENTIONS` | `2` | `stateless.py:97` |

**`stagnation_gate = OFF` is established** and was never globally enabled. Every enabled-mode case in this
phase overrides the flag on a `tmp_path` config.

---

## 3. Contract Verification

Source read against the ratified contract. **No contradiction.** Each row is a contract claim and the line
that satisfies it.

| Contract claim | Source | Verdict |
|---|---|---|
| The gate is **bounded** at 2 interventions | `_MAX_STAGNATION_INTERVENTIONS = 2`, `stateless.py:97`; condition at `:833-834` | holds |
| The **last iteration cannot be withheld** | `iteration + 1 < max_iterations`, `stateless.py:840` | holds |
| The gate **fails open** | `except Exception: may_complete = True`, `stateless.py:843-848` | holds |
| The gate sits **after** the floor guard | floor block `stateless.py:795-818` ends in `continue`; gate at `:832` | holds |
| The engine receives **only a predicate** | closure at `runtime.py:769-772`; never the detector | holds |
| The predicate is the **arbiter's own input** | `not stagnation_detector.may_report_goal_met()`, `runtime.py:1174-1176` | holds |
| The record carries the **predicate**, not the verdict | `"stagnation_allows_goal_met": not _stagnating`, `runtime.py:1265` | holds |
| `trap_fired` is **monotonic** | `trap_verdicts` append-only, `stagnation.py:250`; `bool(trap_verdicts)`, `:288` | holds |
| The predicate closes on `trap_fired` **and** on the flat run | `stagnation.py:303-305` | holds |
| `min_consecutive` gates the **verdict only** | `observe` `:260`, `verdict` `:276`; **absent** from `may_report_goal_met` | holds |
| The detector is **per turn** | a local built inside `run_turn`, `runtime.py:716`, `:731` | holds |
| The arbiter order is ADR-0035's table | `PRECEDENCE`, `goal.py:70-78`; branches `:133-155` | holds |
| `turn_succeeded` is terminal evidence, unchanged | `saw_done and not saw_fatal_error` | holds |
| `GOAL_STAGNATED` outranks `GOAL_MET` | row 4 before row 6, `goal.py:146-151` | holds |

**One asymmetry found, reported not repaired** (§17, risk 3): the engine guards its call to
`may_report_goal_met()`; the runtime's post-turn read of the same predicate (`runtime.py:1174-1176`) is
unguarded. It is unreachable in production — the method is a pure, total function over dataclass fields —
and this is a validation-only phase. Pinned by a test so it is a known difference, not a surprise.

---

## 4. Gate Sequence

The normal chain, on the real runtime path, with the predicate permanently closed:

```
observation → flat observation → trap_fired → predicate False
    → gate withholds  → replan #1 → next iteration
    → predicate still False → replan #2 → next iteration
    → bound spent (2 < 2 is false) → `done` permitted → terminal turn
```

| Assertion | Test | Result |
|---|---|---|
| open predicate ⇒ zero interventions | `TestTheBudgetIsBounded::test_s1…` | holds |
| withhold twice, then surrender | `…::test_s2_s3_s4_the_gate_withholds_twice_then_surrenders` | holds |
| the replan is persisted in the transcript | `…::test_the_intervention_is_persisted_in_the_transcript` | holds |
| **no third intervention, ever** | `TestTheBoundInteractsWithTheLoopBound::test_no_third_intervention_is_ever_observed` (new) | holds |
| the turn always reaches `done` | the sweep below | holds |
| `turn_succeeded` stays terminal evidence | `…::test_a_turn_that_recovers_still_records_goal_stagnated` | holds |

**New: the bound against the *loop* bound.** `iteration + 1 < max_iterations` makes the observable count
`min(2, max_iterations − 1)`. No single-point test can show this; the sweep does:

| `max_iterations` | 1 | 2 | 3 | 4 | 30 | 200 |
|---|---|---|---|---|---|---|
| interventions (measured) | 0 | 1 | 2 | 2 | 2 | 2 |

---

## 5. Progress-After-Trap — `REOPENED = FALSE`

The mandatory case, and the phase's key invariant. **A replan may change the work, but within the current
detector lifetime it cannot erase the fact that stagnation was detected.**

```
observation A → observation A again → trap_fired
    → predicate False → replan → NEW work (is_progress_from == True)
    → consecutive_flat = 0 → trap_fired STILL True → predicate STILL False
```

| Assertion | Test | Result |
|---|---|---|
| `is_progress_from()` is True and `consecutive_flat` resets | `TestThePredicateIsALatch::test_progress_does_not_reopen_a_latched_predicate` | holds |
| the predicate does **not** reopen | same | holds |
| the verdict returns to `progressing` while the predicate stays closed | same | holds |
| on the **live path**, a recovering turn still records `GOAL_STAGNATED` | `…::test_a_turn_that_recovers_still_records_goal_stagnated` | holds |
| with the gate **on**, the same | `TestTheControlledEnablementExperiment::test_case_4_progress_after_the_trap` (new) | holds |

**This is expected behaviour, not a failure.** Under ADR-0037 the latch is monotonic. No test in this phase
asserts reopening; one that did would be stale by construction.

---

## 6. No-Progress After Replan

```
flat → intervention #1 → same state → intervention #2 → same state → surrender
```

`test_case_3_persistent_flat_state` (new) drives six consecutive flat observations — more than enough to
spend any bound — and observes **exactly two** interventions and a terminal `done`. No infinite loop, no
third nudge, no runtime reinvocation of `core.turn()`, and no recovery-ladder escalation merely because
stagnation persisted: the ladder is gated on `not turn_succeeded` (`runtime.py:1185`), and a stagnating
turn is `turn_succeeded = True`.

---

## 7. Boundary Conditions

| Case | Scenario | Result | Evidence |
|---|---|---|---|
| **A** | `max_iterations = 1` | **PROVEN** | sweep row 1 → 0 interventions; `test_a_single_iteration_turn_cannot_be_withheld` |
| **B** | `max_iterations = 2` | **PROVEN** | sweep row 2 → **exactly 1**; new |
| **C** | intervention budget exhausted | **PROVEN** | sweep rows 4–6 → 2; `test_the_bound_is_two` |
| **D** | predicate callable absent | **PROVEN** | `test_s8_no_gate_is_todays_behaviour` |
| **E** | predicate callable raises | **PROVEN** | `test_s9_a_raising_gate_permits_done`; `test_case_8_a_predicate_exception` |
| **F** | detector disabled | **PROVEN** | `test_graph_oscillation_guard_off_means_no_intervention` (new) |
| **G** | `stagnation_gate = false` | **PROVEN** | `test_s10_the_flag_off_preserves_the_old_path` |
| **H** | cancellation | **NOT APPLICABLE** | see below |
| **I** | terminal error | **PROVEN** | `test_a_fatal_error_with_a_closed_predicate_stays_goal_failed`; `test_case_7_a_terminal_error` |
| **J** | verification floor rejection | **PROVEN** | §8 |

**Case H is not applicable on the live path, with evidence.** `derive_goal_state` is called with
`cancelled=False` **unconditionally** (`runtime.py:1239`) — there is no live producer of operator
cancellation reaching the arbiter. The arbiter's row 1 is unit-tested directly
(`test_t9_cancelled_outranks_recovery_and_acceptance`), but the gate has no cancellation input to
preserve. This matches the project's long-standing note that cancellation has no live producer.

**Case F is the two-rollback-levels property.** `graph_oscillation_guard=false` disables the *detector*, so
the predicate is open by construction and an **enabled** gate still never intervenes; the goal record then
shows the predicate **open**, not "not evaluated".

---

## 8. Authority Tests

Required order: `VerificationFloorGuard` → stagnation predicate → `done`.

**The floor guard cannot be made to reject on a live turn in this environment** — `jsonschema` is absent
(F8), so every tool call is refused before dispatch and `wrote_code` never becomes `True`. The claim is
therefore proven three ways, and this phase adds the behavioural third:

1. **Structural.** The floor's rejection branch is at `stateless.py:795`, the gate at `:832`, and the
   rejection branch ends in `continue` — while it rejects, control cannot fall into the gate.
   (`test_s6_the_floor_guard_is_consulted_first`, `test_s7_the_floor_guard_block_ends_in_continue`.)
2. **Negative control.** On the same turn shape with the floor switched off, the gate *does* fire twice —
   so the gate's silence is the ordering, not an unreachable gate.
   (`test_the_stagnation_gate_is_reachable_when_the_floor_is_off`.)
3. **Behavioural, new.** The guard is subclassed to reject deterministically and the **combined** case is
   driven: floor rejects **and** predicate closed, same turn.
   * `test_the_floor_guard_owns_the_pass_it_rejects` — the first nudge is the floor's, never the gate's.
   * `test_the_two_budgets_are_independent` — with 0, 1 and 2 floor rejections, the stagnation
     interventions are **exactly 2** in every case. The floor's blocking neither consumes nor extends the
     stagnation budget.

**`VERIFICATION_AUTHORITY: PRESERVED`.** The floor guard is unmodified; no test weakened it.

---

## 9. Goal-State Tests

`goal_state` is enabled (`goal_state=True`) throughout the live tests. The four required scenarios:

| # | Inputs | Expected | Result |
|---|---|---|---|
| 1 | P3 `PASS` + terminal success + predicate **open** | `GOAL_MET` | holds — `test_t1_pass_progressing_success_is_goal_met` |
| 2 | P3 `PASS` + terminal success + predicate **closed** | `GOAL_STAGNATED` | holds — `test_t2_pass_and_stagnating_is_goal_stagnated`; `test_s14…` reaches `GOAL_MET` only with an open predicate |
| 3 | P3 `FAIL` + terminal success + predicate closed | `GOAL_FAILED` | holds — `test_t3_fail_beats_terminal_success`; `test_a_fatal_error_with_a_closed_predicate_stays_goal_failed` |
| 4 | P3 `INCONCLUSIVE` + terminal success + predicate closed | `GOAL_UNVERIFIED` | holds — `test_t4_inconclusive_is_goal_unverified` |

Stagnation **never** silently becomes `GOAL_MET`: `GOAL_MET` is reachable only through row 6
(`goal.py:150`), which requires `turn_succeeded` **and** an acceptance `PASS`. No precedence was altered.

---

## 10. F35 Replay Tests

The live predicate and the recorded predicate are the **same computation**, and replay reads the record.

| Required chain | Test | Result |
|---|---|---|
| live `may_report_goal_met()` == recorded `stagnation_allows_goal_met` | `test_the_predicate_field_is_the_arbiter_input_not_a_second_opinion` | holds |
| recorded is `False` in the trap-closed case | `test_s13_trap_fired_below_the_flat_threshold` | holds |
| replay derives the **same** goal state | same; `_replay()` reconstructs from the record alone | holds |
| replay survives a restart | `test_s13_replay_survives_a_restart` (new repository, detector gone) | holds |
| the trap-fired-below-threshold case preserves the predicate | `test_a_trap_closed_turn_records_the_predicate_and_no_event` (new) | holds |

```
LIVE:      stagnation_allows_goal_met = False
PERSISTED: stagnation_allows_goal_met = False
REPLAY:    GOAL_STAGNATED   ==   the live goal state
```

The recorded `stagnation_verdict` is `"progressing"` in the same record — the two facts are deliberately
different, and replay follows the predicate, which is why F35 existed and is now closed.

---

## 11. Audit-Gap Verification

ADR-0037 documents that a trap-closed turn records the predicate but **not** the observations. This phase
**measured** it rather than trusting the documentation.

| Turn shape | `stagnation_allows_goal_met` | `stagnation_verdict` | `STAGNATION` events |
|---|---|---|---|
| 2 identical reads (trap closes **below** the threshold) | `False` | `"progressing"` | **0** |
| 4 identical reads (verdict **reached**) | `False` | `"stagnating"` | **1** |

Mechanism: `stagnation_seen` is set only when `observe()` returns `STAGNATING`
(`runtime.py:864-866`); below the threshold `observe()` returns `UNKNOWN`, so no event is written
(`runtime.py:1126-1131`). **The gap is exactly as documented** — `AUDIT_GAP: UNCHANGED`.

The contrast row makes the gap precise: it is the *below-threshold* case only, and recording is
independent of `stagnation_gate` (both rows were measured with the flag off).

**Not silently fixed.** ADR-0037 accepted this limitation with a stated revisit condition; repairing it
would add persistence for explainability, which the replay contract does not require, and would exceed a
validation-only phase's authority.

---

## 12. Concurrency

| Assertion | Test | Result |
|---|---|---|
| a second turn gets a fresh budget | `test_s11_a_second_turn_gets_a_fresh_budget` | holds |
| two concurrent runtimes share no state | `test_s12_concurrent_turns_share_no_state` (2 budgets, 2 nudges each, no cross-transcript leakage) | holds |
| the counter is a **local**, never an attribute | `test_the_intervention_counter_is_a_local_not_shared_state` | holds |
| no `ContextVar` in the engine or the runtime | `test_no_contextvar_in_the_engine_or_the_runtime` (new) | holds |
| neither the detector nor the gate closure is stored on an object | `test_the_detector_and_the_gate_are_locals_never_attributes` (new) | holds |
| no module-level mutable stagnation state | `test_no_module_level_mutable_stagnation_state` (new) | holds |

`CONCURRENCY: PROVEN`. Cross-turn contamination: **0**.

---

## 13. Restart / Replay

Existing infrastructure was sufficient; **no new persistence was introduced**, and the intervention count
remains transient (ADR-0036 §7, re-ratified).

| Assertion | Test |
|---|---|
| the goal state is reconstructible from the journal | `test_d1_the_goal_state_is_reconstructible_from_the_journal` |
| replay reproduces the same goal state | `test_d6_replay_reproduces_the_same_goal_state` |
| replay reproduces the same recovery decision | `test_d7_replay_reproduces_the_same_recovery_decision` |
| a restart reconstructs the same authority result | `test_restart_reconstructs_the_same_authority_result` |
| the trap-closed state survives a restart | `test_s13_replay_survives_a_restart` |

`REPLAY: PROVEN`. Replay mismatches: **0**.

---

## 14. Controlled Enablement Experiment

`stagnation_gate=true` exercised **only** in isolated `tmp_path` configs. The repository default was never
touched and is asserted unchanged in §15/§19.

| # | Representative case | Observed |
|---|---|---|
| 1 | no stagnation | 0 interventions; not `goal_stagnated` |
| 2 | one flat observation | **2** interventions; `goal_stagnated` |
| 3 | persistent flat state (6 rounds) | **2** interventions; terminal `done` |
| 4 | progress after the trap | **2** interventions; predicate still `False`; `goal_stagnated` |
| 5 | final iteration (`max_iterations = 1`) | **0** interventions; `done` |
| 6 | verification rejection | floor owns the pass; **2** stagnation interventions; `done` |
| 7 | terminal error | **0** interventions; `goal_failed` |
| 8 | predicate exception | **0** interventions; `done` (fail open) |

`CONTROLLED_ENABLEMENT: PASS`. No deadlock, no timeout, no third intervention in any case.

---

## 15. Enablement Conditions

| # | Condition | Verdict | Basis |
|---|---|---|---|
| A | **Contract fidelity** — ADR-0035/0036/0037 preserved | **PROVEN** | §3 table, all rows hold; §9 goal states |
| B | **Boundedness** — at most 2 per turn | **PROVEN** | sweep; `max_iterations = 200` still 2 |
| C | **Termination** — every path reaches terminal behaviour | **PROVEN** | §4, §6, §14; 0 deadlocks |
| D | **Fail-open** — absence / exception / disabled detector cannot deadlock | **PROVEN** | cases D, E, F, G, 8 |
| E | **Authority** — verification remains authoritative | **PROVEN** | §8, three independent proofs |
| F | **Replay** — goal state deterministic | **PROVEN** | §10, §13; 0 mismatches |
| G | **Concurrency** — no cross-turn contamination | **PROVEN** | §12; 0 contamination |
| H | **No hidden semantic mutation** — enabling does not alter M13 detection | **PROVEN** | `test_s10…`: only the transcript differs; detector, predicate and goal state are identical |
| I | **Observability** — the audit gap is documented and accepted | **PROVEN** | §11, measured exactly as documented |

**All nine PROVEN.** No condition is `FAILED` or `NOT PROVEN`. Condition H is the one worth naming
explicitly: enabling the gate changes *whether the engine withholds `done`*, and nothing else — the
detector's observations, the predicate's value and the recorded goal state are identical with the flag on
and off.

---

## 16. Enablement Decision

**The mechanism is validated. Enablement is not authorised — and that is a contract fact, not a defect.**

Two ratified conditions stand between "the gate is safe" and "the gate may be turned on":

1. **ADR-0035 §9:** *"enforcement stays behind the per-concern flags until ADR-0016's measurement
   exists."* That measurement is the `INCONCLUSIVE` rate at P3 stage 3b, which **cannot be produced in this
   environment** — it requires a working tool path, and `jsonschema` is absent with no network to install
   it (F8). The precondition is unmet and unmet-able here.
2. **ADR-0037's implementation boundary** lists *"enabling `stagnation_gate` by default"* among the things
   **forbidden without a superseding ADR**.

So the default flip requires a **superseding ADR** — one that names the enablement decision, the
measurement that justifies it, and the rollback. It is *not* required by anything this phase found in the
gate's behaviour; the gate behaved correctly in every exercised case.

**`DECISION_STATE: ENABLEMENT_NOT_READY`** — because §18's behavioural conditions being PROVEN is
necessary but not sufficient: the enablement precondition is a contract, and the contract is not satisfied.

`DEFAULT_ENABLEMENT: NOT_CHANGED` — verified by two independent tests (§19).

---

## 17. Remaining Risks

| # | Risk | Assessment |
|---|---|---|
| 1 | **The gate's field behaviour is unmeasured outside this environment.** F8 means every tool call is refused pre-dispatch, so the stagnation signal is built from *refused* calls. The gate fires (2 interventions, repeatedly), so the mechanism is exercised — but a turn that actually mutates files was never driven. | Real, and the same gap ADR-0035 §9 defers to. Unmeasurable here. |
| 2 | **The verification-floor interaction is proven by subclass, not by a real rejection.** The combined case is behavioural, but with a stubbed guard. | Reduced to low: the ordering is also proven structurally, and the floor guard's own contract is pinned unchanged. |
| 3 | **Asymmetry: the engine guards its predicate call; the runtime's post-turn read does not** (`runtime.py:1174-1176`). | Low. `may_report_goal_met` is pure and total over dataclass fields, so it cannot raise in production. Reported, not repaired (validation-only). |
| 4 | **The audit gap is real for the below-threshold case.** A `GOAL_STAGNATED` closed by the trap alone cannot be *explained* from the journal, only reproduced. | Accepted by ADR-0037 with a revisit condition; measured and unchanged here. |
| 5 | **The intervention count is transient.** A crash mid-turn loses it. | By design (ADR-0036 §7): replay needs the predicate, not the count. |

---

## 18. Non-Goals

- **Not** enabling `stagnation_gate` by default, or changing any config default.
- **Not** modifying the latch, the trap, `is_progress_from()`, `consecutive_flat`, `min_consecutive` or
  `may_report_goal_met()`.
- **Not** adding reopening, a second detector, a second completion predicate, or any new persistence.
- **Not** altering goal precedence, recovery semantics, or `turn_succeeded`.
- **Not** repairing the audit gap or the guard asymmetry.
- **Not** changing F8, the graph or fanout, or any existing test.

---

## 19. Evidence

### Exact metrics

```
focused_tests_before:                154
focused_tests_after:                 182
new_tests:                            28
existing_tests_changed:                0
production_files_changed:              0
production_lines_changed:              0
config_defaults_changed:               0
interventions_observed:               25  (summed over the 20 gate-exercising turns; max 2 in any one turn)
maximum_interventions_observed:        2
third_intervention_observed:           0
deadlocks:                             0
timeouts:                              0
replay_mismatches:                     0
concurrency_cross_contamination:       0
```

### The new suite is non-vacuous

A validation suite that cannot fail proves nothing, so three hard exit criteria were deliberately broken
and the suite was run against each broken tree:

| Mutation | Suite result | Tree restored |
|---|---|---|
| `_MAX_STAGNATION_INTERVENTIONS = 3` | **1 failed** (the sweep) | SHA-256 identical |
| the `iteration + 1 < max_iterations` rule removed | **1 failed** | SHA-256 identical |
| `stagnation_gate` declared default flipped to `True` | **1 failed** | SHA-256 identical |

`prove_suite_is_not_vacuous.py`; the production files were restored byte-identically and re-verified:

```
a50deabe…95cc  wisp/core/stateless.py   (== its pre-mutation snapshot)
4fe6da2a…ef2c  wisp/config.py           (== its pre-mutation snapshot)
```

### Regression: the chunk's failure set is unchanged

`tests/reliability/` cannot run in one process on this host — the kernel kills it (exit 137, F36) — so it
was chunked per file and unioned:

| File | Result |
|---|---|
| `test_13h2_determinism.py` | 6 failed, 33 passed — **all 6 in the stable baseline** |
| `test_13j1_fanout_contract_repair.py` | 13 failed, 35 passed — **all 13 in the stable baseline** |
| `test_13j_fanout_contract.py` | 5 failed, 5 passed — **all 5 in the stable baseline** |
| `test_post_m13_stagnation_gate_validation.py` (**new**) | **28 passed, 0 failed** |
| the other 12 files | 0 failed |

24 pre-existing failures, **all present in `.workbuddy-ai/memory/baseline-failures-stable.txt`** (129
entries); the new file appears **0** times in the baseline. **Both `comm` directions empty** — 0 new,
0 fixed.

**Order-independence** (the one way a new test file can break others):

```
new file first, in one process:  61 passed
new file last,  in one process:  61 passed
```

### Files written by this phase

| Path | Kind |
|---|---|
| `tests/reliability/test_post_m13_stagnation_gate_validation.py` | **new** — 28 tests |
| `PHASE_POST_M13_STAGNATION_GATE_VALIDATION.md` | **new** — this report |
| `WISP_MIGRATION_STATUS.md` | one change-log row |
| `.workbuddy-ai/memory/2026-09-24.md` | daily log |
| `.workbuddy-ai/memory/post-m13-gate-validation/` | evidence artefacts (per-file results, snapshots, the non-vacuity script) |

No production file, no config default, and no existing test was modified.

---

## 20. Status Log

| Checkpoint | Result |
|---|---|
| `STATUS START` | validation-only; production changes 0; defaults 0; constraints recorded |
| `STATUS BASELINE` | `HEAD 7c15626`, `main`, 41 M / 57 untracked, all flags at their declared defaults, `stagnation_gate` OFF |
| `STATUS CONTRACT` | ADR-0035/0036/0037 source alignment verified — 14 rows, all hold; one asymmetry reported |
| `STATUS GATE` | gate at `stateless.py:832-861`, three conditions, after the floor guard, predicate-only seam |
| `STATUS SEQUENCE` | two interventions then surrender; the loop-bound sweep `0/1/2/2/2/2` |
| `STATUS FAILURE_PATH` | fail-open PROVEN (absent / raising / disabled / flag off); last iteration safe; cancellation NOT APPLICABLE |
| `STATUS REPLAY` | F35 live == persisted == replay; restart reproduces `GOAL_STAGNATED`; 0 mismatches |
| `STATUS CONCURRENCY` | per-turn local; no `ContextVar`; no stored detector or closure; 0 contamination |
| `STATUS ENABLEMENT` | isolated `stagnation_gate=true` across 8 cases — PASS |
| `STATUS ADR_CHECK` | the default flip requires a superseding ADR (ADR-0037) and ADR-0016's measurement (ADR-0035 §9) |
| `STATUS COMPLETE` | report written; evidence reconciled |

**No `STATUS CONTRADICTION` was emitted.** The source did not contradict the recon, and no test encoded
pre-ADR-0037 reopening semantics — a search for one found none.

---

## 21. Final Status

```
=== STATUS: COMPLETE ===
PHASE: POST-M13-STAGNATION-GATE-VALIDATION
MODE: VALIDATION-ONLY
PRODUCTION_CHANGES: 0
DEFAULT_CONFIG_CHANGED: 0
DECISION_STATE: ENABLEMENT_NOT_READY
ADR_CONTRACT: PRESERVED
M13_LATCH: MONOTONIC
REOPENING: FORBIDDEN
GATE_BOUND: 2
THIRD_INTERVENTION: NOT_OBSERVED
TERMINATION: PROVEN
FAIL_OPEN: PROVEN
VERIFICATION_AUTHORITY: PRESERVED
REPLAY: PROVEN
CONCURRENCY: PROVEN
AUDIT_GAP: UNCHANGED
CONTROLLED_ENABLEMENT: PASS
DEFAULT_ENABLEMENT: NOT_CHANGED
REPORT: PHASE_POST_M13_STAGNATION_GATE_VALIDATION.md
NEXT: no code or configuration change is authorised. Turning the gate on by default is a SEPARATE
      decision that requires (a) a superseding ADR — ADR-0037 forbids the flip without one — and
      (b) the measurement ADR-0035 §9 defers to, which F8 blocks in this environment. The gate
      itself needs nothing further: all nine §18 conditions are PROVEN.
=============================
```

### EXIT CRITERION

```
=== EXIT CRITERION ===
CONTRACT_PRESERVED: YES
INTERVENTION_BOUND_2: YES
TERMINATION_PROVEN: YES
FINAL_ITERATION_SAFE: YES
FAIL_OPEN_PROVEN: YES
VERIFICATION_AUTHORITY_PRESERVED: YES
M13_LATCH_PRESERVED: YES
GOAL_STATE_CORRECT: YES
REPLAY_PROVEN: YES
CONCURRENCY_PROVEN: YES
CONTROLLED_ENABLEMENT: PASS
DEFAULT_ENABLEMENT_CHANGED: NO
REPORT_COMPLETE: YES

EXIT:
COMPLETE
========================
```
