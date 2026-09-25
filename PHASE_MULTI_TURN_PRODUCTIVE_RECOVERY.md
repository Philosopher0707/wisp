# PHASE — MULTI-TURN PRODUCTIVE RECOVERY & COMPLETION AUTHORITY

**Mission:** resolve F60 (should a failed turn with an independent acceptance `PASS` be
`GOAL_FAILED`?) and F61 (should a productive continuation be consumed permanently?), then
implement the architecture that makes both answers semantically correct, bounded, replayable and
empirically defensible.

**Status:** decided, implemented, adversarially tested, exercised live.
**Decision:** ADR-0047. **Findings:** F60 and F61 resolved; **F62** and **F63** found and fixed
while building the tests. **New tests:** 50. **Regression:** 930 passed, 0 failed (625 s).

---

## 1. Executive summary

Both questions turned out to be the **same mistake in two places**: a fact about the *attempt*
was being used as a fact about the *objective*.

**F60.** `derive_goal_state`'s row 3 read *"P3 FAIL **or fatal terminal error**"*. A turn timeout
therefore outranked an independent acceptance `PASS`, and a repository that satisfied **every**
objective criterion was reported `GOAL_FAILED`. It was not an oversight — it was a ratified row
with its own test (`test_t10_timeout_is_goal_failed`). The live evidence that it is wrong:
five runs reached `exit 0`, recorded verdict `pass` on every attempt, terminated `goal_failed`,
and each spent a whole extra attempt re-attempting an objective that was already met.

**F61.** R5 forbade *a rung* from repeating. That unit was right while the only thing a rung
could carry was a failure. ADR-0046 gave rungs a second possible payload — a **success that has
not finished** — and then the rule became actively harmful. Measured live: attempt 1 of
`positive10` completed **14 of the 17 outstanding files** and moved the objective from 1 to 44
passing checks, *more work than the first attempt*; it needed one more continuation, and R5
refused it, falling to `DIAGNOSTIC` — whose directive is *"Do not edit any file in this
attempt"*. The run was **guaranteed** to fail while every attempt made measurable progress.

**What changed, in one line each.**

- `derive_goal_state` row 3's fatal-error clause became row 4, qualified by **"and no P3 PASS"**,
  and `turn_succeeded` stopped arbitrating. Exactly one input combination moved: *fatal error +
  `PASS`*, `GOAL_FAILED` → `GOAL_MET`.
- R5's unit became **the same strategy against materially unchanged state**, with
  `MEANINGFUL_PROGRESS` — computed from the objective's own measurement — as the host-owned
  witness that the state changed, bounded by a new durable **`productive_continuations`** budget.
- An **authorization event** is excluded from completion *before* the verdict is consulted, so a
  denial can never be laundered into `GOAL_MET`.
- Two defects found while testing were fixed: **F63** (the stagnation witness was a function of
  *when* it was taken, not of *what was there*) and **F62** (`--resume` with no journal crashed).

**Live result.** Two runs carry the empirical weight, and between them they close both
questions.

**The two-continuation run** (`f60-live3`: 18 modules / 72 functions, 41 s turn budget,
`max_attempts=3`, `productive_continuations=2`) is the mission's §15 target trajectory exactly:

```
attempt 0  INITIAL   timeout E1101, 38 tools,  3 files, passing  0→8    → MEANINGFUL → REPAIR
attempt 1  REPAIR    timeout E1101, 12 tools,  1 file,  passing  8→12   → MEANINGFUL → REPAIR (re-chosen)
attempt 2  REPAIR    timeout E1101, 24 tools, 14 files, passing 12→72   → verdict PASS → GOAL_MET
```

**Two productive continuations**, the second one a *re-choice* of `REPAIR` charged to the new
budget; every attempt timed out; every attempt made measurable progress; every failure survives
in the journal; and the run terminates `GOAL_MET` on the harness's own measurement.

**The single-continuation run** (`f61-live`: 18 modules, 36 s budget) shows the same mechanism
finishing genuinely-remaining work in one step — attempt 0 wrote 8 files (`0→24` passing),
attempt 1 wrote **11 more** (`24→72` passing) — and its attempt 1 is also the cleanest **F60**
witness: the turn timed out and the objective was `PASS`, so the run concluded `GOAL_MET` where
the old semantics would have said `GOAL_FAILED` and demanded a third attempt.

---

## 2. F60 semantic analysis

The conflict, precisely: `derive_goal_state` received `terminal_outcome=FAILED` and
`acceptance_verdict=PASS`, and row 3 fired before row 6 could.

