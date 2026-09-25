# PHASE POST-M13 — COMPLETION ENFORCEMENT POLICY ADR

**Phase type:** architecture decision only. **No production code, tests, configuration, tripwires,
`VerificationFloorGuard`, F8, fanout or graph work.**
**Date:** 2026-09-24
**Decision:** **ADR-0036**, appended to `WISP_ARCHITECTURE_DECISIONS.md`
**Predecessors:** `PHASE_POST_M13_AUTHORITY_RECON.md` · `PHASE_POST_M13_VERDICT_CONTRACT.md` ·
`PHASE_POST_M13_AUTHORITY_ADR.md` (ADR-0035) · `PHASE_POST_M13_AUTHORITY_IMPLEMENTATION.md` ·
`PHASE_POST_M13_LIVE_COMPLETION_SEAM_RECON.md`

---

## 1. Baseline

| Property | Value |
|---|---|
| HEAD | `7c15626` — `docs(m11): record the M11 phase; findings F29-F31; repair the commit table` |
| Branch | `main` |
| Working tree | **dirty** — 88 entries; the Phase 10/13 WIP plus this session's post-M13 documents, **all treated as immutable** |
| Diff at baseline | 174,566 bytes |
| Last ADR before this phase | ADR-0035 (`WISP_ARCHITECTURE_DECISIONS.md:1334`) |
| Existing flags | `graph_oscillation_guard` (default **true**), `goal_state` (**false**), `recovery_ladder` (**false**), `record_verdict` (**false**), `task_graph` (**false**) |

Snapshot: `.workbuddy-ai/memory/post-m13-policy/{head,branch,status-before}.txt`, `diff-before.patch`.

---

## 2. Evidence inspected

The four policy documents above, ADR-0016/0018/0020/0026/0028/0029/0032/0035, and — because a decision
must be implementable against the repository that exists — the **current source**:

| Source | What was read for |
|---|---|
| `core/stagnation.py` (whole) | `may_report_goal_met` (`:294-305`), `verdict` (`:264-278`), `trap_fired` (`:280-288`), `observe` (`:225-262`), `to_dict` (`:307-313`), the replan-not-retry docstring (`:331-350`) |
| `core/verification.py` (`:24-206`) | `HARNESS_REJECTION`, `INVARIANT_STATEMENT`, `compose_nudge` (`:44-58`), `SHORT_REPEAT_NUDGE`, `VerificationFloorGuard` (`:119-206`) — `min_turns=5`, `max_nudges=2`, `rejection()` (`:159-191`), the honest-surrender rule (`:176-179`), `resolved()` (`:193-196`) |
| `core/stateless.py` | `turn()` (`:204-216`), `_turn_inner()` (`:315-318`), the guard + `self._last_guard` (`:341-367`), the loop bound (`:368`), **the pre-`done` gate** (`:761-806`), the incomplete-round return (`:752-760`), the iteration wrap-up (`:940-964`), `steering_drain` (`:909-911`) |
| `core/runtime.py` | the detector's construction, the `core.turn` call site (`:750-752`), the terminal derivation (`:902`), the recovery consumer (`:1149-1183`), the goal derivation + record (`:1198-1227`) |
| `core/goal.py` (whole) | `GoalState`, `TerminalOutcome`, `derive_goal_state` (`:107-155`), `goal_state_from_record` (`:189-205`) |
| `core/recovery.py` (`:86-136`) | `LEGAL_RUNGS[STAGNATION] = {GLOBAL_REPLAN, DIAGNOSTIC, HUMAN}` (`:107-109`), `FORBIDDEN_RUNGS[STAGNATION] = {RETRY, REPAIR}` (`:131`), `LEGAL_RUNGS[SECURITY] = {HUMAN}` (`:110`) |
| `config.py` | the flag table and its `get_setting` reads |

**Two facts were established by reading, not by assuming**, and both changed the decision:

- the arbiter's row-4 input and the record's evidence are **different computations** (§5, finding **F35**);
- `trap_fired` is a **latch** (`stagnation.py:288`), so one class of stagnation can never be cleared.

---

## 3. Current authority model (as it stands, before this ADR)

```
                          AgentRuntime.run_turn
                                  │
             ┌────────────────────┴─────────────────────┐
             │                                          │
     COMPLETION TRACK                             RECOVERY TRACK
             │                                          │
  P3 (floor_guard_verdict, ADR-0018)            M12 classify_failure_signal
  M13 may_report_goal_met()                     P7 route_to_recovery
             │                                          │
  derive_goal_state()  ← runtime.py:1198         RecoveryLadder.decide()
             │                                          │
       GOAL_* (6 states)                          RecoveryDecision / ESCALATED_TO_HUMAN
             │                                          │
             └──────────────────┬───────────────────────┘
                                ▼
                          journal (audit)
```

