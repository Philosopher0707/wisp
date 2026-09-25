# PHASE — PROGRESS-AWARE RECOVERY

**Mission:** introduce objective-relevant, durable progress semantics so that a timeout
*with* meaningful coding progress can be told apart from a timeout *without* it, route the
former into a continuation strategy, and demonstrate the trajectory
`failure → progress → continuation → independent verification → GOAL_MET` on a real objective
with a real provider.

**Status:** implemented, adversarially tested, exercised live against two real providers.
**Decisions:** ADR-0046. **Findings:** F59 (repaired), F60 (open, recorded), F61 (open,
recorded). **New module:** `wisp/core/progress.py`. **New tests:** 39. **Full regression:**
771 passed, 0 failed.

---

## 1. Executive summary

The previous mission's live experiment ended on a rung that could not work:

```text
CODE_TURN_TIMEOUT  →  FailureClass.ENVIRONMENT  →  LEGAL_RUNGS {DIAGNOSTIC, HUMAN}
                                                          │
                                                "Do not edit any file"
```

That classification is **correct** — the host stopped the turn, and `ENVIRONMENT` exists
precisely for *"the model is too slow or unreachable — not retrying"*. It is also useless for
a turn that was cut off **mid-implementation**, whose work is real and whose only sensible
recovery is to continue it. Both are the same failure, so no re-classification can separate
them: the missing fact is not *what failed* but *whether the attempt moved the objective*.

This mission adds that fact as a **second, orthogonal input to the existing recovery ladder** —
not a second ladder, not a new failure class, not a new authority.

**What was built.** `core/progress.py` compares two `CommandProbe` measurements and reports
which of the objective's own numbers moved. `RecoveryLadder.decide`/`legal_rungs` gained a
keyword-only `progress` parameter (default `None`), and a **meaningful-progress** observation
*widens* a class's legal rung set with the continuation rungs. `PROGRESS_CONTINUATION_RUNGS`
is total and empty for nine of ten classes; `ENVIRONMENT` gains exactly `REPAIR`.
`directive_for(rung, progress=…)` selects an action-first continuation wording, and the next
attempt is shown **what improved** as well as what is still unmet.

**What was demonstrated live** (12 real runs, two providers, every number from the durable
journal):

| | |
|---|---|
| real timeout failure with measurable progress | **11 runs** — `E1101`, `turn_succeeded=False`, `failure_class=environment`, progress `meaningful_progress` with authoritative signals |
| the continuation rung selected | **9 runs** — `INITIAL → REPAIR`, a rung `ENVIRONMENT` withholds without progress |
| no-progress timeout stays conservative | **2 runs** — `NO_PROGRESS → DIAGNOSTIC → escalated_to_human` |
| the continuation writing genuinely remaining work | **2 runs** — 1 file (40→44 passing) and **14 files** (1→44 passing) |
| **`goal_met` after a real timeout failure** | **5 runs** — e.g. `positive12`: timeout at 36 s / 19 calls / 15 files → `REPAIR` → independent verification `exit 0, 60 passed, inputs unchanged` → `GOAL_MET` |
| full regression | **771 passed, 0 failed** |

**The boundary, stated up front and repeated in §14.** In the five runs that reached
`GOAL_MET`, attempt 0 had already satisfied the *objective*; the timeout cut off the *turn*
before it could close. So the recovery's contribution there was to finish the turn, not to
write more code. The variant where the continuation writes genuinely remaining work **is**
demonstrated (`positive10`: attempt 1 changed 14 of the 17 outstanding files), but those runs
did not converge — because the ladder permits **exactly one** continuation (`R5`), and
`positive10` needed two. That limit is measured, named as **F61**, and left un-changed because
`R5` is a preserved contract.

**Nothing was manufactured.** No harness mutation, no weakened acceptance, no model-declared
criterion, no skipped regression, no provider outage counted as recovery.

---

## 2. Baseline architecture

