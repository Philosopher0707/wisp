# PHASE POST-M13 — ADR-0036 AMENDMENT · STAGNATION LATCH SEMANTICS & REOPENING AUTHORITY

**Phase type:** architecture decision only. **No production implementation.**
**Date:** 2026-09-24 · **Method:** the `architecture-evolution` skill system, stage **decision**
(`architecture-decision-engineering`).
**Predecessor:** `PHASE_POST_M13_STAGNATION_REOPENING_RECON.md` — `RECON COMPLETE — ADR AMENDMENT REQUIRED`.

---

## 1. Mission Result

**The stagnation latch is ratified as monotonic.** `trap_fired` is permanent for the detector's lifetime,
`may_report_goal_met()` cannot reopen, and no intervention can restore `GOAL_MET` for a turn in which the
predicate closed.

The decision is **derived, not preferred**: ADR-0036 §6 already ratified non-clearability for
*trap-sourced* stagnation, and the recon proved the trap case is **universal**. So the amendment
**completes** ADR-0036 rather than changing it — widening its §6 scope, correcting its truth table, and
settling the three items the recon found UNDECIDED (`min_consecutive`'s meaning, the predicate's
threshold, and the audit scope).

Two consequences worth stating plainly:

- **`stagnation_gate` can withhold `done` and nudge, but it cannot change the goal state.** That is now
  ratified, so the flag will not be read as doing more than it does.
- **The `min_consecutive` reading is forced, not chosen.** Any predicate depending on `consecutive_flat`
  alone is non-monotonic — it reopens when progress resets the counter — so monotonicity and
  *"`min_consecutive` gates the predicate"* are mutually exclusive. Monotonicity therefore forces the
  verdict-only reading.

```
DECISION_STATE: RATIFIED   ·   ADR AMENDMENT: ADR-0037   ·   PRODUCTION_CHANGES: 0
```

## 2. Baseline

| Property | Value |
|---|---|
| HEAD | `7c15626` |
| Branch | `main` |
| Working tree | **dirty** — 96 entries; tracked diff 230,220 bytes, **byte-identical** to the recon's end state |
| ADR log | append-only (`WISP_ARCHITECTURE_DECISIONS.md:3-9`): *"Decisions are never edited — a superseded decision is marked `SUPERSEDED by ADR-NNNN`"*. Last entry ADR-0036 (line 1645); index ended at line 2153 → **next is ADR-0037** |
| Config defaults | `graph_oscillation_guard=True`, `stagnation_gate=False`, `goal_state=False`, `record_verdict=False`, `recovery_ladder=False`; `max_iterations=50`, `turn_timeout=1800` |

Snapshot: `.workbuddy-ai/memory/post-m13-amendment/`.

## 3. Contract Reconciliation

Every mechanism the question touches, with its current meaning, its ratified requirement, and its class.

| Mechanism | Current meaning | Ratified requirement | Class |
|---|---|---|---|
| `trap_fired` | monotonic per-turn latch = `bool(trap_verdicts)` | ADR-0036 §6: a *trap-sourced* stagnation "cannot be cleared by any intervention", "bounded and conservative". **Scope** was not stated | **DECIDED** for the trap case; the scope is **DERIVED** |
| `is_progress_from()` | five strict improvements; a new action or artifact is progress | ADR-0034 §1/§3: the signal is *accumulated*, not differential; a new action is progress | **DECIDED** |
| `consecutive_flat` | resets to 0 on progress | named in **no ADR** | **IMPLEMENTATION DETAIL** |
| `verdict` | `UNKNOWN`/`PROGRESSING`/`STAGNATING`, from `consecutive_flat` | ADR-0034/0036 use it as the descriptive verdict; ADR-0036 §7 records it as the turn-end verdict | **DECIDED** (descriptive) |
| `may_report_goal_met()` | the completion predicate | ADR-0035 row 4 names it as the arbitration input; ADR-0036 §6 names it as the thing not to change without a decision | **input DECIDED; threshold UNDECIDED** |
| `min_consecutive` | 2; gates `verdict` only | named in **no ADR**; its docstring claims it is the false-positive guard requiring a *run* | **UNDECIDED** |
| `stagnation_gate` | bounded replan enforcement, default OFF | ADR-0036 §1–§4 | **DECIDED** |
| `GOAL_STAGNATED` | goal state | ADR-0035 taxonomy + row 4 | **DECIDED** |
| `turn_succeeded` | turn-level completion | ADR-0035 / 13-H5 | **DECIDED** |
| `stagnation_allows_goal_met` | durable predicate snapshot | ADR-0036 §6 / F35 | **DECIDED** |
| the `STAGNATION` event | written when `observe()` returns `STAGNATING` | **no ADR specifies its gate** | **UNDECIDED** |

