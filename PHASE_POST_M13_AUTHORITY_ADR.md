# PHASE POST-M13 — COMPLETION / RECOVERY AUTHORITY ADR

**Phase type:** architecture-decision phase. **No production code was modified.**
**Date:** 2026-09-24
**Predecessors:** `PHASE_POST_M13_AUTHORITY_RECON.md`, `PHASE_POST_M13_VERDICT_CONTRACT.md`
**Produces:** **ADR-0035**, appended to `WISP_ARCHITECTURE_DECISIONS.md`

---

## 0. Where the ADR lives (a deliberate deviation, explained)

The brief asked for `<repo-standard-ADR-location>/POST-M13_COMPLETION_RECOVERY_AUTHORITY.md` *"or the
repository-equivalent naming convention."* The repository has **two** ADR conventions, and the
migration uses only one of them:

| Convention | Location | Used by | Last entry |
|---|---|---|---|
| **The migration's** | `WISP_ARCHITECTURE_DECISIONS.md` — an **append-only numbered log**, "Decisions are never edited — a superseded decision is marked `SUPERSEDED by ADR-NNNN` and a new entry is appended"; status vocabulary `ACCEPTED · SUPERSEDED · PROVISIONAL` | every migration phase | **ADR-0034** |
| A pre-migration one | `docs/adr/YYYY-MM-DD-<slug>.md` — dated standalone files | Phase-1 era only | `2026-09-04-*`, before the migration |