**Where the two tracks meet, and where they do not.** The completion **gate** lives in the engine
(`stateless.py:766`) and is the only place completion can be *prevented*; it is today owned solely by
`VerificationFloorGuard`. The goal-state **derivation** lives in the runtime (`runtime.py:1198`) and is
**recorded, not enforced**. The recovery track runs at the turn boundary (`runtime.py:1149`), **only when
`turn_succeeded` is false**, and governs the next step only.

**The one missing edge** — the same edge every post-M13 phase has circled: **M13's predicate is consumed
as a *record* but never as a *control*.** `may_report_goal_met()` decides what is written; nothing asks it
before `done` is emitted.

---

## 4. The decision

**One sentence:** *stagnation may withhold `done` for a bounded number of replan interventions, and then
it surrenders — it never vetoes the turn.*

The full decision, its evidence and its tables are in **ADR-0036**. The four axes, as decided:

| Axis | Question | Decision |
|---|---|---|
| **A** | delay or veto? | **`DECISION A = BOUNDED DELAY / REPLAN.`** The gate withholds `done` for at most the bound, then falls through to `done`. A veto is rejected because **both** of its implementations produce outcomes ADR-0035 already forbids: `GOAL_FAILED` via `CODE_ITERATION_BUDGET` (which is "treat stagnation as generic failure", its own rejected alternative), or a silent end with no terminal event (13-H1) |
| **B** | the intervention | A **replan-shaped continuation message**, appended and yielded exactly as the floor guard's nudge is (`stateless.py:788-790`). Text composed in **`stagnation.py`** (M13's home), emitted by the engine — the repo's GH#27 convention, already in the source at `stateless.py:332-336`. **Generic by design:** it names the condition and the instruction, not the repeated action, because the model's transcript already carries the specifics and naming them would widen the boundary past a predicate |
| **C** | the bound | **2 per turn**, owned by the engine beside `guard.nudges_used`, reset per turn, inherited as a **constructor default** (as `max_nudges` is) rather than a new config key. Plus a third condition — `iteration + 1 < max_iterations` — because the last iteration cannot be withheld without converting the surrender into a `CODE_ITERATION_BUDGET` failure |
| **D** | failure policy | **`DECISION D = FAIL OPEN`** — a `None` gate, a raising callable, or a disabled detector all permit `done`. It **cannot** manufacture a false `GOAL_MET`, because the goal state derives from the *recorded predicate*, not from the gate; and it is not silent, because the record already carries `not_evaluated` |

**Why A1 is not merely preferable but required.** ADR-0035 `:1542-1545` already says *"The completion gate
must be bounded… The goal gate must inherit that property, or a stagnating turn would never end."* Under
A1, ADR-0035's two apparently conflicting clauses are **both true**: the gate *did* withhold (line 1528),
and it *then* surrendered with the predicate still closed, so `turn_succeeded = True` coexists with
`GOAL_STAGNATED` (line 1478). **Nothing is superseded** — the conflict was an ambiguity, and it is
resolved by the reading under which both clauses hold.

---

## 5. Finding F35 — the live/replay divergence (new, and blocking)

| Fact | Source | Line |
|---|---|---|
| the arbiter's `stagnating` | `not stagnation_detector.may_report_goal_met()` — **includes `trap_fired`** | `runtime.py:1138-1140` |
| the record's `stagnation_verdict` | `stagnation_detector.verdict` — **ignores `trap_fired`** | `runtime.py:1194-1197` |

`may_report_goal_met()` is `False` on `consecutive_flat >= min_consecutive` **or** `trap_fired`
(`stagnation.py:303-305`). `verdict` returns `STAGNATING` on the first term **only**
(`stagnation.py:276-278`). So when the reused `OscillationTrap` fires below the flat threshold, the live
derivation yields `GOAL_STAGNATED` while the record says `"progressing"` — and the existing replay test
reconstructs from the record (`test_post_m13_authority_implementation.py:350`). **Live and replay
disagree, and the record contradicts its own conclusion.**

**Decided (ADR-0036 §6):** the goal record gains `stagnation_allows_goal_met` — the value of
`may_report_goal_met()` at turn end, i.e. exactly what row 4 consumed — and replay reads that. The defect
is in the record, so the record is what changes; aligning the *live* input to `verdict` instead would
contradict ADR-0035's ratified row 4 and discard the trap's evidence. Recorded as **F35** in the ledger.