**Eight DECIDED, one IMPLEMENTATION DETAIL, three UNDECIDED** — and the three undecided items are exactly
what this amendment settles. Nothing was invented to fill a gap.

## 4. Architectural Conflict

```
FACT A  A repeated observation fires `trap_fired`.                      (source + probe)
FACT B  Later genuine progress IS detected by `is_progress_from()`.     (probe: True; flat resets to 0)
FACT C  `trap_fired` remains true.                                      (append-only, AST scan)
FACT D  `may_report_goal_met()` gives precedence to `trap_fired`.       (stagnation.py:303-305)
FACT E  ADR-0036 models a possible clearable flat-run path.             (its truth table)
FACT F  That path is unreachable under the live signal construction.    (recon, §5)
```

```
CLASSIFICATION: C — incomplete ADR
```

| Option | Verdict | Evidence |
|---|---|---|
| A — deliberate safety invariant | **partly**, but incomplete | ADR-0036 §6 ratified non-clearability, but scoped it to *"a stagnation that came from the trap"*; it never states that this is the only case |
| B — accidental implementation consequence | **no** | the behaviour follows from ADR-0034 decision 3 (*"accumulated rather than differential"*), so it is derived from a ratified decision, not accidental |
| **C — incomplete ADR** | **selected** | ADR-0036's truth table contradicts its own §6 clause; no ADR states the effective threshold; three items are UNDECIDED |
| D — conflicting architectural contracts | **no** | ADR-0034, ADR-0035 and ADR-0036 do not contradict each other — only ADR-0036's internal table is over-broad |
| E — other | **no** | the evidence is sufficient; only the *meaning* was unstated |

## 5. Option Analysis

Factual status per option. **No ranking, and no evaluative language.**

### Option A — M13 owns reopening
- **OPTION STATUS:** CONTRACT CHANGE REQUIRED — overrides ADR-0036 §6's ratified clause.
- **Evidence available:** `is_progress_from()` — but it returns true on *novelty* (a new action or a new
  artifact), so reopening on it means a single new tool call clears the latch, which makes the latch
  near-vacuous.
- **A stricter rule** (*"verified recovery"*) would need verification or P3 data the detector does not
  have: `core/stagnation.py` imports only `dataclasses`, `enum`, `typing`, `wisp.core.graph.loop` and
  `wisp.core.recovery` — **no `wisp.core.verification`** — and reads no guard state (import-graph check).
  Its `criteria_satisfied` / `failing_criteria` / `completed_nodes` fields are settable only by
  `from_verdict_and_graph`, the opt-in source ADR-0034 removed.
- **IMPLEMENTATION COMPLEXITY:** high — either accept novelty, or reverse ADR-0034's central decision.
- **REPLAY IMPACT:** a new decision about recording the reopen transition.
- **AUTHORITY IMPACT:** M13's remit changes from *"is progress stalled"* to *"has recovery occurred"*.
- **Critical question answered:** it does turn M13 into a recovery-state machine, because it would have to
  decide *whether recovery happened*, which is a different question from *whether progress stalled*.

### Option B — the runtime owns reopening
- **OPTION STATUS:** CONTRACT CONFLICT — ADR-0036 §3/§6: the engine receives a read-only predicate, and the
  detector is the single stagnation authority.
- **IMPLEMENTATION COMPLEXITY:** medium — the runtime already holds the detector and the intervention count.
- **REPLAY IMPACT:** a new decision about what the runtime records.
- **AUTHORITY IMPACT:** two components hold opinions about one question.
- **Critical question answered:** yes — it recreates the authority ambiguity ADR-0035/0036 removed, because
  the runtime's derived predicate and M13's own predicate could disagree.

### Option C — recovery owns reopening
- **OPTION STATUS:** CONTRACT CONFLICT — ADR-0035 §5 and ADR-0036's ordering: completion is evaluated
  before recovery, and the ladder runs at the turn boundary and only when `not turn_succeeded`
  (`runtime.py:1149`).
- **IMPLEMENTATION COMPLEXITY:** high — requires moving the recovery consumer, or adding a producer for
  `RECOVERY` records, which today has none.