| Component | State before this mission | Role now |
|---|---|---|
| `core/recovery.py::FailureClass` | closed taxonomy of ten classes; `CODE_TURN_TIMEOUT → ENVIRONMENT` | unchanged — still answers *what failed* |
| `core/recovery.py::LEGAL_RUNGS` / `FORBIDDEN_RUNGS` | total per class; `ENVIRONMENT = {DIAGNOSTIC, HUMAN}` | unchanged; a third table now *widens* on progress |
| `core/recovery.py::RecoveryLadder` | 7 rungs, budgets, `R5` no-repeat, escalation as a state | gained a keyword-only `progress` parameter |
| `core/convergence.py::ConvergenceController` | objective-level loop; measures, classifies, re-attempts | now also evaluates progress per attempt and journals the baseline |
| `core/convergence.py::CommandProbe` | runs specs, records `exit`/`collected`/`failed`/`inputs_digest` | unchanged — it is the measurement progress is computed from |
| `core/acceptance.py::evaluate` | the verdict authority | unchanged |
| `core/goal.py::derive_goal_state` | ADR-0035 precedence | unchanged |
| `core/stateless.py` turn loop | emits `E1101` on timeout, `E1102` on iteration budget | unchanged |

The turn budget is `WISP_TURN_TIMEOUT` (default 1800 s, min 10). The iteration budget is
`max_iterations` (default 50). A timeout emits a **fatal** `error_event` followed by `done`, so
`saw_fatal_error` wins and `turn_succeeded=False` — which is why an attempt that times out can
never be `passed` at the objective level.

---

## 3. Progress semantics definition

`ProgressVerdict` is total: `NO_PROGRESS` / `MEANINGFUL_PROGRESS` / `PROGRESS_UNDETERMINABLE`.
`evaluate_progress(before, after, *, changed_files=())` compares two measurements' payloads —
keyed by criteria id, which is what makes the comparison **objective-relative**: the criteria
ids *are* the objective's terms.

Signals are classified, and the classification is the whole discipline:

| Kind | Signals | Sufficient alone? |
|---|---|---|
| `AUTHORITATIVE` | failing checks fell; passing checks rose (failures not rising); a non-zero exit became 0 with something collected; a symbol went missing → defined | **yes** |
| `SUPPORTING` | files changed by the attempt; `exit 0` with 0 collected (vacuous) | no |
| `REGRESSION` | failing checks rose; passing checks fell; a symbol disappeared | forces `NO_PROGRESS` |
| `UNSAFE` | a declared verification input's content digest moved | forces `PROGRESS_UNDETERMINABLE` |

Decision order, and why each step exists:

1. **No `before`** → `PROGRESS_UNDETERMINABLE`. Nothing to compare against; inventing a verdict
   would be a claim.
2. **A moved `inputs_digest`** → the criterion is skipped and the verdict is
   `PROGRESS_UNDETERMINABLE`. The measurement is not evidence of anything, so it can neither
   help nor hurt.
3. **A regression** → `NO_PROGRESS`. Fixing one thing while breaking another is not progress,
   and there is no "negative progress" state to invent.
4. **An authoritative improvement** → `MEANINGFUL_PROGRESS`.
5. Otherwise → `NO_PROGRESS`. Supporting signals are recorded for review; they are not
   sufficient, by design.

**What is deliberately not a signal.** §3 of the mission forbids `files_changed > 0`,
`tool_calls > 0` and "the model says it made progress". All three are activity, and a
formatting churn satisfies the first two — so file mutations are `SUPPORTING` and can never
produce `MEANINGFUL_PROGRESS`. Nothing in `progress.py` reads model text at all.

**Why the pass count is authoritative.** `environment._detect_verification_commands` hardcodes
`python -m pytest tests/ -x -q`, and `-x` stops at the first failure — so on a red suite the
*failure* count is pinned at 1 and the *pass* count is the only thing that moves. Reading only
failures would report "no progress" for a project that had just implemented half its
functions. It is not gameable: the only way to inflate the pass count is to add or edit tests,
and `tests/` is a declared input whose digest disqualifies the whole criterion.

---

## 4. Authority analysis

The mission's §2 lists the contracts to preserve. Each was checked, not assumed:

| Question | Authority | Touched? |
|---|---|---|
| what failed? | `recovery.classify_failure*` / `FailureClass` | **no** — `E1101` is still `ENVIRONMENT` |
| is it done? | `acceptance.evaluate` | **no** |
| what is the goal state? | `goal.derive_goal_state` (ADR-0035) | **no** |
| what may be tried next? | `RecoveryLadder.decide` + the legality tables | extended by one optional input |
| did the objective move? | **`core/progress.py` — new, and the only new question** | new |
| who measured it? | `CommandProbe` (producer `convergence.command_probe`) | **no** |