**Also ratified, not fixed:** `trap_fired` is a latch, so a trap-sourced stagnation cannot be cleared by
any intervention. The gate's withholding is therefore *neutral-but-costly* in that case — bounded by §4,
and unable to turn a `GOAL_MET` into anything worse than `GOAL_STAGNATED`. Changing it means changing
`may_report_goal_met()`, i.e. M13's semantics, which is out of scope and is named as such.

---

## 6. Precedence matrix (the highest-risk conflict rules)

Full table in ADR-0036. The rules an implementer must not get wrong:

1. **`P3 PASS` + `P7 STAGNATING` → `GOAL_STAGNATED`**, and the turn may still be `turn_succeeded = True`.
   This is the **ordinary** outcome of enforcement, not an edge case.
2. **`P3 FAIL` + terminal success → `GOAL_FAILED`.** A terminal `done` never overrides an authoritative
   failure.
3. **`P3 INCONCLUSIVE` + terminal success → `GOAL_UNVERIFIED`** — never `GOAL_MET`, never `GOAL_FAILED`.
4. **`P7 UNKNOWN` + `P3 PASS` + success → `GOAL_MET`.** `UNKNOWN` stays non-blocking and is never
   synthesised into a `PASS`.
5. **`CANCELLED` beats everything except an already-recorded terminal state** (row 1), and a replan never
   overrides it.
6. **Escalation beats failure** (row 2): `ESCALATED_TO_HUMAN`, not `GOAL_FAILED`.
7. **Timeout / budget exhaustion → `GOAL_FAILED` with a `reason`.** Recovery may choose the next
   attempt's rung; it cannot turn this turn's state into success.
8. **The gate withholds only when all three hold**: predicate closed **and** budget remains **and** a
   further iteration exists.
9. **Recovery runs only when `turn_succeeded` is false**, after the gate — so it can neither prevent nor
   declare this turn's completion, and a *successful* stagnating turn never escalates.
10. **An already-recorded terminal state is frozen** — a duplicate event, a later observation and a
    replay all return it unchanged (ADR-0020).

---

## 7. Goal vs turn

| | Turn | Goal |
|---|---|---|
| Question | *Did this turn finish cleanly?* | *Is the work complete?* |
| Rule | `saw_done ∧ ¬saw_fatal_error` (`runtime.py:902`, 13-H5) — **unchanged** | the six-state derivation |
| Effect of this ADR | **none** | the gate now *acts* on one of its inputs |

**`turn_succeeded = True` with `GOAL_STAGNATED` is the designed outcome** when a stagnating turn exhausts
its intervention budget — ADR-0035 line 1478, holding in the field. **This ADR does not change
`turn_succeeded`, and must not be implemented in a way that does.**

---

## 8. Replay contract

```
LIVE:    the per-turn detector → predicate → the gate's withhold/surrender decision   (transient)
REPLAY:  the recorded predicate → derive_goal_state                                   (durable)
```

The gate's decision is **not replayed** — its consequence is durable in the terminal outcome. What replay
needs is the arbitration **input**, and that is the F35 fix. Once it lands,
`LIVE_DERIVATION(inputs) == REPLAY_DERIVATION(same inputs)` holds, because the goal state is a pure
function of three recorded facts and the arbiter reads nothing else; a reconstruction that cannot read
them fails loud (`goal.py:189-205`).

**Not journaled, and why (§7 of the ADR):** the intervention count and its exhaustion. Neither is used by
the authority; the count would require either a stateful gate closure (breaking the purity/idempotency the
seam recon ratified) or a second engine→runtime publication beside `_last_guard`. The already-required
observability list is complete without it.

---

## 9. Rejected alternatives

Fully tabulated in ADR-0036. The list the brief required, with the decisive reason:

| # | Rejected | Decisive reason |
|---|---|---|
| 1 | a second stagnation detector | two authorities for `UNKNOWN`/`PROGRESSING`/`STAGNATING` |
| 2 | moving M13 into `stateless.py` | creates a second detector during the transition; no evidence the ownership is wrong |
| 3 | handing the engine the detector object | `observe()` mutates — the engine would gain write access to the one authority |
| 4 | global mutable M13 state | leaks across turns/sessions/runs; makes replay depend on live state |
| 5 | a ContextVar for one boolean | the only ContextVar home is scoped to tool execution; an explicit parameter is smaller |
| 6 | retrying the same action | `FORBIDDEN_RUNGS[STAGNATION] ∋ RETRY` (`recovery.py:131`) |
| 7 | stagnation ⇒ `GOAL_FAILED` | `FailureClass.STAGNATION` is *working, not progressing*; ADR-0035 already refuses this |
| 8 | stagnation ⇒ `ESCALATED_TO_HUMAN` | removes the ladder's authority; escalation is a chosen rung |
| 9 | changing `turn_succeeded` for convenience | ADR-0035 non-goals; §7 |
| 10 | changing `VerificationFloorGuard` | STOP 1; the new gate sits beside it |
| 11 | a full planner | the intervention is a message; the transcript carries what the model needs |
| 12 | graph-native completion enforcement | the graph is a shape, not a payload (ADR-0029) |
| + | steering-only delivery | drained at **tool boundaries**, so a final no-tool round never sees it — and it cannot withhold `done` |
| + | the runtime re-invoking `core.turn` | makes the intervention a second turn, hence a second goal state |
| + | aligning the live arbiter to `verdict` | contradicts ADR-0035 row 4 and discards the trap's evidence |

---

## 10. Implementation boundary

The next phase may change **only**:

1. `core/stateless.py` — one optional keyword parameter on `turn()` (`:204`) and `_turn_inner`
   (`:315-318`), threaded at `:303`; one gate evaluation between `:790` and `:791`; one local counter.
2. `core/runtime.py` — the closure over the per-turn detector, passed at `:750-752` behind the flag;
   **`stagnation_allows_goal_met` added to the goal record** (`:1212-1224`) and used by replay.
3. `core/stagnation.py` — the intervention text, beside `route_to_recovery`.
4. `config.py` — `stagnation_gate` / `WISP_STAGNATION_GATE`, **default OFF** (ADR-0002, one flag per
   concern). Two rollback levels: the flag stops enforcement while keeping the record;
   `graph_oscillation_guard` stops the detector, hence both.
5. Tests: S1–S14 from the seam recon, plus the **trap-fired replay case** F35 requires.

**Not authorised:** `VerificationFloorGuard` or its criterion; a second detector; global mutable state;
`turn_succeeded`; ADR-0035's precedence; the two failure-path `done` sites (`:313`, `:964`); F8; the
graph; fanout; M1/M5–M8/M10.

**Non-goals:** enabling the gate by default; a cancellation producer; `BUDGET_EXHAUSTED`/`TIMED_OUT` as
states; subagent/background enforcement (no detector ⇒ no intervention authority); journaling the
intervention count; moving goal derivation into `core.turn()`; M13's detection semantics or its
`min_consecutive`; the ladder's own semantics; F8.

---

## 11. Production changes

```
PRODUCTION CHANGES: NONE
```

Verified, not asserted: the code-path diff (`wisp/`, `wisp-desktop/`, `tests/`) is compared **byte for
byte** against the pre-phase snapshot, and the `git status` delta is inspected for anything but
documentation. Evidence in §12.

---

## 12. Integrity verification

| Check | Method | Result |
|---|---|---|
| No production/test/config change | byte-level comparison of the extracted code-path patch (`wisp/`, `wisp-desktop/`, `tests/`), before vs after | **99,275 bytes both ways — byte-identical.** `git diff --numstat -- wisp/config.py` reads `43+0`, and that is **pre-existing WIP**: the byte-identical code patch is the proof it did not change this phase |
| Only documentation changed | per-file hunk comparison of the whole diff, before vs after | **exactly three files**, all documentation: `CONTEXT.md`, `WISP_ARCHITECTURE_DECISIONS.md`, `WISP_MIGRATION_STATUS.md` |
| Only one file added | `git status` delta vs `status-before.txt` | one line: `?? PHASE_POST_M13_COMPLETION_ENFORCEMENT_POLICY_ADR.md` |
| Tripwires intact | `tests/test_stagnation_live_wiring.py` (the two inverted M13 tripwires) | **passing** |
| Doc guards intact | `tests/test_doc_drift.py`, `tests/test_escalation_durability.py` (the audit-kind totality guard) | **passing** |
| Combined | `pytest tests/test_stagnation_live_wiring.py tests/test_doc_drift.py tests/test_escalation_durability.py` | **79 passed** |
| ADR present | `WISP_ARCHITECTURE_DECISIONS.md` | heading at **`:1645`**, index row at **`:2153`** |
| ADR range updated | `CONTEXT.md:45` | `ADR-0001 … ADR-0036` |

**No `git stash`, `git reset --hard` or `git checkout --` was used at any point.** The baseline is a
snapshot taken outside the tracked tree (`.workbuddy-ai/memory/post-m13-policy/`), and the pre-existing
WIP was never reverted.