- **REPLAY IMPACT:** requires a recovery record for an in-turn event.
- **AUTHORITY IMPACT:** a detection fact becomes a recovery decision.
- **Critical question answered:** *"stagnation ended"* is a **detection** fact — it is a statement about
  the progress signal, which the detector owns — not a recovery decision, which is *"what should happen
  next"*.

### Option D — no reopening (monotonic)
- **OPTION STATUS:** SUPPORTED BY CURRENT CONTRACT — ADR-0036 §6 already ratifies non-clearability for the
  trap case, and the recon proved the trap case is universal.
- **IMPLEMENTATION COMPLEXITY:** none for behaviour; documentation only.
- **REPLAY IMPACT:** none — the predicate at turn end is already recorded.
- **AUTHORITY IMPACT:** none — no new owner and no new transition; the one internal competition is resolved
  explicitly.
- **Critical question answered:** yes — monotonicity is the safety property ADR-0035 row 4 was adopted
  for, and the recon's classification (D — valid but incomplete architecture) says the behaviour is
  intended while the *account* of it was incomplete.

## 6. Decision Matrix

| Dimension | A: M13 | B: Runtime | C: Recovery | D: Monotonic |
|---|---|---|---|---|
| Existing authority preserved? | no — M13's remit changes | no — the runtime gains an opinion on stagnation | no — recovery gains a detection fact | **yes** |
| New authority introduced? | no new owner, but a new transition | yes — a second opinion on stagnation | yes — recovery decides a detection fact | **no** |
| ADR-0035 compatible? | yes — row 4 names the predicate's *value* | yes | no — violates the completion-before-recovery ordering | **yes** |
| ADR-0036 compatible? | no — overrides §6 | no — conflicts with §3's predicate-only seam | no — conflicts with the ordering | **yes — completes §6** |
| ADR-0034 compatible? | no — needs the opt-in signal source, or accepts novelty | n/a | n/a | **yes — load-bearing for it** |
| Replay impact | a decision on recording the reopen | a decision on what the runtime records | requires a recovery record | **none** |
| Persistence impact | possibly a new field or event | possibly a new field | requires a `RECOVERY` producer | **none** |
| Boundedness | bounded by the intervention budget (2/turn) | bounded | bounded | **bounded — no cycle exists to bound** |
| M13 semantic impact | remit becomes recovery-shaped | M13 unchanged | M13 unchanged | **M13 unchanged; the internal competition is resolved** |
| `min_consecutive` impact | must gate the predicate → non-monotonic | unaffected | unaffected | **verdict-only — derived, not chosen** |
| Oscillation impact | introduces OPEN→STAGNATING→REPLAN→OPEN (bounded at 2) | same | same | **no cycle** |
| Audit impact | needs a reopen record | needs a decision | needs a recovery record | **none — the predicate is already durable** |
| Production changes required | predicate change, possibly signal wiring | runtime derivation + recording | consumer move + `RECOVERY` producer | **documentation only** |

## 7. `min_consecutive` Decision

**Ratified: Interpretation A — `min_consecutive` is the VERDICT threshold only. It does not gate the
predicate.**

This is **derived**, not selected. Consider what a predicate that *did* respect it would have to be:

```python
# Interpretation B, made concrete:
return self.consecutive_flat < self.min_consecutive
```

With that rule and `min_consecutive = 2`: after two identical observations `consecutive_flat == 1` and the
predicate is **open**; after a third it closes; and after genuine progress resets the counter to 0 it is
**open again**. So Interpretation B *is a reopening design* — it makes the predicate non-monotonic by
construction. **Monotonicity (Option D) and Interpretation B are mutually exclusive.**

The amendment therefore ratifies A, and records the consequence: the module docstring's claim that
*"N consecutive non-progressing evaluations are required before stagnation is declared"* is accurate for
the **verdict** and must be read as scoped to it. That wording is corrected in a future documentation-only
phase (§13).

**Not implemented in this phase.**

## 8. Audit Decision

**Ratified: Interpretation A with a stated limitation.** The `STAGNATION` event records the **verdict**;
the **predicate** is recorded in the goal record.

| Fact | Recorded where | Authoritative for |
|---|---|---|
| the verdict (`UNKNOWN`/`PROGRESSING`/`STAGNATING`) | `STAGNATION` event, gated on the verdict | explaining the detector's own conclusion |
| the predicate the arbiter consumed | `GOAL_STATE.stagnation_allows_goal_met` | **replay and the goal state** |

Two records with two meanings; neither substitutes for the other, and no new event kind is introduced.