**The question the mission poses** — *if the repository has independently satisfied every
objective acceptance criterion, what exactly remains for the objective to fail?* — has one
answer: **nothing**. The acceptance verdict is produced by `CommandProbe` over a workspace the
model did not write the criteria for, and a `PASS` requires (rule 1) at least one required
criterion, (rule 2) every required deterministic check to hold, (rules 3–4) valid, non-invalidated
evidence for each. That *is* the objective being met. `terminal_outcome` says the **attempt** was
cut off.

**Why the fatal clause exists, and what it is still for.** A fatal error with *no* decisive
verdict is a run that was aborted without establishing anything — that is still `GOAL_FAILED`,
and it must still outrank stagnation (a *heuristic* must not soften a *fact*). So the clause was
qualified, not removed.

**Why the precedence could not simply be reordered.** Three preserved rules must hold at once:

| Rule | Source |
|---|---|
| a fatal error outranks stagnation | `test_a_fatal_error_with_a_closed_predicate_stays_goal_failed` |
| stagnation outranks `PASS` | `test_t2_pass_and_stagnating_is_goal_stagnated` |
| `PASS` outranks a fatal error | this mission (F60) |

As priorities that is `fatal > stagnation > PASS > fatal` — a **cycle**, so no ordering of rows
can express it. The cycle is broken where it is *semantically* broken: the fatal clause is the
only one whose meaning depends on the verdict, because it is the only one that is a statement
about the attempt rather than about the objective. Hence the conjunction.

### 2.1 The conflict cases

| Case | Turn | Verdict | Progress | Objective state | Why |
|---|---|---|---|---|---|
| **F60-A** | failed | `PASS` | meaningful | **`GOAL_MET`** | the objective is met; the continuation that finished it timed out |
| **F60-B** | failed | `PASS` | none | **`GOAL_MET`** | progress is observational; it does not decide completion |
| **F60-C** | failed | `FAIL` | any | `GOAL_FAILED` | row 3; unchanged |
| **F60-D** | succeeded | `PASS` | any | `GOAL_MET` | unchanged |
| **F60-E** | succeeded | `FAIL` | any | `GOAL_FAILED` | unchanged |
| **F60-F** | failed | `INCONCLUSIVE` | any | `GOAL_FAILED` | row 4 — aborted with nothing established; unchanged |
| **F60-G** | failed | `PASS` *claimed*, inputs moved | — | `GOAL_FAILED` | **unreachable as `PASS`**: the integrity criterion is *required*, so tampering makes the verdict `FAIL` |
| **F60-H** | failed | `PASS` + denial | any | **`ESCALATED_TO_HUMAN`** | an authorization event is terminal for the *run*; a denial is never absorbed |

F60-G is worth its own sentence: the honest answer is not "a `PASS` we distrust" but "no `PASS`
can exist", because `verify:cmdN:inputs_unchanged` is a **required** criterion. The matrix has no
`PASS + tampered` row at all.

---

## 3. F61 semantic analysis

R5's text is *"a rung that would repeat an already-failed rung for the same failure is
illegal"*. The unit it protects is the **rung**. That unit was correct for exactly as long as a
rung could only carry one thing.

ADR-0046 changed the payload. A rung can now carry a *success that has not finished*, and for
that payload the question is not "did this rung already run?" but **"is the state it would act on
still the state it failed on?"**. `MEANINGFUL_PROGRESS` answers exactly that, and it is computed
from the objective's own measurement — so the answer is host-owned, deterministic, and never the
model's word.

**The refinement is therefore one condition, not a new mechanism:**

```
a rung may be re-chosen  ⟺  the previous attempt produced MEANINGFUL_PROGRESS
                            AND the productive-continuation budget has room
```

No progress → the original R5, byte for byte. Budget spent → no re-choice, and the ladder
escalates.

**Why the bound is a separate budget.** `productive_continuations` counts *re-choices across the
whole objective*, separately from the rung's own budget. `REPAIR` has no budget entry of its own,
so without this it could repeat without limit; and `local_replans` answers a different question
(*how much replanning*) than this one (*how much continuing*). Charging both — the rung's own
budget when it has one, plus this — is what keeps the two bounds from standing in for each other.

**Why no new strategy-identity mechanism (§6).** The mission asks whether two consecutive
attempts are "the same strategy". The journal already carries the fingerprint — rung, directive,
evidence lines, measurement digest, session, files changed — and the *decidable* part of "same
strategy" is the part that matters: **was the state materially unchanged?** `MEANINGFUL_PROGRESS`
is false exactly when it was. Adding a second identity mechanism would be a second authority for
a question the progress verdict already answers, so R10 of ADR-0047 declines to add one.

---

## 4. Authority matrix