**ADR-0035 was therefore appended to `WISP_ARCHITECTURE_DECISIONS.md`**, which is the repository-equivalent
naming convention. A standalone duplicate file was **deliberately not created**: it would be a second
authority for one decision, which is the defect class this repository exists to remove (and which the
log's own header forbids by being append-only).

Also updated: the **decision index** at the foot of the log, and `CONTEXT.md:45`'s ADR range
(`ADR-0001 … ADR-0034` → `ADR-0001 … ADR-0035`).

---

## 1. Evidence inspected

**Documents** — in the brief's order: `PHASE_POST_M13_VERDICT_CONTRACT.md`,
`PHASE_POST_M13_AUTHORITY_RECON.md`, `PHASE_M13_REPORT.md` (§7 items 8–9), `WISP_TARGET_ARCHITECTURE.md`
(§5, §16), ADR-0016, ADR-0026, ADR-0032, ADR-0017, ADR-0018, ADR-0028, ADR-0027,
`WISP_MIGRATION_STATUS.md`, `CONTEXT.md`.

**Implementation, traced by call site:** `core/acceptance.py`, `core/verification.py`,
`core/recovery.py`, `core/stagnation.py`, `core/runtime.py`, `core/stateless.py`, `core/events.py`,
`core/session.py`, `tests/reliability/test_13h5_success_derivation.py`,
`tests/test_stagnation_live_wiring.py`, `tests/test_doc_drift.py`.

### 1.1 Four facts re-verified against source in this phase

The ADR was written against the repository that exists, not against the documentation. Four claims were
checked and one was corrected.

| Claim | Verified | Evidence |
|---|---|---|
| `WISP_RECOVERY_LADDER` is an existing rollback flag | **FALSE — corrected** | It appears only in `WISP_MIGRATION_PLAN.md:482`, `WISP_ARCHITECTURE_DECISIONS.md:766` (ADR-0026's reversal condition) and `PHASE_P6_REPORT.md:10,169`. **No `.py` file, and not in `config.py`.** It is a *reserved name for a future flag* |
| `graph_oscillation_guard` exists and is read | **TRUE** | `config.py:256,618,888-889` (default `true`); read at `stagnation.py:222` via `from_config` |
| `record_verdict` exists, defaults off | **TRUE** | `config.py:319,634,912-913` (default `false`); read at `runtime.py:669` |
| Every journal event kind has a producer | **FALSE** | `verdict_event` → `runtime.py:1048` ✅; `stagnation_event` → `runtime.py:1070` ✅; **`recovery_event` (`session.py:230`) and `escalation_event` (`session.py:245`) have no production caller** |

The fourth finding is new in this phase and materially affects the replay contract (§6): the recovery
track's records have **no producer at all**, so the escalation's state-bearing durability (ADR-0028)
cannot currently be exercised on the live path.

### 1.2 A correction made to the predecessor

`PHASE_POST_M13_VERDICT_CONTRACT.md` §17 item 6 listed `WISP_RECOVERY_LADDER` alongside the two real
flags, implying it exists. That line has been **corrected in place** to state that it is reserved and must
be added. This is a documentation-only correction of a factual error introduced by the previous phase;
no behaviour or code is affected. Disclosed here rather than made silently.

---

## 2. Current authority model (as implemented, before this ADR)

```
COMPLETION TRACK (partly live)
  AcceptanceCriteria + Evidence
        ↓
  VerificationFloorGuard            verification.py:120
        ├─ rejection()  →  consulted at stateless.py:766  ← can WITHHOLD `done`
        └─ resolved()   →  verification.py:193-196
        ↓  (published at stateless.py:367, read per ADR-0018)
  floor_guard_verdict()             verification.py:293-301
        ↓
  CompletionVerdict PASS/FAIL/INCONCLUSIVE   acceptance.py:178-210
        ↓
  recorded as VERDICT (opt-in, default OFF)  runtime.py:1042-1052
        ↓
  ✗ nobody consumes it

  terminal evidence → turn_succeeded = saw_done ∧ ¬saw_fatal_error   runtime.py:878

RECOVERY TRACK (not live)
  ProgressSignal → StagnationDetector → { may_report_goal_met(), route_to_recovery() }
                                              ✗ no caller        ✗ no caller
  runtime (message, recoverable, code)
        ↓
  classify_failure_signal()  →  FailureClass   recovery.py:181     ✗ no caller
        ↓
  RecoveryLadder.decide() → RecoveryDecision   recovery.py:512     ✗ no caller
```

**Goal-level completion does not exist.** `GOAL_MET` appears in `wisp/` only inside a docstring
(`stagnation.py:295`); `GoalState` appears in **no `.py` file**.

**Precedence:** none among the implemented mechanisms. The repository's only explicit statement
(`WISP_TARGET_ARCHITECTURE.md:391-405`) governs an unimplemented vocabulary, is cited by no ADR, and
cannot express `INCONCLUSIVE`.

---

## 3. Decision made

**ADR-0035: completion and recovery are two authorities; completion is evaluated first, and stagnation
vetoes goal-met.**

Nine clauses, in the ADR. In summary:

1. **Two tracks, two authorities.** Completion answers *"is the work complete?"*; recovery answers *"what
   should happen next?"* Neither answers the other's question.
2. **Turn-level completion is unchanged** — `turn_succeeded = saw_done and not saw_fatal_error`. A **new
   goal-level state** is derived from three journaled inputs.
3. **P3 is the acceptance input, not a second authority.** `PASS` = the floor is satisfied and every
   required criterion has valid evidence; it does **not** mean the goal is complete.
4. **The detector feeds both tracks with different outputs** — `may_report_goal_met()` to completion,
   `route_to_recovery()` to recovery. They never compete.
5. **Completion is evaluated first** (pre-`done` gate), **recovery second** (turn boundary). A recovery
   decision therefore cannot prevent this turn's completion, nor declare one.
6. **`INCONCLUSIVE` is neither pass nor failure** → a distinct state, `GOAL_UNVERIFIED`.
7. **`UNKNOWN` stays non-blocking** — and non-blocking is *not* "becomes success".
8. **Classification is not authority** (ADR-0032) — it feeds the recovery track only.
9. **Recorded first, enforced later** — the goal state is recorded audit-only under the P3 stage-3a
   pattern; enforcement waits for ADR-0016's measurement.

**The minimum goal taxonomy — six states**, §16's five minus the two that are *reasons* in this
implementation, plus the one §16 cannot express:

`GOAL_MET` · `GOAL_UNVERIFIED` · `GOAL_STAGNATED` · `GOAL_FAILED` · `ESCALATED_TO_HUMAN` · `CANCELLED`

`BUDGET_EXHAUSTED` and `TIMED_OUT` are **`reason`s within `GOAL_FAILED`**, because the current
implementation cannot distinguish them at goal level and `BudgetGovernor` is not on the live path.
`GOAL_UNVERIFIED` is **required, not invented**: without it `INCONCLUSIVE` would have to be recorded as
`GOAL_MET` (violating invariant 1) or `GOAL_FAILED` (a claim the evidence does not support).

---

## 4. Precedence matrix

The ordered arbiter. Highest row wins. A total order over the *goal state* — not a severity score over
mechanisms.

| # | Condition | Result | Basis |
|---|---|---|---|
| 0 | A terminal state is already recorded | **frozen** | ADR-0020 |
| 1 | Operator cancellation | `CANCELLED` | §16 adopted verbatim |
| 2 | Ladder exhausted | `ESCALATED_TO_HUMAN` | **decided here** — suppressing an escalation would convert "a human is required" into "failed" |
| 3 | P3 `FAIL` ∨ fatal terminal error | `GOAL_FAILED` | **decided here** |
| 4 | `may_report_goal_met()` is `False` | `GOAL_STAGNATED` | **adopted from the plan, item 4** |
| 5 | P3 `INCONCLUSIVE` ∨ terminal `INCOMPLETE` | `GOAL_UNVERIFIED` | **decided here**; invariant 1 |
| 6 | `turn_succeeded` ∧ P3 `PASS` | `GOAL_MET` | §16's `GOAL_MET` definition |

Conflict cases, all deterministic:

| A | B | Result |
|---|---|---|
| P3 `PASS` | P7 `STAGNATING` | `GOAL_STAGNATED` — the turn may still be `turn_succeeded` |
| P3 `FAIL` | recovery requested | `GOAL_FAILED`; the recovery request is honoured for the **next** step only |
| P3 `INCONCLUSIVE` | recovery requested | `GOAL_UNVERIFIED`; recovery honoured for the next step |
| P3 `FAIL` | terminal success | `GOAL_FAILED` — **terminal success cannot override an authoritative failure verdict** |
| P3 `INCONCLUSIVE` | terminal success | `GOAL_UNVERIFIED` |
| P7 `STAGNATING` | terminal success | `GOAL_STAGNATED` |
| P7 `UNKNOWN` | terminal success | non-blocking → falls through to the acceptance rule |
| `SECURITY` failure | retry candidate | **retry forbidden by class**; the ladder escalates (`LEGAL_RUNGS[SECURITY] = {HUMAN}`) |
| cancellation | recovery request | `CANCELLED`; recovery does not run |
| timeout | recovery request | `GOAL_FAILED` (reason `timeout`); recovery may pick a rung for the next attempt only |
| budget exhausted | recovery request | `GOAL_FAILED` (reason `budget`); same |

**§16's `GOAL_MET` beats `BUDGET_EXHAUSTED` is preserved by construction.** A budget-exhausted turn
emits a fatal `CODE_ITERATION_BUDGET` error → `turn_succeeded` is `False` → row 6 is unreachable. The
rule holds vacuously, which is the honest form, since no path makes both conditions true at once.

---

## 5. Goal-vs-turn semantics

| | **Turn** | **Goal** |
|---|---|---|
| Question | *Did this turn finish cleanly?* | *Is the work complete?* |
| Rule | `saw_done ∧ ¬saw_fatal_error` (`runtime.py:878`) | the six-state derivation |
| Consumer | node status (`runtime.py:998-1018`), spans (`:1224`), persistence (`:1274`) | **new** — the completion authority |
| Status | **implemented, unchanged** | **not implemented** — defined by this ADR |

**`turn_succeeded = True` may coexist with `GOAL_FAILED`, `GOAL_UNVERIFIED` or `GOAL_STAGNATED`.** That
coexistence is the substance of the decision: a turn can finish cleanly and still not complete the goal.
Today the two levels are conflated because only the turn level exists. **Consequence: terminal `done` can
no longer imply goal success.**

---

## 6. Replay contract

> A run reconstructed from its journal must reproduce the goal state and the recovery decision
> **exactly** — or fail loud rather than guess.

The goal state is a **pure function of journaled inputs**; it is derived, never separately stored, so it
cannot drift from its inputs (ADR-0029's projection principle).

| Input | Recorded today? | Required |
|---|---|---|
| Terminal outcome (`done` / fatal `error` + code) | **yes** (`session_events`, F4) | keep |
| P3 acceptance verdict | **opt-in** — `record_verdict` defaults `False` | **mandatory whenever the goal state is authoritative** |
| P7 stagnation predicate | **partial** — `STAGNATION` written only when the verdict is reached | **a positive record per evaluated turn**, else replay cannot tell "not stagnating" from "not evaluated" |
| M12 `FailureClass` | **no** — `recovery_event` has no producer | required before the recovery track is authoritative |
| Recovery decision | **no** — same | required |
| Escalation | **no** — `escalation_event` has no producer, though `ESCALATION` is the only state-bearing kind | required |
| Cancellation / budget | yes (terminal `error` codes) | keep |

**Two blocking gaps:** under today's defaults a reconstructed run **could not reproduce its own goal
state** — the acceptance verdict is off by default, and a non-stagnating turn writes no stagnation
record. Both must be closed before enforcement is honest.

`ESCALATION` keeps ADR-0028's treatment (state-bearing, fail-loud); the other records stay best-effort
audit.

---

## 7. Rejected alternatives

| Alternative | Why rejected |
|---|---|
| A unified verdict | No normalizer exists; building one creates a fifth vocabulary and a second place that decides what an outcome means |
| Adopt §16 wholesale | Unimplemented, unratified, cannot express `INCONCLUSIVE`, and diverges from the implementation on stagnation routing |
| Treat P3 as an independent authority | `floor_guard_verdict` derives its criteria *and evidence* from the guard (ADR-0018's whole point) |
| Treat stagnation as generic failure | `FailureClass.STAGNATION` is `# working, not progressing` (`recovery.py:63`) |
| Let recovery imply completion | A `RecoveryDecision` carries no completion claim (invariant 4) |
| Let terminal `done` imply goal completion | The status quo, and the exact conflation being removed (invariant 8) |
| Promote `BUDGET_EXHAUSTED`/`TIMED_OUT` to states | Would require wiring `BudgetGovernor`; not this decision |
| Make escalation orthogonal to the goal state | `ESCALATION` is state-bearing by ADR-0028 |
| Put the recovery consumer before the completion gate | Recovery needs a failure class, which needs the terminal outcome |

---

## 8. Implementation boundary

The next phase **may**:

1. Add the goal-state derivation and its **audit-only** record (the P3 stage-3a pattern).
2. Make the acceptance verdict and the stagnation verdict **mandatory** when the goal state is
   authoritative.
3. Add producers for `RECOVERY` and `ESCALATION` records.
4. Wire the completion consumer at the pre-`done` gate (`stateless.py:761-806`), bounded, with the floor
   guard's honest-surrender property.
5. Wire the recovery consumer at the turn boundary (`runtime.py:867-878`).
6. Add the recovery flag under the name ADR-0026 reserved — `recovery_ladder` / `WISP_RECOVERY_LADDER`
   (**it does not exist in code today**).
7. Replace the two M13 tripwires with their inverses **in the same commit** that wires the consumers.

**Not authorised:** changing `turn_succeeded`; changing `VerificationFloorGuard` or its criterion;
changing `evaluate`'s rules; repairing F8; refactoring fanout or `dag.py`; M1/M5/M6/M7/M10; graph-driven
execution; any new execution engine; and **enabling enforcement before ADR-0016's measurement exists**.

---

## 9. No production code was modified

**Confirmed by mechanical comparison, not by assertion.**

| Check | Result |
|---|---|
| `git diff` vs the pre-phase snapshot | **byte-identical** — no tracked file changed |
| `git status` delta | this phase's documents only |
| `wisp/` or `tests/` modified | **none** |
| Tests weakened, tripwires changed | **none** — `test_stagnation_live_wiring.py` re-run: **27 passed** |
| F8 repaired | **no** |

Files written by this phase:

- `WISP_ARCHITECTURE_DECISIONS.md` — **ADR-0035** + its decision-index row (documentation)
- `CONTEXT.md` — the ADR range on line 45 (documentation)
- `PHASE_POST_M13_AUTHORITY_ADR.md` — this report
- `PHASE_POST_M13_VERDICT_CONTRACT.md` — **one line corrected** (§17 item 6), disclosed in §1.2 above
- `.workbuddy-ai/memory/post-m13-contract/` — read-only baseline artifacts

No file under `wisp/` or `tests/` was touched.

---

## 10. Exact next implementation phase

> **`PHASE_POST_M13_AUTHORITY_IMPLEMENTATION`** — wire the completion and recovery consumers per ADR-0035,
> **recording first, enforcement behind flags.**

Bounded scope, in order:

1. **Add the goal-state derivation** (`GOAL_*`, six states) as a pure function of the three inputs, with
   an **audit-only** record. No behaviour change.
2. **Close the two durability gaps** — the acceptance verdict and a positive per-turn stagnation record
   must be recorded whenever the goal state is authoritative.
3. **Add `RECOVERY` / `ESCALATION` producers** so the recovery track's records exist before it becomes
   authoritative.
4. **Add the `recovery_ladder` flag** (reserved by ADR-0026), default **off**.
5. **Wire the two consumers** — completion at the pre-`done` gate (bounded, honest surrender); recovery
   at the turn boundary.
6. **Invert the two M13 tripwires in the same commit.**
7. **Verify** with the contract's test matrix (`PHASE_POST_M13_VERDICT_CONTRACT.md` §14) — the eight
   conflict tests become implementable once step 1 exists.

Enforcement stays behind flags until ADR-0016's measurement exists. F8 remains a **measurement blocker**
for the P3 stage-3b rate, not a contract blocker.

---

## 11. Validation checklist (brief §23)

| Check | Result |
|---|---|
| References actual implementation symbols | **yes** — every claim carries `file:line`; the ADR cites `verification.py:159/193/293-301`, `acceptance.py:178-210/213`, `stagnation.py:222/294/331`, `recovery.py:181/512/585-586`, `runtime.py:878`, `stateless.py:766/367`, `session.py:230/245` |
| Every claimed authority verified against source | **yes** — §1.1; one claim corrected (`WISP_RECOVERY_LADDER`) |
| No target-architecture feature presented as implemented | **yes** — §16 and §5.1 are explicitly marked unimplemented and unratified; `GoalState` in no `.py` file is stated |
| Every precedence-matrix conflict has a deterministic answer | **yes** — 12 rows, all answered |
| `INCONCLUSIVE` has explicit semantics | **yes** — `GOAL_UNVERIFIED`; never `PASS`, never `FAILED` |
| `P3 PASS + P7 STAGNATING` has explicit semantics | **yes** — `GOAL_STAGNATED`; the plan's item 4 supplies the rule |
| Recovery cannot imply completion | **yes** — clause 5; `RecoveryDecision` carries no completion claim |
| Terminal `done` cannot become goal success | **yes** — `GOAL_MET` requires P3 `PASS` (invariant 8) |
| Turn-level and goal-level not conflated | **yes** — §5, with the coexistence table |
| Replay requirements explicit | **yes** — §6, including the two blocking gaps |
| Next phase has bounded scope | **yes** — §10, seven steps plus a non-goals list |
| Tests run only for read-only validation | **yes** — `test_stagnation_live_wiring.py` (27 passed) |

### Invariants (brief §18) — all twelve ratified

| # | Invariant | Status |
|---|---|---|
| 1 | `INCONCLUSIVE` cannot silently become `PASS` | **RATIFIED** — `GOAL_UNVERIFIED`; `evaluate` returns `PASS` only via `ALL_REQUIRED_SATISFIED` with cited valid evidence |
| 2 | `STAGNATING` cannot silently become successful completion | **RATIFIED** — arbiter row 4 |
| 3 | `UNKNOWN` cannot silently become successful completion | **RATIFIED** — non-blocking ≠ converted to `PASS`; explicitly distinguished |
| 4 | Recovery routing cannot imply success | **RATIFIED** — clause 5 |
| 5 | Failure classification is not completion authority | **RATIFIED** — clause 8; ADR-0032 |
| 6 | Empty observations cannot create completion | **RATIFIED** — F32's guard; `observe` returns `UNKNOWN` before touching state |
| 7 | Terminal success cannot override an explicitly authoritative failure state | **RATIFIED — introduced by this ADR** (today terminal evidence wins) |
| 8 | Completion authority cannot be bypassed because a provider emitted `done` | **RATIFIED — introduced by this ADR**; `done` yields at most `GOAL_UNVERIFIED` |
| 9 | Replay reproduces the same authoritative result | **RATIFIED as a contract**; two durability gaps must be closed first (§6) |
| 10 | A recovery decision cannot erase recorded evidence | **RATIFIED** — the journal is append-only; recovery records are additive |
| 11 | Cancellation remains distinguishable from failure | **RATIFIED** — distinct state `CANCELLED` |
| 12 | Escalation remains distinguishable from successful completion | **RATIFIED** — distinct state `ESCALATED_TO_HUMAN` |

No invariant was changed. Two (7 and 8) are **introduced**, and both are marked as such rather than
presented as existing behaviour.

---

## FINAL REPORT

```
ADR STATUS: RATIFIED
```

### Decision

Completion and recovery are two authorities answering two different questions. Completion — *"is the
work complete?"* — keeps its existing owner at turn level (`turn_succeeded`, unchanged) and gains a
derived six-state goal level (`GOAL_MET`, `GOAL_UNVERIFIED`, `GOAL_STAGNATED`, `GOAL_FAILED`,
`ESCALATED_TO_HUMAN`, `CANCELLED`) computed from the terminal outcome, the P3 acceptance verdict and the
P7 stagnation predicate. Recovery — *"what should happen next?"* — is owned by `RecoveryLadder.decide()`.
Completion is evaluated first, at the pre-`done` gate; recovery second, at the turn boundary; so a
recovery decision can neither prevent this turn's completion nor declare one. `INCONCLUSIVE` yields
`GOAL_UNVERIFIED` rather than being collapsed into pass or failure, and stagnation vetoes `GOAL_MET`. The
goal state is recorded audit-only first and enforced only behind flags, after ADR-0016's measurement.

### Authority Model

| Mechanism | Role | Authority over | Cannot decide |
|---|---|---|---|
| `VerificationFloorGuard.rejection()` | completion gate (negative) | whether `done` may be emitted | goal state; recovery |
| `VerificationFloorGuard.resolved()` | verified-completion predicate | whether the turn earned completion | goal state; recovery |
| P3 `CompletionVerdict` / `evaluate()` | acceptance verdict (input) | `PASS`/`FAIL`/`INCONCLUSIVE` | the goal state; recovery |
| `StagnationDetector.observe()` / `.verdict` | progress verdict | `UNKNOWN`/`PROGRESSING`/`STAGNATING` | completion; recovery routing |
| `may_report_goal_met()` | completion input | whether the goal may be called met | recovery; classification |
| `route_to_recovery()` | recovery input | converting a progress verdict to a `FailureClass` | completion; the rung |
| M12 `classify_failure_signal()` | classification | which `FailureClass` applies | completion; recovery; the rung |
| `RecoveryLadder.decide()` / `.escalate()` | recovery authority | the next rung; escalation | completion; failure; the goal state; erasing evidence |
| terminal evidence | turn-completion authority | `turn_succeeded` | goal state; recovery; verification |
| `turn_succeeded` | turn-level completion | node status, spans, persistence | goal state |

### Critical Precedence

1. **`CANCELLED` pre-empts everything** except an already-recorded terminal state.
2. **`ESCALATED_TO_HUMAN` outranks `GOAL_FAILED`** — suppressing an escalation would silently convert "a
   human is required" into "failed".
3. **`P3 FAIL` outranks terminal success** — terminal `done` cannot override an authoritative failure.
4. **`STAGNATING` outranks `P3 PASS`** for the goal answer → `GOAL_STAGNATED`. The turn may still be
   `turn_succeeded`.
5. **`INCONCLUSIVE` yields `GOAL_UNVERIFIED`** — never `GOAL_MET`, never `GOAL_FAILED`.
6. **`UNKNOWN` never blocks** and is never converted into `PASS`.
7. **`SECURITY` failures never retry** — forbidden by class; the ladder escalates.
8. **Recovery never implies completion**, and completion never implies recovery.

### Goal vs Turn

**Turn**: *did this turn finish cleanly?* — `saw_done ∧ ¬saw_fatal_error`. Implemented, unchanged, drives
node status, spans and persistence.
**Goal**: *is the work complete?* — the six-state derivation. Not implemented before this ADR.
**They can disagree**: `turn_succeeded = True` may coexist with `GOAL_FAILED`, `GOAL_UNVERIFIED` or
`GOAL_STAGNATED`. **Terminal `done` no longer implies goal success.**

### Replay Contract

The goal state is a **pure function of journaled inputs** and is derived, not stored, so it cannot drift.
Must survive reconstruction: the terminal outcome (`done` / fatal `error` + code), the **acceptance
verdict** (currently opt-in — must become mandatory), a **positive stagnation record per evaluated turn**
(currently written only when stagnating), the `FailureClass`, the recovery decision, and escalation.
**Two gaps block enforcement today**: under default settings a reconstructed run could not reproduce its
own goal state.

### Implementation Boundary

`PHASE_POST_M13_AUTHORITY_IMPLEMENTATION` may add the goal-state derivation and its audit-only record,
close the two durability gaps, add `RECOVERY`/`ESCALATION` producers, add the `recovery_ladder` flag
(default off), wire the two consumers (completion at the pre-`done` gate; recovery at the turn boundary),
and invert the two M13 tripwires in the same commit.

### Non-Goals

`turn_succeeded`; `VerificationFloorGuard`; `evaluate`'s rules; F8/`jsonschema`; fanout and
`multi_agent/dag.py`; M1/M5/M6/M7/M10; graph-driven execution; any new execution engine; and **enabling
enforcement before ADR-0016's measurement exists**.

### Production Changes

```
NONE
```

No file under `wisp/` or `tests/` was modified; no test was weakened; no tripwire was changed; F8 was not
repaired. Verified by byte-identical `git diff` against the pre-phase snapshot.