**The limitation, stated rather than hidden:** when the trap closes the predicate below the threshold, the
turn records **zero** `STAGNATION` events (measured in the recon), so the *observations* that closed the
predicate are not durable. The predicate itself is. Accepted because ADR-0035's replay contract requires
the decision to be **reproducible**, not the evidence to be **explainable**, and because the goal record
always carries the predicate. **Revisit condition:** enforcement enabled by default, or an operator needing
to explain a `GOAL_STAGNATED` from the journal alone.

Interpretation B (make predicate closure an authoritative audit event) and C (a distinct trap event) were
both available and are recorded as rejected in §14 — both add persistence for explainability, which the
replay contract does not require.

## 9. Replay Contract

| Question | Answer |
|---|---|
| What proves reopening? | **N/A** — reopening does not occur |
| What proves the latch was previously closed? | `stagnation_allows_goal_met == False` in the goal record, with `stagnation_verdict` as the readable form |
| Can replay derive the same predicate? | **Yes** — the record carries the predicate the arbiter consumed, from the same computation (ADR-0036 §6 / F35) |
| Does replay need an intervention count? | **No** (ADR-0036 §7, re-ratified) |
| Does replay need a replan event? | **No** — the nudge is already a `[SYSTEM]` transcript message and is not an authority input |
| Does replay need the progress evidence? | **No** — the predicate is the input; the observations explain it |
| Does `stagnation_allows_goal_met` remain sufficient? | **Yes** |
| Under Option D, is new replay state required? | **No** — monotonicity **removes** a transition rather than adding one, so the durable surface does not change at all |

```text
reconstruct(terminal_outcome, acceptance_verdict, stagnation_allows_goal_met, turn_succeeded)
    == the live goal state
```

`LIVE_REPLAY_EQUIVALENCE: PROVEN`, given the record — and conditional on `goal_state`, which defaults OFF
(an unchanged, pre-existing property).

## 10. Boundedness Contract

| Question | Answer |
|---|---|
| How many reopenings may occur per turn? | **0** — the predicate cannot reopen |
| How many interventions? | **2** per turn (`_MAX_STAGNATION_INTERVENTIONS`), engine-local, reset per turn |
| What stops oscillation? | the latch — with no reopen there is no `OPEN → STAGNATING → REPLAN → OPEN` cycle to bound |
| What happens on the final iteration? | withholding is forbidden (`iteration + 1 < max_iterations`), so the surrender is honest rather than a budget failure |
| What happens when time expires? | the timeout pre-empts the loop; `GOAL_FAILED`, reason `timeout` |
| What happens after intervention budget exhaustion? | surrender → `done` → `GOAL_STAGNATED` with `turn_succeeded = True` |

**No new budget is created.** The five existing bounds remain distinct, and the intervention budget stays
the binding constraint on the gate. Monotonicity removes a potential cycle; it does not require a new
counter.

## 11. Final Decision

```text
IF:      trap_fired == true
THEN:    may_report_goal_met() remains false until the detector's lifetime ends

PROGRESS AFTER THE TRAP:  does not reopen the predicate, even when `is_progress_from()` is true
                          and `consecutive_flat` resets to 0
BOUNDARY:                 a new turn constructs a new detector (runtime.py:731) — per turn, never
                          per session, never global
REPLAN:                   may continue execution and may improve the work, but cannot restore
                          GOAL_MET for that turn
AUTHORITY:                M13 owns the predicate, and therefore owns this transition — by not making it
EVIDENCE:                 none required, because no transition occurs
PERSISTENCE:              unchanged — `stagnation_allows_goal_met` in the goal record
REPLAY:                   unchanged — the recorded predicate is row 4's input
MIN_CONSECUTIVE:          the VERDICT threshold; it does not gate the predicate
AUDIT:                    `STAGNATION` records the verdict; the goal record records the predicate
```

**Reversal condition.** Reversed only by a superseding ADR naming a reopening **owner**, an **evidence
rule** the detector can observe without importing the verification or acceptance authorities, and a
**replay contract** for the transition. Trigger to revisit: enforcement enabled by default, or a measured
rate of `GOAL_STAGNATED` on turns that later produced verified work — evidence that the monotonic
predicate produces false stagnation rather than conservative under-claiming.

## 12. ADR Amendment Reference

**ADR-0037** — *"The stagnation latch is monotonic; the predicate closes on the trap, and
`min_consecutive` gates the verdict"*, appended to `WISP_ARCHITECTURE_DECISIONS.md` (heading line **2114**;
index row line **2395**; 241 lines). It **amends ADR-0036** by completing it, and **supersedes nothing**.