- **Progress is not a second verification authority.** It introduces no correctness question:
  it compares two measurements an existing authority already took. `GOAL_MET` still needs
  `acceptance.evaluate` to return `PASS` *and* the turn to have succeeded.
- **The model is not an authority for progress.** No model text is read.
- **The rung set stays one authority.** `is_legal_rung` still checks `FORBIDDEN_RUNGS` first, so
  a widening can never reintroduce a forbidden rung: `SECURITY` still cannot retry, and
  `REPEATED`/`STAGNATION` still cannot repeat.
- **`progress=None` is provably a no-op** for every class (pinned by
  `test_progress_none_is_identical_to_the_legacy_behaviour`).

---

## 5. Implementation changes

| File | Change |
|---|---|
| `wisp/core/progress.py` | **new**, 319 lines — `ProgressVerdict`, `SignalKind`, `ProgressReport`, `evaluate_progress`, `ProgressLedger` |
| `wisp/core/recovery.py` | +100/−8 — `PROGRESS_CONTINUATION_RUNGS` (total), `_continuation_rungs`, `is_legal_rung(..., progress=)`, `legal_rungs(..., progress=)`, `decide(..., progress=)` |
| `wisp/core/convergence.py` | `Measurement.to_dict/from_dict`; `AttemptRecord` gains `progress`, `progress_signals`, `measurement_observations` and a `measurement` accessor; `_CONTINUATION_DIRECTIVES`; `directive_for(..., progress=)`; controller gains `baseline=`, `_last_progress`, `_last_progress_signals`; baseline journaling (`kind: "baseline"`); `read_journal_baseline` |
| `wisp/autonomous.py` | passes the baseline to the controller; on resume prefers the **journaled** baseline so the criteria are not re-derived from an already-mutated workspace |
| `scripts/next_progress_experiment.py` | **new**, 299 lines — the live driver (`--scenario`, `--modules`, `--per-module`) |
| `tests/reliability/test_progress_aware_recovery.py` | **new**, 855 lines, 39 tests |

**No behaviour changes to any existing caller.** `progress` is keyword-only and defaults to
`None`; the continuation directive only replaces the generic one when the previous attempt
measurably advanced the objective.

**Durability.** The baseline is journaled as the first record so a resume cannot re-measure a
workspace the interrupted run already mutated — which would change both the derived criteria
and every subsequent progress verdict. `AttemptRecord` journals the raw payloads, because
`evaluate_progress` compares payloads and a resumed run that kept only the rendered lines could
not reproduce the verdict. A legacy journal with no baseline record still resumes.

---

## 6. Test matrix

`tests/reliability/test_progress_aware_recovery.py` — **39 tests**, all passing.

| Case | Test | Result |
|---|---|---|
| P1 real progress after a timeout selects continuation | `test_p1_a_timeout_with_measurable_progress_selects_continuation` | pass |
| P2 no progress stays conservative | `test_p2_a_timeout_with_no_progress_stays_conservative` | pass |
| P3 churn (incl. identical rewrite) is not progress | `test_p3_file_churn_is_not_meaningful_progress`, `test_changed_files_alone_can_never_be_meaningful_progress` | pass |
| P4 tampering is not progress and not convergence | `test_p4_editing_the_declared_input_is_not_progress_and_not_success`, `test_a_moved_verification_input_disqualifies_the_measurement` | pass |
| P5 vacuous green is not progress | `test_p5_a_green_with_zero_collected_is_not_progress`, `test_a_vacuous_green_is_not_progress` | pass |
| P6 regression is not progress | `test_p6_a_regression_does_not_open_the_continuation_door`, `test_a_falling_pass_count_is_a_regression` | pass |
| P7 partial improvement is meaningful | `test_p7_a_five_of_ten_improvement_is_meaningful` | pass |
| P8 progress never overrides acceptance | `test_p8_progress_cannot_override_acceptance`, `test_p8b_a_clean_finish_is_goal_met` | pass |
| P9 provider-only timeout is not progress | `test_p9_a_provider_only_timeout_is_an_environment_failure` | pass |
| P10 resume reproduces verdict and rung | `test_p10_resume_reproduces_progress_and_rung`, `test_p10b_…not_vacuous`, `test_p10c_a_torn_journal_still_resumes` | pass |
| P11 repeated progress still terminates | `test_p11_progress_does_not_make_recovery_unbounded` | pass |
| P12 continuation ≠ failed strategy | `test_p12_the_continuation_is_a_materially_different_strategy` | pass |
| table totality / no widening where forbidden | `test_the_continuation_table_is_total`, `test_progress_can_never_widen_a_class_that_forbids[security/repeated/stagnation]`, `test_forbidden_wins_over_a_progress_widening_for_every_class`, `test_only_meaningful_progress_widens` | pass |
| the evidence the continuation is shown | `test_the_continuation_is_shown_what_improved_not_only_what_is_wrong`, `test_a_non_progressing_failure_shows_only_the_measurement` | pass |
| non-vacuity | `test_the_p1_assertions_are_not_vacuous` | pass |