| Question | Authority | Touched? |
|---|---|---|
| what is the objective state? | `goal.derive_goal_state` | **amended** (rows 3–6); still the only answer |
| is the work done? | `acceptance.evaluate` | **no** |
| did the attempt move the objective? | `core/progress.py` | **no** |
| what kind of failure is this? | `recovery.classify_failure*` | **no** |
| what may be tried next? | `RecoveryLadder.decide` + the legality tables | **refined** (R5's unit + one budget) |
| did the turn complete? | `terminal_outcome` / `turn_succeeded` (ADR-0044) | **no** — demoted from arbitration, not changed |
| what did the run conclude? | the aggregation in §5 | stated once, in one place |

The hierarchy is intact and one-directional:

```
turn outcome → verification/acceptance → progress observation → recovery decision → GoalState
```

Nothing upstream is reachable from downstream. `progress` cannot produce `GOAL_MET`; a rung
cannot; the model cannot; and `turn_succeeded` alone cannot.

---

## 5. Decision matrix

Every row is a call to `derive_goal_state` — the one authority — so the table cannot drift from
the implementation. The **run-level aggregation** is separate and stated once:

> A run's state is the ladder's escalation if it surrendered, otherwise the **last attempt's
> derived state**.

| Turn | Acceptance | Progress | Objective state |
|---|---|---|---|
| success | `PASS` | any | `GOAL_MET` |
| success | `FAIL` | any | `GOAL_FAILED` |
| failure | `PASS` | meaningful | `GOAL_MET` |
| failure | `PASS` | none | `GOAL_MET` |
| failure | `FAIL` | meaningful | `GOAL_FAILED` |
| failure | `FAIL` | none | `GOAL_FAILED` |
| failure | `INCONCLUSIVE` | meaningful | `GOAL_FAILED` |
| failure | `INCONCLUSIVE` | none | `GOAL_FAILED` |
| failure | `PASS` | tampered input | *unreachable as `PASS`* — the integrity criterion is required, so the verdict is `FAIL` → `GOAL_FAILED` |
| failure | `PASS` | security violation | `ESCALATED_TO_HUMAN` (authorization event, before the verdict) |
| repeated productive failure | incomplete | meaningful | per attempt `GOAL_FAILED`; the run continues while the productive budget lasts, then `GOAL_FAILED` or `ESCALATED_TO_HUMAN` |
| repeated productive failure | incomplete | none | per attempt `GOAL_FAILED`; the run does **not** continue (`R5`), and escalates when no rung is left |

Deterministic, single-valued, and pinned by `TestF60TheDecisionMatrix`.

---

## 6. Chosen architecture and rejected alternatives

**Chosen.** F60: qualify the fatal clause with *"and no P3 PASS"* and stop arbitrating on
`turn_succeeded`. F61: `MEANINGFUL_PROGRESS` relaxes R5 for one case, bounded by a new durable
`productive_continuations` budget.

| Rejected | Why |
|---|---|
| **Design A** — preserve strict R5 | Measured to *guarantee* failure for an objective needing two continuations, even when every attempt is productive. |
| **Design B** — productive continuation with no new budget | "Productive" would be the only bound; a task advanced one unit at a time would never terminate. §5's invariant is explicit. |
| **Design C alone** — a budget without the progress condition | A budget alone permits `REPAIR → REPAIR` against *unchanged* state, which is the blind retry R5 exists to forbid. |
| **Design E** — a phase ladder (`INITIAL → RECOVERY → CONTINUATION → …`) | A second vocabulary for a question the rung already answers (`REPAIR` *is* "continue the work"). The distinction that matters is not *which phase* but *whether the state changed*. |
| **A new `GoalState`** for "objective met, turn incomplete" | ADR-0035 fixes six states; the information is already durable on the attempt; `GoalState` should answer one question. |
| **Re-classifying a progressing timeout** | Already rejected in ADR-0046 and still rejected: a timeout is `ENVIRONMENT` either way. |

---

## 7. ADR

**ADR-0047** — *A failed turn is not a failed objective, and R5's unit is the strategy, not the
rung.* Rules R1–R13. It amends ADR-0035's precedence rows 3–6 and ADR-0046's use of R5, and
changes no other contract.

---

## 8. Implementation

| File | Change |
|---|---|
| `wisp/core/goal.py` | `PRECEDENCE` rows 3–6 restated with the cycle documented; `derive_goal_state` reordered with the qualifier; `turn_succeeded` demoted to a recorded fact |
| `wisp/core/recovery.py` | `RecoveryBudget.productive_continuations` (default 2); `PRODUCTIVE_BUDGET`; `_is_meaningful_progress`; `legal_rungs(..., exclude=)` with the refined R5; `decide(..., exclude=)` charging the productive budget on a re-choice; `snapshot()` reports it |
| `wisp/core/convergence.py` | `WITNESS_FIELDS` / `witness_digest` (**F63**); `Measurement.digest` over the state-bearing projection; evidence identity via the same projection; the failure class computed for *every* attempt; `authorization_event` excluded from completion and escalated before the goal state is derived; `passed = verdict PASS`; `_WANTS_FIX_RE` widened to three phrasings; `_next_rung` passes `exclude`; `_resume_recovery` guards the empty case (**F62**) |
| `tests/reliability/test_multi_turn_productive_recovery.py` | **new**, 50 tests |
| `tests/reliability/test_next_convergence_controller.py` | the F60 test revised into a pair, plus the authorization-event test |
| `tests/reliability/test_progress_aware_recovery.py` | fixture criteria promoted to match the fixture's own objective text; P11 restated as the refined bound |

---

## 9. Test matrix

`tests/reliability/test_multi_turn_productive_recovery.py` — **50 tests**, all passing.

| Group | Covers |
|---|---|
| `TestF60TheDecisionMatrix` | the 11-row matrix; F60-A/B; F60-G (tamper is unreachable as `PASS`); F60-H (denial escalates); the cost measurement (no wasted attempt) |
| `TestF60DoesNotWeakenTerminalHonesty` | fatal-without-`PASS`; fatal > stagnation; stagnation > `PASS`; INCOMPLETE → unverified; row 0 frozen |
| `TestF61TheContinuationContract` | C3+C4 (progress permits); C6 (no progress forbids); C6b (`SECURITY` never widened); C7 (budget bounds); C7b (budget durable and reported); C8 (no acceptance bypass); C9 (tamper cannot unlock); C10 (append-only journal) |
| `TestF61ContinuationIsNotBlindRetry` | the rule stated directly against the ladder; a regression cannot unlock; `exclude` prevents a stall |
| `TestResumeReproducesTheContinuationDecision` | interrupted == uninterrupted across **two** continuations; torn journal |
| `TestStagnationInteraction` | cases 21–25 |
| `TestTheWitnessIsDeterministic` | F63 — same state ⇒ same digest/ids; changed state ⇒ different; the excerpt is still recorded; stagnation detected reliably |
| `TestFalsificationProbes` | A–J |

---

## 10. Falsification results

Three **mutation probes**, each breaking a mechanism and watching the suite catch it:

| Probe | Mutation | Result |
|---|---|---|
| revert F60 | row 4's `and acceptance != "pass"` → `and True` | **3 tests fail** — the two `failed + PASS` matrix rows and the progress-independence test |
| remove the bound | `if self.governor.exhausted(PRODUCTIVE_BUDGET)` → `if False` | **2 tests fail** — C7 and the zero-budget test |
| delete R5's condition | `if not productive: continue` → `if False` | **3 tests fail** — C6, C9, and the tiny-churn stagnation test |

Probe-by-probe (§14):

| Probe | Outcome |
|---|---|
| **A** — a timed-out turn that satisfies everything | Turn failure and objective satisfaction are **separately readable** from one record: `turn_succeeded=False`, `verdict=pass`, `goal_state=goal_met`. |
| **B** — three consecutive productive turns | Continues safely; stops when the budget is spent; `len(turn.calls) == 4` for a budget of 2. |
| **C** — repeated edits with no improvement | Terminates at 2 attempts (no repeat is legal without progress). |
| **D** — alternating progress/regression | `progress → regression → progress` is `MEANINGFUL → NO_PROGRESS → …`; the regression does **not** read as progress and does not unlock a continuation. |
| **E** — restart between every continuation | One attempt per process, four processes: the journal's rung sequence is `INITIAL, REPAIR, REPAIR, REPAIR` and each process makes exactly one new attempt. |
| **F** — corrupt the last journal record | Discarded; reconstruction resumes at the next index; deterministic. |
| **G** — modify verification inputs | Still prevents success: `inputs_unchanged` fails, the verdict is `FAIL`, and `progress` is `PROGRESS_UNDETERMINABLE` so it cannot unlock a continuation either. |
| **H** — provider-only timeout | Zero tool calls, zero mutations → `NO_PROGRESS` → `DIAGNOSTIC`. Never `GOAL_MET`. |
| **I** — a security denial | `legal_rungs` for `SECURITY` is unchanged by **any** progress value; asserted as equality of candidate sets for `SECURITY`, `REPEATED` and `STAGNATION`. |
| **J** — the model declares "I made progress" / "I am done" | Structurally impossible to matter: `evaluate_progress` reads no model field, and `derive_goal_state` contains no model input. |

**One falsification succeeded, and it was the point of the mission.** F60 *was* the falsification:
the matrix's `failed + PASS` row was asserted the other way for a year's worth of decisions, and
the live evidence says it was wrong. It is now revised, with the reasoning in the test itself
rather than deleted.

---

## 11. F60 live experiment

The F60 case — *a real turn timeout whose independent verification says `PASS`* — was produced
three times, in three different runs, and in every one of them the turn really ended through the
runtime's own fatal outcome rather than being staged:

| Run | Attempt | Turn | Verdict | Objective state |
|---|---|---|---|---|
| `f61-live` | 1 (`REPAIR`) | `E1101`, 33 tools, 37.2 s | `exit 0, 72 passed` | **`GOAL_MET`** |
| `f61-live2` | 1 (`REPAIR`) | `E1101`, 26 tools, 36.6 s | `exit 0, 96 passed` | **`GOAL_MET`** |
| `f60-live3` | 2 (`REPAIR`) | `E1101`, 24 tools, 42.0 s | `exit 0, 72 passed` | **`GOAL_MET`** |
| `f60-live` | 0 (`INITIAL`) | `E1101`, 18 tools, 36.2 s | `exit 1, 44 passed` | `GOAL_FAILED` — one module short, so *no* `PASS`; the continuation finished it |

Against §16's checklist, using `f61-live` attempt 1:

| Requirement | Evidence |
|---|---|
| a real turn timeout | `turn_succeeded=False`, `failure_code=E1101`, `terminal_outcome=failed`, 33 tool calls, 37.2 s |
| independent verification `PASS` | `verify:cmd0` = `exit 0, collected 72, failed 0` |
| acceptance host-derived | `derive_acceptance` over the objective text and the workspace's own verification command |
| verification inputs unchanged | `verify:cmd0:inputs_unchanged` PASS; `inputs_digest` identical to the baseline |
| the agent did not fake the result | 11 files written by the agent, 72 tests passing, no input touched |
| the turn really ended through a fatal outcome | the observation is the runtime's own `E1101` |
| the final state per the new semantics | **`GOAL_MET`** |

Two runs failed to produce the case, and both are reported rather than dropped: `f60-live2`
(18 modules / 45 s) had attempt 0 **survey without writing** — 10 tool calls, 0 files — so there
was nothing to complete and the run escalated conservatively, which is the negative path working;
and `f60-live`'s first configuration left one module over, so its verdict was `FAIL` and the
`PASS` arrived on the continuation instead.

---

## 12. F61 live multi-turn experiment

**The decisive run is `f60-live3`**, and it is the trajectory §15 asks for: a task requiring
**two** productive continuations.

18 modules × 4 functions = 72 functions. `WISP_TURN_TIMEOUT=41`, `max_attempts=3`,
`productive_continuations=2`. Provider OpenRouter (`stealth/space-bunny-alpha`). Wall 127.2 s.

| | attempt 0 | attempt 1 | attempt 2 |
|---|---|---|---|
| rung | `INITIAL` | **`REPAIR`** | **`REPAIR`** (re-chosen) |
| turn outcome | `E1101` | `E1101` | `E1101` |
| tool calls / duration | 38 / 42.2 s | 12 / 42.6 s | 24 / 42.0 s |
| files changed | 3 | 1 | **14** |
| progress | `meaningful_progress` — `passing checks 0→8` | `meaningful_progress` — `passing checks 8→12` | `meaningful_progress` — `failing checks 1→0`, `passing checks 12→72` |
| measurement | `exit 1, collected 8, failed 1` | `exit 1, collected 12, failed 1` | **`exit 0, collected 72, failed 0`** |
| verdict | `fail` | `fail` | **`pass`** |
| goal state | `goal_failed` | `goal_failed` | **`goal_met`** |

Read against §15's target, element by element:

| Target | Evidence |
|---|---|
| attempt 0 does real work and times out | 3 files, `0→8` passing, `E1101` |
| progress is `meaningful` | four authoritative signals across the run, all from the harness |
| **continuation** | `REPAIR`, selected on the progress verdict, not on a claim |
| attempt 1 times out and makes progress | 1 file, `8→12` passing |
| **continuation again** | `REPAIR` re-chosen — legal only because attempt 1 advanced the objective, and charged to `productive_continuations` |
| attempt 2 finishes the remaining work | 14 files, `12→72` passing, `exit 0` |
| independent verification | the harness's own `CommandProbe`; `inputs_unchanged` PASS |
| `GOAL_MET` | reached on the last attempt's derived state |
| every failure preserved | three `attempt` records, each with `turn_succeeded=False`, `E1101`, and its own progress verdict |

Two further runs corroborate the single-continuation form: `f61-live` (8 → **11** files,
`24→72` passing) and `f61-live2` (8 → **16** files, `8→96` passing), each reaching `GOAL_MET` in
two attempts with the continuation doing the majority of the work.

**A note on the interaction between the two decisions.** `f61-live` and `f61-live2` needed only
*one* continuation because F60 removed the "close the turn" attempt that used to consume a
second one — under the old semantics, their attempt 1 (`E1101` with a `PASS` verdict) would have
been `GOAL_FAILED` and would have demanded another attempt. The two decisions are therefore not
independent, and the honest reading is that F60 makes multi-turn recovery *rarer but more
effective*: fewer attempts are spent, and the ones that are spent do work.

---

## 13. Resume / replay evidence

- `test_interrupted_equals_uninterrupted_across_two_continuations` runs a four-attempt trajectory
  twice: whole, and interrupted after attempt 1 then resumed in a fresh controller told nothing
  but the journal path. The rung sequence, every attempt's progress verdict, and the final
  `GOAL_MET` are identical; completed attempts are not re-run.
- `test_probe_e_restart_between_every_continuation` restarts between **every** attempt — one
  process per attempt, four processes — and the journal's rung sequence is
  `INITIAL, REPAIR, REPAIR, REPAIR`, with each process making exactly one new attempt. The
  productive budget is spent by the *replayed* decisions, so a restart cannot hand it back.
- The journaled **baseline** is preferred over a fresh measurement, so a resumed run is judged
  against the same criteria the interrupted one was.
- `test_probe_f` and `test_a_torn_journal_does_not_break_the_continuation` corrupt the final
  record; it is discarded and the run continues at the next index.

---

## 14. Stagnation evidence

| Case | Result |
|---|---|
| 21 — genuine progress is not stagnation | every productive attempt's failure class is `environment`, never `stagnation` |
| 22 — a repeated unchanged measurement is stagnation | attempt 1's class is `stagnation` |
| 23 — tiny churn cannot reset stagnation into progress | cosmetic rewrites give `no_progress` on every attempt and **cannot unlock a repeat** |
| 24 — a regression is not progress | `no_progress` with the signal `failing checks 3→5`, and no continuation |
| 25 — the latch stays monotonic | `core/stagnation.py` is not reachable from this mechanism (asserted structurally: it mentions neither the budget nor the continuation) |

**F63 belongs here.** Writing case 22 exposed that the stagnation predicate was a coin flip:
`Measurement.digest` hashed the whole payload, and `output_tail` ends with the command's
**elapsed time**. Measured directly — three probes of one unchanged workspace produced three
different digests (`"3 failed in 0.04s"` vs `"0.05s"`). So `repeated = digest ==
stagnation_witness` could be false for a genuinely stagnant run, which would classify it
`IMPLEMENTATION` and take `REPAIR` instead of `GLOBAL_REPLAN`; and ADR-0046 R10's replay
determinism did not hold. The witness is now a digest over `WITNESS_FIELDS` only, and the same
projection identifies evidence. The prose excerpt is still recorded — it is evidence; it is
simply not an identifier.

---

## 15. Acceptance-integrity evidence

- **A tampered input is unreachable as a `PASS`.** `verify:cmdN:inputs_unchanged` is a *required*
  criterion over a content digest, so F60's wider completion door cannot be walked through: the
  matrix has no `PASS + tampered` row (case F60-G, tested).
- **Tampering cannot unlock a continuation either.** A moved digest yields
  `PROGRESS_UNDETERMINABLE`, which is not `MEANINGFUL_PROGRESS`, so R5 is untouched (case C9).
- **An authorization event is never absorbed.** A denial is excluded from completion before the
  verdict is consulted and escalates through the ladder's own `SECURITY` row (case F60-H).
- `exit 0` with zero collected is still not progress and not convergence.
- **The criteria are now the sole gate, and that is stated as a consequence.** Before F60 a
  timeout accidentally masked weak criteria. `criteria_for`'s guards-only case is a legitimate
  *no-regression* objective, and `GOAL_MET` on it is honest; but an objective that plainly
  requires a green suite must say so, so `_WANTS_FIX_RE` was widened from one phrasing to three.
  Two fixture suites in this repository had criteria weaker than their own objective text, and
  both were corrected — the fixture is the experiment.

---

## 16. Provider separation

| | agent recovery | infrastructure failure |
|---|---|---|
| what happened | the turn made measurable progress and was cut off | nothing moved |
| observed | `f61-live` attempts 0 and 1; `f60-live` attempts 0 and 1 | `f60-live2` (survey without writing → 0 files) |
| classification | `environment` + `meaningful_progress` → `REPAIR` | `environment` + `no_progress` → `DIAGNOSTIC` |
| outcome | continuation, then `GOAL_MET` | bounded, then `escalated_to_human` |

No HTTP 401, 502, connection, TLS or provider-unavailability event is counted anywhere in this
report. `f60-live2` is the honest near-miss: it is a *coding* failure (the agent surveyed and
never wrote), not a provider failure, and it took the conservative path.

**Provider notes.** The venv has no CA path, so `SSL_CERT_FILE=<certifi>/cacert.pem` is required
for outbound HTTPS. `~/.config/wisp/.env` remains write-only (F57), so keys must be exported. The
OpenRouter key used here appears in this session's transcript and should be rotated.

---

## 17. Performance and boundedness

- **No model call is added.** Progress is a pure comparison of payloads the loop already
  measured; `derive_goal_state` is pure. The F60 and F61 changes add dict lookups and one integer
  budget, nothing more.
- **The bound is explicit and reported.** `BudgetGovernor.snapshot()` now includes
  `productive_continuations`, so the four conceptually distinct budgets are all answerable:
  *how many model turns can this objective consume* (`max_attempts`), *how many productive
  continuations* (this), *how many recovery strategy changes* (the rung budgets), *when does it
  surrender* (`ESCALATED_TO_HUMAN`).
- **Measured live.**

  | Run | Task | Budget | Attempts | Rungs | Tool calls | Wall | Continuations | Escalations |
  |---|---|---|---|---|---|---|---|---|
  | `f60-live3` | 18 modules / 72 fn | 41 s | 3 | `INITIAL, REPAIR, REPAIR` | 74 | 127.2 s | **2** | 0 |
  | `f61-live2` | 24 modules / 96 fn | 36 s | 2 | `INITIAL, REPAIR` | 41 | 74.3 s | 1 | 0 |
  | `f61-live` | 18 modules / 72 fn | 36 s | 2 | `INITIAL, REPAIR` | 96 | 75.2 s | 1 | 0 |
  | `f60-live` | 15 modules / 60 fn | 34 s | 2 | `INITIAL, REPAIR` | 24 | 58.2 s | 1 | 0 |
  | `f60-live2` | 18 modules / 72 fn | 45 s | 2 | `INITIAL, DIAGNOSTIC` | 28 | 94.9 s | 0 | **1** |

  The last row is the conservative path and it is the useful one: zero mutations → `no_progress`
  → `DIAGNOSTIC` → `escalated_to_human`, with no third attempt attempted.
- **Journal growth** is unchanged in shape: a baseline record plus one record per attempt, each
  carrying the measurement payloads. The three-attempt run's journal is under 12 KB.
- **Boundedness is pinned** by C7, C7b, probe B, probe C and P11: four consecutive
  progress-producing timeouts terminate with exactly `productive_continuations` re-choices, and a
  zero budget restores R5 byte for byte.
- **Full regression: 930 passed, 0 failed**, 625.27 s — `tests/reliability/` plus the recovery,
  classification, escalation, stagnation, acceptance and benchmark suites. No existing test was
  disabled or weakened; three were *revised* where they pinned the semantics this mission
  changed (`test_t10`, P11, and two fixture criteria sets), each with the reasoning in the test.

---

## 18. Remaining limitations

1. **F60 makes the criteria load-bearing.** The objective state is now a function of the
   objective's evidence, so a weak criteria set produces a weak conclusion. The mitigation is
   host-derived criteria plus the widened `_WANTS_FIX_RE`, but the residual is real: a
   *guards-only* criteria set (a red baseline, no promotion, no symbol criterion) is a
   no-regression objective, and `GOAL_MET` on it means "nothing got worse" — honest, and less
   than a reader might assume.
2. **The live window is narrow and provider-latency dependent.** The two-continuation case was
   reached, but only after four configurations: the agent's strategy has a *survey* phase and a
   *write* phase, and a budget either cuts the survey short (nothing written → no progress →
   conservative) or lets the write phase complete (the objective is met). Measured across the
   runs in §11–§12, the per-call latency varied roughly fourfold, so the same task and budget can
   land in either regime. This is a property of the task shape and the provider, not of the
   mechanism, and it is recorded rather than tuned away.
3. **The productive budget is a policy number.** `2` is a default, not a measurement. §8's
   warning — do not overload `max_attempts` — is honoured by keeping it separate, but the
   *value* has no empirical basis beyond "small".
4. **A rung with no budget of its own can be re-chosen up to the productive budget even when a
   cheaper untried rung exists.** That is intended (continue the strategy that is working), but
   it means `REPAIR` will be preferred over `LOCAL_REPLAN` while it keeps producing progress.
5. **`cancelled` is still not passed to `derive_goal_state` on the convergence path.** A
   cancellation arrives as a `SECURITY`-classified failure and escalates rather than reporting
   `CANCELLED`. Pre-existing, unchanged, and now also covered by the authorization rule.
6. **The witness projection is a fixed field list.** A new probe payload field that *is*
   state-bearing would be invisible to the witness until `WITNESS_FIELDS` is updated. Pinned by a
   test on the projection, not on the producer.
7. **`F47`, `F48`, `F57` and the graph/convergence split remain open** and were not required by
   either question.

---

## 19. Exact final architecture

```text
USER OBJECTIVE
      ↓
HOST-DERIVED ACCEPTANCE CRITERIA            (derive_acceptance; _WANTS_FIX_RE, 3 phrasings)
      ↓
CONVERGENCE CONTROLLER                      (bounded, journaled, resumable)
      ↓
TURN  ──► terminal_outcome / turn_succeeded (ADR-0044; recorded, not arbitrating)
      ↓
HARNESS MEASUREMENT                         (CommandProbe; witness = state-bearing fields)
      ↓
ACCEPTANCE VERDICT                          (PASS / FAIL / INCONCLUSIVE)
      ↓
PROGRESS VERDICT                            (NO_PROGRESS / MEANINGFUL / UNDETERMINABLE)
      ↓
RECOVERY DECISION
      │   legal rungs  = class table ∪ continuation rungs (MEANINGFUL only)
      │   R5 refined   = a rung may repeat ⟺ MEANINGFUL_PROGRESS
      │                  AND productive_continuations has room
      │   authorization events → escalation, before the verdict is consulted
      ↓
GOAL STATE                                  (rows 3–6: FAIL → FAILED; fatal w/o PASS → FAILED;
                                             stagnating → STAGNATED; PASS → MET; else UNVERIFIED)
      ↓
RUN AGGREGATION   escalation if the ladder surrendered, else the last attempt's state
      ↓
PERSISTENCE / REPLAY                        (append-only journal; baseline first)
```

---

## 20. Final verdict

| Property | Verdict | Basis |
|---|---|---|
| Turn failure and objective failure are semantically separated | **DEMONSTRATED** | row 4's qualifier; `turn_succeeded` demoted; the matrix |
| `PASS` has a deterministic treatment when the turn fails | **DEMONSTRATED** | `GOAL_MET`, live in `f61-live` attempt 1 |
| One authoritative `GoalState`, no false success introduced | **DEMONSTRATED** | F60-C/E/F/G/H tests; the denial exception |
| F60 live | **DEMONSTRATED** | `f61-live` attempt 1 (and `f60-live` attempt 0→1) |
| Productive continuation has a formal contract (C1–C10) | **DEMONSTRATED** | `TestF61TheContinuationContract` |
| Strategy repetition remains prohibited | **DEMONSTRATED** | C6/C9, probe D, mutation probe 3 |
| Productive continuation can continue when objectively justified | **DEMONSTRATED** | live (`REPAIR` re-chosen and finishing the work) |
| Continuation is bounded | **DEMONSTRATED** | C7/C7b, probe B, P11, mutation probe 2 |
| Stagnation still terminates unproductive loops | **DEMONSTRATED** | cases 21–25, and F63 fixed |
| Resume reproduces the exact decision | **DEMONSTRATED** | two continuations; one process per attempt |
| **Live multi-turn recovery needing ≥2 continuations** | **DEMONSTRATED** | `f60-live3`: `INITIAL → REPAIR → REPAIR`, three timeouts, `0→8→12→72` passing, `GOAL_MET` |
| Acceptance remains independent | **DEMONSTRATED** | §15 |
| Progress remains observational | **DEMONSTRATED** | the matrix is invariant under the progress value |
| The model remains non-authoritative | **DEMONSTRATED** | probe J, structurally |
| Provider failures remain distinct | **DEMONSTRATED** | §16 |
| Audit history remains append-only | **DEMONSTRATED** | C10, probe E |
| No unrelated architecture reopened | **DEMONSTRATED** | only `goal.py`, `recovery.py`, `convergence.py` |

```text
F60 COMPLETION AUTHORITY: DEMONSTRATED
F61 MULTI-TURN PRODUCTIVE RECOVERY: DEMONSTRATED
WISP CONVERGENCE AUTHORITY: CLOSED
```

**What "closed" rests on, stated plainly.** Both questions were the same mistake — a fact about
the *attempt* used as a fact about the *objective* — and both are now decided, implemented,
adversarially tested, and demonstrated live: a turn that timed out completed an objective whose
evidence said `PASS`; a productive continuation was selected on measured progress, re-chosen
once under a durable bound, and finished the remaining work; and a no-progress turn was routed
conservatively and escalated. The residuals are §18's, and the two that matter most are that the
criteria are now the sole gate (§18.1) and that the live window is narrow (§18.2).