`CONTEXT.md:45` updated to `ADR-0001 … ADR-0037`.

The amendment's required sections are all present: Context · Decision · Authority · State transition ·
Bounds · Replay · Audit · Interaction with existing ADRs · Rejected alternatives · Implementation
boundary · Reversal condition.

## 13. Implementation Boundary

**Authorised in a future implementation phase — documentation only:**

1. `core/stagnation.py` — **docstrings only, no behaviour**: state in `may_report_goal_met()` that it is
   `False` from the first flat observation and is monotonic for the detector's lifetime; scope the module
   docstring's *"N consecutive"* mitigation to the verdict; align `trap_fired`'s *"not sufficient on its
   own"* with the predicate's actual rule.
2. `AGENTS.md` — its completion-gate section already states the gate cannot change the goal state; verify
   the wording matches ADR-0037.

**Forbidden without a superseding ADR:** clearing or truncating `trap_fired`; changing
`may_report_goal_met()`'s rule; changing `is_progress_from()`; changing `OscillationTrap`; making
`min_consecutive` gate the predicate; adding a reopening mechanism, a second detector or a second
completion predicate; adding persistence or an event kind for reopening; changing ADR-0035's precedence;
changing recovery semantics; changing `turn_succeeded`; enabling `stagnation_gate` by default; touching
F8, the graph or fanout.

## 14. Rejected Alternatives

| Alternative | Factual reason |
|---|---|
| **Option A — M13 owns reopening** | Requires overriding ADR-0036 §6's ratified clause. Reopening on `is_progress_from` reopens on *novelty*; a stricter rule needs data the detector does not import and cannot read, i.e. reversing ADR-0034. It also changes M13's remit to a recovery-shaped one |
| **Option B — the runtime owns reopening** | Conflicts with ADR-0036 §3: the engine receives only a read-only predicate; a runtime-derived predicate makes the runtime a second opinion on stagnation |
| **Option C — recovery owns reopening** | Conflicts with ADR-0035 §5 and ADR-0036's ordering: completion precedes recovery, and the ladder runs at the turn boundary on failed turns only. It would also make a detection fact into a recovery decision |
| **Make `min_consecutive` gate the predicate** | Not available alongside monotonicity — such a predicate reopens when progress resets the counter, so it *is* a reopening design |
| **Record the observations in the trap-closed case** | Adds persistence for explainability, which the replay contract does not require; recorded as a limitation with a revisit condition instead |
| **Do nothing** | Leaves a ratified ADR asserting an unreachable row and leaves the effective detection threshold undocumented — the two facts that made this phase necessary |

## 15. Verification

| Claim | Method | Result |
|---|---|---|
| No production code, config, or test changed **by this phase** | the tracked `git diff` compared byte-for-byte against the pre-phase snapshot | **byte-identical — 230,220 bytes both ways** |
| The protected surfaces are untouched | per-file `numstat` + the byte-identical diff | `stagnation.py`, `graph/loop.py`, `stateless.py`, `runtime.py`, `goal.py`, `verification.py`, `recovery.py`, `config.py` — no change from this phase |
| No test weakened | `tests/` status inspected | entries exist but are **pre-existing WIP** from earlier phases; the byte-identical diff proves this phase added none |
| No unrelated WIP reverted | no `git stash` / `reset --hard` / `checkout --` run at any point | the tree was read and two docs edited |
| The ADR log convention was determined, not assumed | read from the log itself | append-only, `SUPERSEDED by ADR-NNNN`; ADR-0037 appended after ADR-0036 |
| The amendment is complete | structure check | 241 lines; 11 required headings; 6 code fences (balanced); 40 table rows |
| ADR-0035 is not modified | diff + index inspection | ADR-0035's text is untouched; **no `ADR-0035 AMENDMENT REQUIRED` condition arose** — see below |
| Files changed by this phase | `git status` / `git diff --stat` | `WISP_ARCHITECTURE_DECISIONS.md` (ADR-0037 + index row), `CONTEXT.md` (ADR range), and the new report |

**ADR-0035 compatibility, stated precisely (per §14 of the brief).** The decision remains within
ADR-0035's model, so no ADR-0035 amendment is required, because row 4 names
`may_report_goal_met()`'s **value** as its arbitration input — not its history. A monotonic predicate
therefore satisfies row 4 exactly: the row simply never fires again in that turn. ADR-0035's precedence
(row 4 over row 6) is untouched, and its rationale — *"a stagnated goal must never reach `GOAL_MET`"* — is
**strengthened** by monotonicity rather than weakened.