**Full regression** (`tests/reliability/` + the recovery, classification, escalation and
benchmark suites): **771 passed, 0 failed**, 641.75 s. No pre-existing test was weakened.

---

## 7. Adversarial / falsification results

Every falsification was attempted as a **mutation probe** — break the mechanism, watch the
suite fail, restore it.

| Probe | Mutation | Result |
|---|---|---|
| **the widening is load-bearing** | empty `PROGRESS_CONTINUATION_RUNGS[ENVIRONMENT]` | **6 tests fail**, including P1, P7, P10, P10b, P12 → non-vacuous |
| **the tamper disqualification is load-bearing** | disable the `inputs_digest` check in `evaluate_progress` | **2 tests fail** — and the mutation report is the important part: with the check disabled, the live tampering scenario is recorded as **`meaningful_progress`**. The guard is not decorative; it is the only thing between "edits the tests" and "made progress". |
| progress manufactured by file churn | P3 | rejected — `SUPPORTING`, never sufficient |
| progress manufactured by modifying tests | P4, unit tamper test | rejected — `PROGRESS_UNDETERMINABLE`; the integrity criterion also fails |
| progress manufactured by adding `conftest.py` | (previous mission's repair; `conftest.py` is a declared input) | rejected — digest moves |
| progress manufactured by a zero-test green | P5 | rejected — vacuous, `SUPPORTING` only |
| progress manufactured by unrelated repository changes | P3 (`README.md` written) | rejected — `SUPPORTING` only |
| progress survives a restart incorrectly / disappears | P10, P10c | neither — verdict and rung reproduced exactly; a torn line is tolerated |
| progress causes repeated recovery | P11 | no — one continuation, then `DIAGNOSTIC`, then escalation |
| progress bypasses acceptance | P8 | no — verdict still `FAIL`, state still not `GOAL_MET` |
| progress overrides `GoalState` | P8b, P12 | no |
| progress turns provider failure into agent recovery | P9 + live `negative-or` | no |
| recovery repeats the failed strategy | P12, live runs | no — `INITIAL → REPAIR`, different rung/directive/session |
| recovery becomes unbounded | P11, live | no |
| model output becomes the progress authority | `progress.py` reads no model text; the module is pure over payloads | structurally impossible |
| a resumed run chooses a different decision | P10 | no |

**One falsification succeeded, and it was the point of the mission.** F2 — "recovery succeeds
without actually fixing the repository" — had been closed for *convergence* by ADR-0045 R12 but
was **re-opened for progress** by the first implementation of this module. The mutation probe
above is that demonstration. It is now closed by R5 of ADR-0046.

---

## 8. Live experiment design

Path: `wisp.autonomous.converge_on_objective` → `CompositionRoot` → `AgentRuntime` →
`WispAgentCore` → a wired `ToolExecutor` (`permission_mode=full`). Providers: the local Ollama
daemon (`nemotron-3-ultra:cloud`) and OpenRouter (`stealth/space-bunny-alpha`). The harness
**never mutates the workspace**; it only measures.

The task: N modules, each with unimplemented functions raising `NotImplementedError`, and a red
`pytest` suite. The objective asks for the suite to pass, so `_WANTS_FIX_RE` promotes the
absolute criterion to required — the run must genuinely reach `exit 0`. The derived criteria
are:

```
required  verify:cmd0                  `python -m pytest tests/ -x -q` exits 0
required  verify:cmd0:no_regression    no more failures than the baseline
required  verify:cmd0:inputs_unchanged the tests are the tests that failed
```

Task size and `WISP_TURN_TIMEOUT` are the two dials; §9 records every configuration tried, not
only the ones that landed.

---

## 9. Complete attempt trajectory

### 9.1 The headline run — `positive12`

Task: 15 modules × 4 functions = 60 functions. Budget 34 s. Provider OpenRouter. Wall 51.9 s.

| | attempt 0 | attempt 1 |
|---|---|---|
| rung | `INITIAL` | **`REPAIR`** (continuation) |
| session | `converge-199269aca0` | `converge-d3cb1e99f1` (fresh) |
| turn outcome | `turn_succeeded=False`, `failure_code=E1101` | `turn_succeeded=True`, no code |
| failure class | **`environment`** | — (nothing failed) |
| progress | **`meaningful_progress`** | `no_progress` |
| progress signals | `failing checks 1→0` · `passing checks 0→60` · `exit 1→0 (60 passed)` · `15 file(s) changed` | — |
| tool calls / duration | 19 / 36.1 s | 18 / 15.4 s |
| files changed | 15 | 0 |
| measurement | `exit 0, collected 60, failed 0` | `exit 0, collected 60, failed 0` |
| verdict | `pass` | `pass` |
| goal state | `goal_failed` | **`goal_met`** |

The directive attempt 1 received, and the evidence it was shown:

```text
The previous attempt measurably advanced this objective — the harness's own measurement of
what improved is below — but the turn ended before the objective was met. That work is already
in the repository and it is correct as far as it goes. Finish the job: implement what is still
missing, and nothing else. Read only what you need to see the remaining shape of the work,
then write the code. Do NOT restart the task, do NOT redo work that is already done, and do
NOT edit the tests — they are the measure, not the objective.

  - verify:cmd0: failing checks 1→0
  - verify:cmd0: passing checks 0→60
  - verify:cmd0: exit 1→0 (60 passed)
  - 15 file(s) changed by the attempt (alpha.py, beta.py, delta.py, epsilon.py…)
  - verify:cmd0: exit 0, collected 60
```

Independent verification: `verify:cmd0` = `exit 0, collected 60, failed 0`, plus
`verify:cmd0:inputs_unchanged` — a **content digest** of `tests/`, `pyproject.toml` and
`conftest.py` identical to the baseline. The agent did not touch the measure.

### 9.2 The run where the continuation wrote the remaining work — `positive10`

Task 18×4. Budget 36 s. **This is the closest live approximation of the mission's §12 shape, and
it is where the boundary is.**

| | attempt 0 | attempt 1 | attempt 2 |
|---|---|---|---|
| rung | `INITIAL` | **`REPAIR`** | `DIAGNOSTIC` |
| outcome | `E1101` | `E1101` | `E1101` |
| progress | `meaningful_progress` | **`meaningful_progress`** | `no_progress` |
| signals | `passing checks 0→1` · 1 file | `passing checks 1→44` · **14 files** | — |
| tool calls / duration | 26 / 38.5 s | 26 / 36.6 s | 19 / 36.9 s |
| files changed | 1 | **14** | 0 |

The continuation did **more work than the first attempt** (14 files against 1) and moved the
objective from 1 to 44 passing checks. It then needed a *third* attempt — and the ladder had no
continuation left, because `R5` forbids repeating a rung. It fell to `DIAGNOSTIC`, whose
directive is *"Do not edit any file in this attempt"*, so the run was guaranteed to fail.
`positive7` is the same shape at a different budget (attempt 0: 12 files, 40 passing; attempt 1:
1 file, 44 passing).

**This is finding F61**, and it is the precise reason the mission's §12 shape is not reached in
a single run. See §14.

### 9.3 Every run

| run | provider | task | budget | attempt 0 | progress | rung 1 | attempt 1 | outcome |
|---|---|---|---|---|---|---|---|---|
| `positive1` | nemotron | 3×2 | 240 s | **succeeded** | — | — | — | `goal_met` |
| `positive2` | nemotron | 8×2 | 120 s | timeout, 12 calls, 8 files, 16 passed | `meaningful` | `REPAIR` | timeout, 4 calls, 0 files | `goal_failed` |
| `positive3` | nemotron | 12×4 | 200 s | **succeeded**, 18 calls | — | — | — | `goal_met` |
| `positive4` | nemotron | 12×4 | 100 s | timeout, 8 calls, 4 files, 16 passed | `meaningful` | `REPAIR` | timeout, 3 calls, 0 files | `goal_failed` |
| `positive-or` | openrouter | 12×4 | 60 s | **succeeded**, 30 calls | — | — | — | `goal_met` |
| **`positive5`** | openrouter | 18×4 | 35 s | timeout, 22 calls, 18 files, 72 passed | `meaningful` | `REPAIR` | **succeeded**, 21 calls | **`goal_met`** |
| `positive6` | openrouter | 18×4 | 25 s | timeout, 8 calls, **0 files** | **`no_progress`** | **`DIAGNOSTIC`** | timeout, 18 calls | `escalated_to_human` |
| `positive7` | openrouter | 18×4 | 32 s | timeout, 25 calls, 12 files, 40 passed | `meaningful` | `REPAIR` | timeout, 14 calls, **1 file**, 44 | `goal_failed` |
| **`positive8`** | openrouter | 6×12 | 20 s | timeout, 10 calls, 6 files, 72 passed | `meaningful` | `REPAIR` | **succeeded**, 11 calls | **`goal_met`** |
| **`positive9`** | openrouter | 18×4 | 45 s | timeout, 30 calls, 18 files, 72 passed | `meaningful` | `REPAIR` | **succeeded**, 21 calls | **`goal_met`** |
| `positive10` | openrouter | 18×4 | 36 s | timeout, 26 calls, 1 file, 1 passed | `meaningful` | `REPAIR` | timeout, 26 calls, **14 files**, 44 | `goal_failed` |
| **`positive11`** | openrouter | 18×4 | 41 s | timeout, 23 calls, 18 files, 72 passed | `meaningful` | `REPAIR` | **succeeded**, 43 calls | **`goal_met`** |
| **`positive12`** | openrouter | 15×4 | 34 s | timeout, 19 calls, 15 files, 60 passed | `meaningful` | `REPAIR` | **succeeded**, 18 calls | **`goal_met`** |
| `negative-or` | openrouter | negative | 25 s | timeout, 14 calls, **0 files** | **`no_progress`** | **`DIAGNOSTIC`** | timeout, 15 calls | `escalated_to_human` |

Read honestly: **five runs** produced the mission's trajectory to `GOAL_MET`; **two runs**
show the continuation writing the remaining work; **two runs** show the conservative path. The
five `goal_met` runs are also the honest counter-example to the strong claim — in all five,
attempt 0 had already reached `exit 0`, so the continuation closed the turn rather than
finishing undone work. The two runs that did leave work over did not converge.

### 9.4 The negative case

`negative-or`: a suite whose assertion is about a path outside the workspace
(`/var/lib/wisp-deploy/marker`), so no edit inside the repository can satisfy it. Objective:
"fix the failing test suite so that `python -m pytest tests/ -q` passes".

```
attempt 0  INITIAL     E1101 timeout, 14 calls, changed []  progress=no_progress  → DIAGNOSTIC
attempt 1  DIAGNOSTIC  E1101 timeout, 15 calls, changed []  progress=no_progress  → no rung left
           escalation  intervention_id=esc-2, failure_class=environment,
                       ladder_history=[DIAGNOSTIC (rung 6), HUMAN]
final      escalated_to_human, wall 52.4 s
```

This is the mission's §13 exactly: a timeout with no meaningful progress goes to the
**conservative** recovery, is bounded, and terminates honestly. It also re-validates §15 — the
agent did not tamper, and had it done so the integrity criterion and the progress
disqualification would both have fired. `positive6` is a second, independent instance.

---

## 10. Resume / replay evidence

- `test_p10_resume_reproduces_progress_and_rung` runs the same scenario twice: uninterrupted,
  and interrupted after attempt 0 then resumed in a **new controller** told nothing but the
  journal path. The progress verdict, the rung (`REPAIR`), the directive and the final
  `GOAL_MET` are identical; the completed attempt is not re-run (exactly one new turn).
- The journaled **baseline** is preferred over a fresh measurement, and
  `read_journal_baseline` returns the original (`failed == 3`) rather than the mutated state —
  so the criteria are the same criteria attempt 0 was judged by.
- `test_p10c_a_torn_journal_still_resumes` proves the new `kind` discriminator did not break
  torn-line tolerance: a truncated final line is discarded and the run continues at the next
  index.
- `test_p10b_the_resume_equivalence_is_not_vacuous` proves the equivalence is not trivial: with
  the verdict removed, the rung changes from `REPAIR` to `DIAGNOSTIC`.

---

## 11. Acceptance-integrity evidence

The mission treats F2's repair as a permanent invariant, and asks specifically that progress
awareness does not reopen it. It does not, and the reason is structural: **a measurement whose
declared inputs moved supplies no signals at all**, and its verdict is
`PROGRESS_UNDETERMINABLE`.

- Live: in `negative-or` the agent did not touch the tests; had it, the digest would have moved
  and `verify:cmd0:inputs_unchanged` would have failed the verdict — which is what the previous
  mission demonstrated end to end.
- Mutation probe: with the disqualification disabled, the tampering scenario is reported as
  **`meaningful_progress`** — so the guard is load-bearing, not decorative.
- The digest is by **content**, so a touched-but-unchanged file is not tampering
  (`test_a_touched_but_unchanged_input_is_not_tampering`), and it is stable across processes
  (`test_the_inputs_digest_is_stable_across_processes`).
- `exit 0` with 0 collected is never progress and never convergence.

---

## 12. Provider separation

| | agent recovery | infrastructure failure |
|---|---|---|
| what happened | the turn made measurable progress and was cut off | nothing moved; 0 tool calls or 0 mutations |
| observed | `positive4/5/7/8/9/10/11/12`, `positive2` | `positive6`, `negative-or`, and the previous mission's provider outage |
| classification | `environment` + `meaningful_progress` → `REPAIR` | `environment` + `no_progress` → `DIAGNOSTIC` |
| outcome | continuation, and in 5 runs `goal_met` | bounded, then `escalated_to_human` |

The previous mission's `hard-or` run was a **provider** failure (repeated HTTP 502) and is
**not** counted here. In this mission every failure recorded as agent recovery has a
**non-empty mutation and a measured movement in the objective**; every run with zero mutations
was classified `no_progress` and escalated. No provider outage was counted as recovery.

**Provider notes.** The OpenRouter key had to be re-supplied mid-mission: the first key worked
at 07:24 and was revoked by 07:34. The venv has **no CA path**, so `SSL_CERT_FILE=<certifi>/cacert.pem`
is required for any outbound HTTPS. `~/.config/wisp/.env` remains write-only (F57) — a key
placed there is never read, so it must be exported. Both keys appear in this session's
transcript and should be rotated.

---

## 13. Performance and boundedness

- **Progress evaluation is host-side and deterministic**: pure comparison of two payload dicts.
  It costs **no model call**, no subprocess, and no I/O. It adds two dict lookups per criteria
  id per attempt.
- **Journal growth**: the baseline record plus, per attempt, the measurement payloads (which
  include a 400-character `output_tail`). For the 18-module runs the journal is ~10 KB.
- **No additional tool calls or provider calls**: the loop already measured after each attempt;
  progress reuses that measurement.
- **Boundedness** is unchanged and enforced by three existing mechanisms plus one new check:
  `max_attempts`, `R5` (no repeated rung), the ladder's budgets, and `PROGRESS_CONTINUATION_RUNGS`
  being empty for every class that forbids the continuation rung.
  `test_p11_progress_does_not_make_recovery_unbounded` drives four consecutive
  progress-producing timeouts and asserts termination, no repeated rung, and an honest final
  state. Live, `positive7` and `positive10` both terminated on budget with `goal_failed`, and
  `positive6`/`negative-or` escalated after two attempts.
- **Wall time**: the added work is a `pytest` run per attempt that the loop already performed.
  Measured overhead is below the run-to-run variance of the provider.

---

## 14. Remaining limitations

1. **F61 — the continuation rung is available exactly once.** `R5` forbids repeating a rung, so
   after one `REPAIR` a further progressing timeout falls to `DIAGNOSTIC`, whose directive
   forbids editing. `positive10` measured the cost precisely: attempt 1 completed **14 of the 17
   outstanding files** and a second continuation would have finished the objective. This is the
   exact reason the mission's §12 shape — *work genuinely left over, and a continuation that
   finishes it* — is not reached in a single run. It is **not** patched here: `R5` is on the
   mission's preserved-contract list, and relaxing it ("a rung that made measurable progress is
   not a failed strategy") is its own decision with its own ADR. The evidence for taking it is
   now in hand.
2. **F60 — a `PASS`ing verdict on a timed-out turn is reported `goal_failed`.** ADR-0035 row 3
   makes a fatal terminal error `GOAL_FAILED` and row 6 requires `turn_succeeded` for
   `GOAL_MET`, so a run whose every measurement is `exit 0` can still end `goal_failed`. This is
   a false **negative** — the mirror of F37 — and it is why `positive5/8/9/11/12` needed a second
   attempt at all. Not caused by progress awareness (the verdict was `pass` and the state
   `goal_failed` on every attempt) and not patched, because it means changing ADR-0035's
   precedence.
3. **The budget window is narrow and provider-latency dependent.** The agent's strategy has two
   phases — survey, then write — and the transition is sharp: at 36 s it surveyed and wrote 1
   module; at 41 s it wrote all 18. So a budget either cuts the survey short (no progress →
   conservative) or lets the write phase complete. Two of twelve runs landed in between. This is
   a property of the task shape and the model, not of the mechanism, and it is recorded rather
   than tuned away.
4. **`PROGRESS_UNDETERMINABLE` is conservative by design**, so an objective whose acceptance
   cannot be measured per-criterion never continues on progress. That is the intended trade
   (treating "cannot tell" as progress would make every unmeasurable objective continue
   forever), but it means the mechanism only helps objectives with machine-checkable criteria.
5. **Derived acceptance is still a closed grammar** (the workspace's own verification command,
   or a named definition in a named file). The `-x` flag in the detected command is why the
   pass count had to be made authoritative; a project whose suite reports neither a pass nor a
   failure count would be `PROGRESS_UNDETERMINABLE`.
6. **`F47` (the plan is write-only), `F48` (default `auto_edit` blocks the agent's own
   `run_bash`), `F57` (`.env` is write-only), and the graph/convergence split remain open.**
   None was required for this mission's property, and §13 forbids expanding scope without the
   experiment proving it necessary.
7. **Model dependence.** Five of twelve configurations converged on attempt 0 outright; the
   results above are for two specific models and do not generalise.

---

## 15. Final verdict

The mission's required trajectory — *real failure → measurable meaningful progress →
progress-aware continuation → materially different second attempt → independent verification →
`GOAL_MET`* — is present, live, and verifiable from the durable journal, in five runs. The
`GOAL_MET` is host-produced: the probe re-measured the repository, every required criterion had
valid evidence, and the integrity criterion confirms the measure was not edited. No simulated
success, no synthetic model claim, no hidden harness mutation, no weakened acceptance, no
skipped regression.

The boundary is real and is stated in §14: in those five runs attempt 0 had already satisfied
the objective, so the continuation closed the turn rather than writing remaining code. The
variant where the continuation writes genuinely remaining work **is** demonstrated
(`positive10`: 14 of 17 outstanding files; `positive7`: 1 file), but those runs did not converge
because the ladder permits exactly one continuation (F61).

```text
PROGRESS-AWARE RECOVERY: DEMONSTRATED

Live trajectory (positive12, OpenRouter stealth/space-bunny-alpha, 15 modules / 60 functions,
WISP_TURN_TIMEOUT=34s, wall 51.9s):

  attempt 0  INITIAL      turn_succeeded=false  failure_code=E1101
                         failure_class=environment
                         progress=meaningful_progress
                           verify:cmd0: failing checks 1→0
                           verify:cmd0: passing checks 0→60
                           verify:cmd0: exit 1→0 (60 passed)
                           15 file(s) changed by the attempt
                         tool_calls=19  duration=36.1s  session=converge-199269aca0
                         verdict=pass  goal_state=goal_failed
                         → RecoveryLadder: ENVIRONMENT + MEANINGFUL_PROGRESS
                           widened by PROGRESS_CONTINUATION_RUNGS to REPAIR

  attempt 1  REPAIR       materially different strategy: different rung, different directive
                         (action-first continuation), different session, and shown what
                         improved as well as what was still unmet
                         turn_succeeded=true  tool_calls=18  duration=15.4s
                         session=converge-d3cb1e99f1
                         verdict=pass  goal_state=goal_met

  independent verification   verify:cmd0 = exit 0, collected 60, failed 0
                             verify:cmd0:inputs_unchanged = content digest identical to baseline
                             (tests/, pyproject.toml, conftest.py untouched)

Boundary, stated with the same evidence: in this run attempt 0 had already reached exit 0, so
attempt 1 completed the turn rather than writing remaining code. The runs where the continuation
did write genuinely remaining work (positive10: 14 of 17 outstanding files, passing 1→44;
positive7: 1 file, passing 40→44) did not converge, because R5 permits exactly one continuation
and those objectives needed two (F61). Closing that requires a decision about R5, which is on
this mission's preserved-contract list and was therefore not changed.
```