### Documentation artifacts added or amended

| File | Change |
|---|---|
| `WISP_ARCHITECTURE_DECISIONS.md` | **ADR-0036** appended in the repo's format, before the decision index; index row added |
| `PHASE_POST_M13_COMPLETION_ENFORCEMENT_POLICY_ADR.md` | **new** — this report |
| `CONTEXT.md` | the ADR range, `ADR-0035` → `ADR-0036` |
| `WISP_MIGRATION_STATUS.md` | **F35** added to the findings log (the live/replay divergence), with its resolution recorded as *decided, not yet implemented* |

---

## FINAL REPORT

```
ADR STATUS: RATIFIED
```

### Decision

Stagnation may withhold `done` at the engine's clean-completion gate for a **bounded number of replan
interventions** (two per turn), and then it **surrenders honestly** and permits the turn to finish —
recorded as `GOAL_STAGNATED` while `turn_succeeded` stays true. It never vetoes the turn. This resolves
ADR-0035's self-conflict by showing both of its clauses are true under bounded delay, so nothing is
superseded; the gate consults exactly the predicate the arbiter's row 4 consumes; the intervention is a
replan-shaped continuation whose text lives in M13's home module; the gate fails open because the goal
state derives from the recorded predicate rather than from the gate; and the goal record gains the
predicate so live and replay cannot disagree.

### Authority Model

| Mechanism | Owns | Cannot |
|---|---|---|
| `VerificationFloorGuard` (unchanged) | the verification floor and its own nudge budget | stagnation; the goal state |
| the completion gate (new) | withholding `done` **once more** on a closed predicate | the predicate's value; the goal state; anything when the predicate is open |
| `may_report_goal_met()` | whether the goal may be reported met | whether `done` is withheld |
| `stagnation.py` | the intervention's wording | whether to intervene |
| the engine's per-turn counter | how many interventions remain | the predicate; the goal state |
| `derive_goal_state()` (unchanged) | the six goal states | withholding; recovery |
| `turn_succeeded` (unchanged) | turn-level completion | the goal state |
| `RecoveryLadder` (unchanged) | the next rung; escalation | completion |

### Critical Precedence

`PASS`+`STAGNATING` → `GOAL_STAGNATED` (turn may still succeed) · `FAIL` beats terminal success ·
`INCONCLUSIVE` → `GOAL_UNVERIFIED` · `UNKNOWN` is non-blocking · `CANCELLED` beats all but a frozen
state · escalation beats failure · timeout/budget → `GOAL_FAILED` with a reason · withhold only if
predicate closed **and** budget remains **and** an iteration remains · recovery runs only on a failed
turn, after the gate · a recorded terminal state is frozen.

### Goal vs Turn

Turn = *did this turn finish cleanly?* (`saw_done ∧ ¬saw_fatal_error`, unchanged). Goal = *is the work
complete?* (the six-state derivation). `turn_succeeded = True` with `GOAL_STAGNATED` is the designed
outcome of enforcement.

### Replay Contract

Live enforcement input is the transient predicate; the durable counterpart is the recorded predicate
(`stagnation_allows_goal_met`, F35). The gate's decision is not replayed — its consequence is durable in
the terminal outcome. The goal state is a pure function of the terminal outcome, the acceptance verdict
and the predicate; missing inputs fail loud rather than guess. The intervention count is deliberately not
journaled.

### Implementation Boundary

`core/stateless.py` (one parameter, one gate evaluation, one counter) · `core/runtime.py` (the closure +
the record key) · `core/stagnation.py` (the text) · `config.py` (`stagnation_gate`, default OFF) · tests
S1–S14 + the F35 replay case. Nothing else.

### Non-Goals

Enabling by default; a cancellation producer; budget/timeout as states; subagent enforcement; journaling
the intervention count; goal derivation in the engine; M13's detection semantics; the ladder's semantics;
F8; the graph; fanout; M1/M5–M8/M10.

### Production Changes

```
NONE
```

### Exact next implementation phase

**`PHASE_POST_M13_COMPLETION_ENFORCEMENT_IMPLEMENTATION`** — implement ADR-0036 exactly: the injected
predicate and the bounded gate; the replan nudge in `stagnation.py`; the `stagnation_gate` flag (default
OFF); the `stagnation_allows_goal_met` record key with the F35 replay fix; S1–S14; and RED-first tests for
the trap-fired replay case. **Enforcement stays behind the flag** until ADR-0016's measurement exists.