### Discovered, reported, not fixed (scope discipline)

`CONTEXT.md` carries **two ADR ranges**, and §17B requires the ADR index to be updated — so both were
corrected to `ADR-0001 … ADR-0037`:

| Line | Was | Now |
|---|---|---|
| 45 | `ADR-0001 … ADR-0036` | `ADR-0001 … ADR-0037` |
| 972 | **`ADR-0001 … ADR-0028`** — stale by nine entries, missed by the phases that added ADR-0029…0036 | `ADR-0001 … ADR-0037` |

The same table carries a **different** staleness that this phase did **not** fix, because it is not part of
the ADR indexing convention and §17B scopes `CONTEXT.md` edits to that convention:

| Line | Says | Reality |
|---|---|---|
| 44 | findings `F1–F34` | the ledger's last finding is **F36** |
| 971 | findings `F1–F24` | the same — and the two lines disagree with each other |

**Recorded here rather than corrected**, so the decision to fix it stays explicit. It is a one-line change
in each place if the next phase wants it.

## 16. Status Log

| Checkpoint | Result |
|---|---|
| `STATUS START` | baseline recorded; append-only convention confirmed from the log; next ADR = 0037 |
| `STATUS CONTRACT` | reconciliation complete: 8 DECIDED, 1 IMPLEMENTATION DETAIL, **3 UNDECIDED** |
| `STATUS CONFLICT` | **C — incomplete ADR** (not A alone, not B, not D, not E) |
| `STATUS OPTIONS` | four options evaluated with factual status labels; no ranking |
| `STATUS AUTHORITY` | `REOPENING_AUTHORITY: NONE`; the one internal competition resolved explicitly |
| `STATUS REPLAY` | `PROVEN`; `stagnation_allows_goal_met` sufficient; no new durable fact |
| `STATUS BOUNDEDNESS` | bounded; no new budget; the latch removes a cycle |
| `STATUS DECISION` | `RATIFIED` — Option D; `min_consecutive` verdict-only; audit = verdict vs predicate |
| `STATUS COMPLETE` | see §17 |

**No `STATUS CONTRADICTION` and no `STATUS BLOCKED` was emitted.** None of §19's six stop conditions
arose: ADR-0035 need not change; the source did not contradict the recon; the ADR convention was
determined; no two authorities remained incompatible; replay is deterministic; and the decision required
no production change.

## 17. Final Status

```
=== STATUS: COMPLETE ===
PHASE: POST-M13-ADR-0036-AMENDMENT
MODE: ARCHITECTURE-ONLY
PRODUCTION_CHANGES: 0
DECISION_STATE: RATIFIED
LATCH_SEMANTICS: MONOTONIC
REOPENING_AUTHORITY: NONE
REOPENING_EVIDENCE: N/A — no transition occurs; the only exit from a closed predicate is the end of the
                    detector's lifetime (a new turn constructs a new detector, runtime.py:731)
MIN_CONSECUTIVE_SEMANTICS: the VERDICT threshold only (gates `observe()`'s return value and `verdict`);
                    it does NOT gate `may_report_goal_met()`. Derived, not chosen: a predicate depending on
                    `consecutive_flat` alone reopens on progress, so it is incompatible with monotonicity
AUDIT_SEMANTICS: the `STAGNATION` event records the VERDICT; the goal record records the PREDICATE
                    (`stagnation_allows_goal_met`). No new event kind, no new persistence. Known accepted
                    limitation: a trap-closed turn records the predicate but not the observations
REPLAY_STATUS: unchanged — the recorded predicate is row 4's input; `stagnation_allows_goal_met` remains
                    sufficient; no new durable fact is required (monotonicity removes a transition)
BOUNDEDNESS_STATUS: bounded — 0 reopenings per turn; 2 interventions per turn; the latch removes the
                    OPEN->STAGNATING->REPLAN->OPEN cycle; no new budget created
ADR_AMENDMENT: ADR-0037 (amends ADR-0036 by completing it; supersedes nothing)
IMPLEMENTATION_STATUS: NOT_IMPLEMENTED
NEXT: a documentation-only phase is authorised (docstrings in core/stagnation.py stating the monotonic
      semantics and scoping the "N consecutive" claim to the verdict; verify AGENTS.md's wording).
      No behavioural implementation is authorised or required. Enforcement stays behind `stagnation_gate`
      (default OFF) until ADR-0016's measurement exists.
=============================
```
