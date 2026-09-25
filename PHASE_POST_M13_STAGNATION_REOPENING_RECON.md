# PHASE POST-M13 — STAGNATION PREDICATE REOPENING FORENSIC RECON

**Phase type:** read-only forensic recon + architecture analysis.
**Date:** 2026-09-24 · **Method:** the `architecture-evolution` skill system, stage **recon**
(`architecture-forensics`).
**Question (from §19 of the brief):** *if a bounded replan produces genuine progress after stagnation,
should M13's completion predicate be able to recognise that recovery — and if so, who owns that
transition, what evidence proves it, and how is it replayed deterministically?*

---

## 1. Mission Result

**The latch is real, and stronger than the previous report claimed.** On the live path the predicate
closes at the **first flat observation** — not at `min_consecutive`, which gates `verdict` only. So
`may_report_goal_met()` returning `False` is equivalent to `trap_fired`, and `trap_fired` is a one-way
latch with **no clearing path inside a turn**. `REOPEN_POSSIBLE: NO`.

Three contradictions between source and its own documentation were found, one of them against a ratified
ADR (ADR-0036's truth table asserts a reachable row that has no reachable configuration). Every ratified
clause of ADR-0035 and ADR-0036 nevertheless holds, so nothing is broken — the architecture's account of
itself is incomplete, not violated.

**The architectural question is therefore real and unanswered**, and it is narrow: the evidence that a
replan produced progress is **already computed** (`is_progress_from()` returns `True`; measured). The only
reason it does not count is that `trap_fired` overrides it. So reopening requires **no new evidence
mechanism** — it requires a decision about precedence between two facts the detector already holds.

```
DECISION_STATE: ADR_AMENDMENT_REQUIRED
```

## 2. Baseline

| Property | Value |
|---|---|
| HEAD | `7c15626` — `docs(m11): record the M11 phase; findings F29-F31; repair the commit table` |
| Branch | `main` |
| Working tree | **dirty** — 95 entries; the tracked diff is 230,220 bytes and is **byte-identical** to the end of the previous phase. Pre-existing WIP treated as immutable |
| `graph_oscillation_guard` | **`True`** (the detector is enabled) |
| `stagnation_gate` | **`False`** |
| `goal_state` | **`False`** |
| `record_verdict` | **`False`** |
| `recovery_ladder` | `False` |
| `max_iterations` | 50 |
| `turn_timeout` | 1800 s |
| Detector defaults | `enabled=True`, `min_consecutive=2` |

Snapshot: `.workbuddy-ai/memory/post-m13-reopen/` (`head.txt`, `branch.txt`, `status-before.txt`,
`diff-before.patch`, `probe.py`).

**The shipped configuration is the interesting one**: `graph_oscillation_guard=True` means the detector
runs on every turn; `goal_state=False` means nothing is recorded by default; `stagnation_gate=False` means
the intervention never runs. So the predicate is computed on every turn and consumed by a derivation that
is itself off by default.

## 3. Source Trace

Every edge, with producer, consumer, authority and persistence. Verified in source, not from comments.

| # | Edge | Source | Producer | Consumer | Authority | Persisted? |
|---|---|---|---|---|---|---|
| 1 | observation | `runtime.py:838-869` — per `tool_result`, fold `with_work(action_key, diff_hash(result))` | the runtime | the detector | none (pure data) | no |
| 2 | progress classification | `stagnation.py:161-179` `is_progress_from()` | the signal | `observe()` | none | no |
| 3 | state digest | `stagnation.py:316-328` `_state_digest()` | the signal | the trap | none | no |
| 4 | trap | `stagnation.py:248` `trap.observe(diff_hash(digest))` → `graph/loop.py:118-125` | the trap | `observe()` | none | no |
| 5 | `trap_fired` | `stagnation.py:280-288` = `bool(trap_verdicts)` | the trap | the predicate | none | **no** |
| 6 | verdict | `stagnation.py:264-278` `verdict` property | the detector | the goal record; the audit gate | descriptive | yes (goal record + STAGNATION event) |
| 7 | predicate | `stagnation.py:294-305` `may_report_goal_met()` | the detector | the closure; the arbiter | **the detection authority's output** | yes (`stagnation_allows_goal_met`) |
| 8 | closure | `runtime.py:764-770` — `lambda: bool(detector.may_report_goal_met())` | the runtime | the engine | none | no |
| 9 | gate | `stateless.py:832-863` — bounded withhold + replan nudge | the engine | the loop | enforcement (behind a flag) | the nudge, as a `[SYSTEM]` message |
| 10 | next iteration | `stateless.py:860` `continue` | the engine | the provider | none | no |

**Two facts that matter and were verified rather than assumed:**

- `runtime.py:864` is the **only** production `observe()` call site in `wisp/`. `loop.py:181` is the
  disowned Layer-C `ExecutionGraph` (no production callers); `stagnation.py:248` is the detector's own
  trap feed.
- The detector is constructed at `runtime.py:731`, **once per turn**, and `stagnation_signal` is a fresh
  `ProgressSignal()` at `:733`. So the latch's lifetime is **one turn**.

## 4. M13 State Model

The five named concepts are **not** five states. Two are real states, one is a counter, one is a latch, and
one is derived:

| Concept | What it actually is | Where |
|---|---|---|
| `OPEN` / closed | **derived** — the return value of `may_report_goal_met()`. Not stored | `stagnation.py:294-305` |
| `PROGRESSING` | a **value of `verdict`**, derived from `consecutive_flat` | `:276-278` |
| `FLAT` | **not a state** — a per-observation predicate (`not is_progress_from`) | `:161-179` |
| `STAGNATING` | a **value of `verdict`**, derived: `consecutive_flat >= min_consecutive` | `:276-278` |
| `TRAP_FIRED` | a **monotonic latch**: `bool(trap_verdicts)`, appended, never cleared | `:288`, `:250` |

The implemented machine, on the live path:

```
observation (a new tool_result)
        |
        v
  digest == previous digest ?
     |                     |
    yes                    no            <-- on the live path, "no" <=> genuine growth in a or w
     |                     |
     v                     v
  consecutive_flat += 1   consecutive_flat = 0        (PROGRESS)
  trap.observe(...) -> 'repeat'
  trap_verdicts.append    (LATCH, monotonic)
     |                     |
     v                     v
  predicate = False      predicate = not trap_fired  -> still False if the latch has ever fired
```

**Terminal/monotonic transitions:** `trap_verdicts` is append-only (AST scan of `stagnation.py` and
`graph/loop.py`: the only assignments are the field default at `:210` and `_hashes` at `loop.py:116`; the
only mutation is `append` at `:250` and `loop.py:120`). No reset, clear or truncate exists anywhere.
`consecutive_flat` is **not** monotonic — it resets to 0 on progress.

## 5. Latch Proof

Each step of the previous report's claim was re-verified independently. One step was **strengthened**.

| # | Question | Answer | Evidence |
|---|---|---|---|
| **A** | What constitutes the digest? | `"c=<n>\|f=<set>\|a=<set>\|w=<set>\|n=<c>/<t>"` — five fields | `stagnation.py:316-328`; probe printed `c=0\|f=\|a=\|w=\|n=0/0` for the empty signal |
| **B** | What inputs contribute? | `criteria_satisfied`, `failing_criteria`, `artifact_hashes`, `work_units`, `completed_nodes`/`total_nodes` | `_state_digest` |
| **C** | Can they change after an intervention? | **Yes** — a new tool call adds a `work_unit` and possibly a new outcome hash. The intervention itself changes nothing | `runtime.py:861-863` |
| **D** | What counts as progress? | five strict improvements; a merely *changed* set is not progress | `stagnation.py:161-179`; probe: identical→False, new action→True |
| **E** | What fires the trap? | last two hashes equal → `"repeat"`; last == third-from-last → `"cycle"` | `loop.py:118-125`; probe: `observe(digest2) -> 'repeat'` |
| **F** | Is `trap_fired` latched? | **Yes, monotonically** | `:288` = `bool(trap_verdicts)`; append-only (AST scan) |
| **G** | Any production path that clears it? | **None.** The only reset is the **turn boundary** — a new turn constructs a new detector (`runtime.py:731`) | AST scan; `grep` for reset/clear across both modules |
| **H** | Can `consecutive_flat` reset independently? | **Yes** — measured 1 → 0 on genuine progress | probe: obs3 `flat=0 trap=True` |
| **I** | Can the predicate reopen with `consecutive_flat == 0`? | **No** — `may_report_goal_met()` falls through to `not self.trap_fired` | `:303-305`; probe: `may_met=False` with `flat=0` |
| **J** | Does a successful replan create evidence that *should* reopen? | **The evidence is already created.** `is_progress_from()` returns `True` and `consecutive_flat` resets. What is missing is a rule, not a signal | probe; `:255-257` |

**The strengthening.** The previous report said the predicate closes when
`consecutive_flat >= min_consecutive` **or** the trap fires, and treated the trap case as *a* latch. The
implementation's own test asserted the trap fires with `consecutive_flat < min_consecutive`. Measured
across `min_consecutive ∈ {1, 2, 3, 5, 50}`:

```
min_consecutive= 1: after ONE flat observation -> flat=1 trap=True  verdict=stagnating  may_met=False
min_consecutive= 2: after ONE flat observation -> flat=1 trap=True  verdict=progressing may_met=False
min_consecutive= 3: after ONE flat observation -> flat=1 trap=True  verdict=progressing may_met=False
min_consecutive= 5: after ONE flat observation -> flat=1 trap=True  verdict=progressing may_met=False
min_consecutive=50: after ONE flat observation -> flat=1 trap=True  verdict=progressing may_met=False
```

**So the predicate closes at one flat observation regardless of `min_consecutive`**, and
`min_consecutive` gates `verdict` only. The latch is not one case among two — it is the only case.

**Why flat implies a repeated digest (the load-bearing step).** On the live path the signal is built
*only* by `with_work`, which preserves `c`, `f` and `n` and unions `a` and `w`. `from_verdict_and_graph`
— the only constructor that can set `failing_criteria` — has **zero call sites in `wisp/`** (AST scan).
So the digest's only variable fields are `a` and `w`, both monotone-growing sets, and any growth in either
is progress by `is_progress_from`. Therefore:

```
flat  <=>  a and w unchanged  <=>  digest unchanged  <=>  the trap fires
```

which makes `predicate closed <=> trap_fired`.

**Verified on the live path**, instrumenting `observe` (diagnostic wrapper, no production file changed):

```
=== 2 identical read rounds ===
  observe -> digest=c=0|f=|a=496b451e... a=1 w=['57b58b58...'] | flat=0 trap=False verdict=progressing ret=unknown
  observe -> digest=c=0|f=|a=496b451e... a=1 w=['57b58b58...'] | flat=1 trap=True  verdict=progressing ret=unknown
  -> audit events=0  verdict='progressing'  allows=False  goal='goal_stagnated'

=== 3 identical read rounds ===
  observe -> ... flat=1 trap=True  verdict=progressing ret=unknown
  observe -> ... flat=2 trap=True  verdict=stagnating  ret=stagnating
  -> audit events=1  verdict='stagnating'  allows=False  goal='goal_stagnated'
```

Note the first case: **the predicate closes and the goal is `GOAL_STAGNATED` while zero audit records
exist.** The digest is stable across calls (`a=1`, one work unit), so the repeated read is genuinely flat.

## 6. Detection vs Completion vs Recovery

Three different questions, three different owners. They are **not** one state and must not be merged.

| Question | Mechanism that answers it | Owner | State it produces |
|---|---|---|---|
| **A — Detection:** is the system currently showing stagnation? | `StagnationDetector.verdict` (`:264-278`) | M13 / `core/stagnation.py` | `UNKNOWN` / `PROGRESSING` / `STAGNATING` |
| **A′ — the *predicate* form of the same signal** | `may_report_goal_met()` (`:294-305`) | M13 | a bool, **not** the verdict |
| **B — Completion:** may the current turn report success? | `VerificationFloorGuard.rejection()` → `turn_succeeded` → `derive_goal_state()` | the engine, then the runtime, then the goal arbiter | `GOAL_*` |
| **C — Recovery:** should the system change strategy? | `RecoveryLadder.decide()` / `route_to_recovery()` | `core/recovery.py`, at the turn boundary | a `RecoveryDecision` rung |

**The finding this separation produces.** Question A has **two answers** in the same object — the verdict
and the predicate — and they disagree exactly in the trap-fired case (measured: `verdict='progressing'`,
predicate `False`). Question B consumes the *predicate*. So "detection" as a user would understand it
(the verdict) and "detection" as the completion authority sees it (the predicate) are different, and the
predicate is strictly stricter.

## 7. Reopening Evidence Analysis

The brief asks which evidence categories already exist. Almost all of them do — and the decisive one is
already computed.

| Evidence category | Exists? | Where | In the progress signal? |
|---|---|---|---|
| new artifact (outcome hash) | **yes** | `artifact_hashes`, `stagnation.py:155` | **yes** |
| new tool action | **yes** | `work_units`, `:158` | **yes** |
| new state digest | **yes** | `_state_digest`, `:316-328` | **yes** |
| progress signal | **yes** | `is_progress_from`, `:161-179` | **yes — and it returns True on recovery** |
| new file mutation | yes, but separate | `runtime._touched_files`, `:474`/`:1496` | **no** |
| successful verification | yes, but separate | `VerificationFloorGuard.verify_ok_after_edit` | **no** |
| new acceptance / goal evidence | yes, but separate | `core/acceptance.py` criteria + evidence | **no** |

**Conclusion: no new evidence mechanism is required.** The detector already observes recovery
(`is_progress_from` returns `True`, `consecutive_flat` resets to 0 — both measured). The evidence is not
missing; the *rule that would honour it* is missing. Reopening is therefore a **precedence question
between two existing facts** — `is_progress_from` and `trap_fired` — not a data-collection question.

This matters for scoping: an implementation that "adds reopening" would not need new observation, new
persistence, or a new subsystem. It would need a rule about which of two existing facts wins.

## 8. Oscillation Analysis

```
REOPEN_LOOP_POSSIBLE: NO — the predicate cannot reopen, so the cycle cannot occur today.
                      Were it made reopenable, the existing intervention budget (2/turn) would cap the
                      cycles at 2, so it would still be bounded.
```

The distinct budgets, **not** collapsed into one number:

| Budget | Value | Owner | Live? | Gates the predicate? |
|---|---|---|---|---|
| detection | `min_consecutive` = 2 | the detector | yes | **no** — gates `verdict` only |
| intervention | `_MAX_STAGNATION_INTERVENTIONS` = 2 | the engine, per turn | yes | n/a — gates the gate |
| iteration | `max_iterations` = 50 | the engine loop | yes | n/a |
| time | `turn_timeout` = 1800 s | `asyncio.timeout` | yes | n/a |
| recovery | `RecoveryBudget` (`local_replans`, `global_replans`, `diagnostic_tasks`) via `BudgetGovernor` | `RecoveryLadder` | **no** — consulted only when `not turn_succeeded`, and `recovery_ladder` defaults OFF | no |
| provider | stream-attempt budget | `provider_stream.py` | yes | no — unrelated |
| goal / run | **none** | — | **no** — `BudgetGovernor` appears only in `recovery.py` and one comment in `goal.py` | no |

**Unbounded path: none for this question.** The intervention budget caps the replan count independently of
the predicate, so even a reopenable predicate could not loop without bound. The only conditional risk is
that the time budget is the last backstop if the intervention budget were ever raised without also
bounding iterations — a future risk, not a present one.

## 9. Replay / Durability Analysis

| | Facts |
|---|---|
| **LIVE inputs** | terminal outcome; the P3 verdict read from the published guard (`runtime.py:1126-1135`); `may_report_goal_met()` (`:1138-1140`) |
| **RECORDED inputs** | the `GOAL_STATE` record (`runtime.py:1248-1270`): `goal_state`, `terminal_outcome`, `acceptance_verdict`, `stagnation_verdict`, **`stagnation_allows_goal_met`**, `turn_succeeded`, `cancelled`, `escalated`, `failure_code` — behind `goal_state`, which **defaults OFF** |
| **REPLAY inputs** | `terminal_outcome`, `acceptance_verdict`, `not stagnation_allows_goal_met`, `turn_succeeded` |
| **Equivalence** | **PROVEN for the goal state**, given the record — both sides read the same computation (F35's fix) and `derive_goal_state` is pure. **Conditional on the flag**: with `goal_state` OFF nothing is recorded at all |
| **Missing durable facts** | `trap_fired`; the digest; the observations; and — measured — **the STAGNATION audit event in the trap-fired-below-threshold case** |
| **Replay risk** | none for the goal state; a real **auditability** gap (below) |

**The audit gap, measured.** The `STAGNATION` event is gated on `observe()` returning `STAGNATING`
(`runtime.py:1126`), which needs `consecutive_flat >= min_consecutive`. But the predicate closes earlier,
via the trap. So:

| Live case | predicate | `GOAL_STAGNATED`? | STAGNATION audit events |
|---|---|---|---|
| 2 identical rounds (trap, flat below threshold) | closed | **yes** | **0** |
| 3 identical rounds (flat ≥ threshold) | closed | yes | 1 |

The audit trail omits exactly the case that closes the predicate. This is the same *shape* as F35 — the
record and the authority disagreeing about what happened — and it is **not** a replay failure, because the
goal record carries the predicate; it is an observability failure.

**Would reopening require new persistence?** **No new durable fact is required for the goal state**: row 4
consumes the predicate at turn end, and that value is already recorded. Reopening would only raise the
separate question of whether the *reopen event* should be recorded for observability — which is the same
gap as above, not a new one. **Recommendation: do not add persistence for reopening until that question is
decided on replay-authority grounds.**

## 10. Authority Analysis

The protected model is intact: M13 is the sole stagnation authority; the floor guard owns verification;
P3 is a projection; `turn_succeeded` is turn-level terminal evidence; `GoalState` is durable goal state;
the ladder is the recovery mechanism. **No component was found to be competing with another.**

The one competition is **inside** the detector: it holds two answers to "has progress resumed?" —
`is_progress_from()` (via `consecutive_flat`) says yes, `trap_fired` says no — and `may_report_goal_met()`
prefers `trap_fired`.

The four options, described neutrally. **Not ranked; no winner selected.**

### Option A — M13 owns reopening
The detector decides that demonstrated progress clears its own latch.
- *Authority*: keeps the question inside the component that owns stagnation. No new owner.
- *Coupling*: none added; the evidence is already local.
- *Replay*: the predicate at turn end is already recorded, so no new field is required.
- *Testability*: high — a pure predicate change, unit-testable without a runtime.
- *Boundedness*: unaffected; the intervention budget still caps the gate.
- *ADR-0035*: row 4 keeps its shape (`may_report_goal_met()`), but its *meaning* becomes
  non-monotonic — which weakens the "must never reach `GOAL_MET`" safety property it was adopted for.
- *ADR-0036*: makes its flat-run truth-table row reachable; §6's trap clause would need amending.
- *M13 semantics*: `trap_fired`'s docstring already says a repeat is "evidence … but not sufficient on
  its own", which is closer to A than to the current code. This option would align code with that
  docstring.
- *Risk*: the detector would then be the component that decides *when stagnation ends* — i.e. it acquires
  a second, harder decision. That is the largest single change to M13's remit of the four options.

### Option B — the runtime owns reopening; M13 remains the sole detector
The runtime observes that a replan produced progress and presents a reopened predicate to the arbiter.
- *Authority*: M13 keeps detection; the runtime gains a *derived* judgement. Two components then hold
  opinions about the same question, which is the shape this migration exists to remove unless the runtime
  is given the decision explicitly.
- *Coupling*: the runtime already holds the detector and the intervention count, so the data is present.
- *Replay*: needs a decision about what is recorded — the predicate alone, or the predicate plus the
  fact that it was reopened.
- *Testability*: medium — needs the live path, not a unit test.
- *Boundedness*: unaffected.
- *ADR-0035 / ADR-0036*: as A, plus it introduces a second consumer of the detector's internals, which
  ADR-0036 deliberately avoided ("the engine receives only a predicate").

### Option C — a separate recovery mechanism owns reopening
Reopening is treated as a recovery decision, not a detection one.
- *Authority*: matches the existing separation (detection vs recovery) and the ladder already owns
  "what should happen next".
- *Coupling*: the ladder is at the **turn boundary** and runs only on failed turns; an in-turn replan is
  neither. Routing reopening there would require moving the recovery consumer, which ADR-0036's ordering
  explicitly forbids.
- *Replay*: `RECOVERY` records have no production producer today; this option would require adding one.
- *Testability*: medium-high (the ladder is already a state machine with budgets).
- *ADR-0035 / ADR-0036*: conflicts with ADR-0036's ordering (completion before recovery) unless the
  ordering is re-decided.
- *Risk*: makes a detection question into a recovery question, which is a category change.

### Option D — no reopening; stagnation is intentionally monotonic
The latch is declared the intended property and documented as such.
- *Authority*: nothing changes. No new owner, no new decision, no new persistence.
- *Coupling*: none.
- *Replay*: unaffected.
- *Testability*: trivially satisfied — it is what the code already does.
- *ADR-0035 / ADR-0036*: ADR-0036's truth table must be corrected (the flat-run row removed or marked
  unreachable), and §6's clause widened from "trap-sourced" to "all live-path stagnation".
- *M13 semantics*: unchanged.
- *Risk*: the `stagnation_gate` flag remains implemented and enabled-able while being unable to change the
  goal state — so enabling it buys behaviour (extra rounds, two nudges) but no authority. That must be
  documented, or the flag will be read as doing more than it does.

## 11. Conflict Matrix

Two columns: **what happens today** (all answered from source/execution) and **what should happen**
(the architectural question). `UNSPECIFIED` marks the latter where no contract exists.

| Current state | New evidence | What happens today | What *should* happen |
|---|---|---|---|
| STAGNATING | no progress | stays closed; row 4 → `GOAL_STAGNATED` | **UNSPECIFIED — ARCHITECTURE DECISION REQUIRED** |
| STAGNATING | new state digest | `consecutive_flat` → 0; predicate **stays** closed | **UNSPECIFIED** |
| STAGNATING | tool execution | no effect on the predicate by itself | **UNSPECIFIED** |
| STAGNATING | code mutation | no effect on the predicate; `_touched_files` is not fed to the signal | **UNSPECIFIED** |
| STAGNATING | verified mutation | no effect on the predicate; the floor guard is a separate authority | **UNSPECIFIED** |
| STAGNATING | P3 `PASS` | `GOAL_STAGNATED` — row 4 outranks row 6 | **DECIDED** (ADR-0035 row 4) |
| STAGNATING | terminal success | `GOAL_STAGNATED` with `turn_succeeded = True` | **DECIDED** (ADR-0035 line 1478) |
| STAGNATING | replan intervention | withheld `done`, nudge emitted, loop continues; predicate unaffected | **DECIDED** (ADR-0036 §1–§4) |
| TRAP_FIRED | progress | `consecutive_flat` resets; predicate **does not reopen** | **UNSPECIFIED** |
| TRAP_FIRED | new artifact | digest changes; predicate **does not reopen** | **UNSPECIFIED** |
| TRAP_FIRED | new goal evidence | predicate **does not reopen** | **UNSPECIFIED** |
| any | a fresh turn begins | a **new detector**, predicate open | **DECIDED** (per-turn construction) |

**Everything in the "today" column is answered.** The undecided cells are all in the "should" column, and
they are all instances of the same question — which is why the decision is one decision, not eleven.

## 12. ADR-0036 Reachability Analysis

ADR-0036's truth table has two rows for a closed predicate:

| ADR-0036 row | Reachable? | Why |
|---|---|---|
| "closed (flat run) → replan clears it → `GOAL_MET`" | **NO** | flat ⟹ digest repeats ⟹ the trap fires ⟹ the predicate is `not trap_fired`. There is no configuration of the *shipped* path in which the predicate clears |
| "closed (trap latched) → replan cannot clear it → `GOAL_STAGNATED`" | **YES** — and it is the **only** row | measured on the live path |

| Question (§14) | Answer |
|---|---|
| 1. What configuration would the flat-run row require? | A signal whose digest can change **without** progress. The only such field is `failing_criteria` (a changed-but-not-shrunken set), which only `from_verdict_and_graph` can set — i.e. the **pre-M13 (F32) signal source** |
| 2. Is that configuration possible today? | **No** — `from_verdict_and_graph` has **zero call sites in `wisp/`** (AST scan) |
| 3. Is it configurable? | **No** — there is no flag selecting the signal source |
| 4. Does the implementation make the row unreachable? | **Yes**, structurally: the digest's only variable fields are monotone growth sets, and growth is progress |
| 5. Implementation detail or architectural consequence? | **Architectural consequence of ADR-0034.** ADR-0034 moved the signal from "the opt-in records" to "the work observed", which is what made `a`/`w` the only variable fields — and therefore what made flat ⟹ trap. It follows from a ratified decision, not an accident |
| 6. Does ADR-0036 require the row to be reachable? | **No.** Its Decision is about delay-vs-veto, the bound, fail-open and the record. The truth table illustrates consequences; §6 explicitly ratifies the trap-latch row as "bounded and conservative". Nothing asserts the flat-run row must be reachable |
| 7. What does ADR-0036 actually guarantee? | (a) delay, not veto; (b) bound 2 per turn; (c) after the bound, surrender with `turn_succeeded = True` and `GOAL_STAGNATED`; (d) fail-open; (e) the record carries the predicate. **All five hold today.** It does **not** guarantee that an intervention can change the goal state |

## 13. Bug vs Contract Classification

```
CLASSIFICATION: D — valid but incomplete architecture
```

| Option | Verdict | Evidence |
|---|---|---|
| **A — implementation bug** | **No** | every ratified clause of ADR-0035 and ADR-0036 holds; the record carries the predicate (F35 fixed); the bound, the last-iteration rule and fail-open are implemented and tested; no test fails |
| **B — architectural bug** | **No** | no invariant is violated. The latch *strengthens* ADR-0035's row-4 safety property ("a stagnated goal must never reach `GOAL_MET`"): a monotonic latch is the strongest form of that protection |
| **C — intended monotonic safety property** | **Partly, but not sufficient alone** | the latch is monotonic and conservative, and ADR-0036 §6 ratified the **trap's** non-clearability. But that clause is scoped to *trap-sourced* stagnation, and ADR-0036's own truth table models a clearable flat-run case. Calling it purely intended would assert the repository meant the latch to be universal — **no source says that** |
| **D — valid but incomplete architecture** | **Selected** | four gaps, each measured: (i) a ratified ADR asserts a row with no reachable configuration; (ii) the predicate's docstring says "False once stagnating" but it closes at the first *flat* observation; (iii) the module's documented N-consecutive false-positive mitigation does **not** gate the predicate, so the effective threshold is 1; (iv) the intervention cannot change the goal state, so the enforcement's purpose is unmet at the authority level |
| **E — unresolved** | **No** | the evidence is sufficient: source, execution, and the ADR text agree on what happens; only the *intent* is undecided, and that is a decision, not an evidence gap |

## 14. Architectural Options

Described neutrally in §10 (Options A–D). **No ranking is offered and no winner is selected**, because
the evidence does not make the contract unambiguous: ADR-0036 ratifies the latch *and* models a clearable
case, so either side can be defended from the repository's own records.

## 15. Recommendation Boundary

### Already decided (do not re-litigate)
- Completion and recovery are two authorities; completion is evaluated first (ADR-0035 §5).
- `GOAL_STAGNATED` outranks `GOAL_MET`; a stagnated goal never reaches `GOAL_MET` (ADR-0035 row 4).
- The gate **delays**, it does not veto; the bound is 2; the last iteration cannot be withheld; it fails
  open (ADR-0036 §1–§5).
- The goal record carries the predicate, from the same computation the arbiter used (ADR-0036 §6 / F35).
- The engine receives only a read-only predicate — never the detector (ADR-0036 §3).
- M13 is the sole stagnation authority; per-turn detector construction; `graph_oscillation_guard` is the
  detector switch and `stagnation_gate` is the enforcement switch.

### Not decided
1. **Whether `trap_fired` may be cleared by demonstrated progress** — i.e. whether the latch is a
   deliberate monotonic safety property or an unreachable branch of ADR-0036's truth table.
2. **Who would own the transition** if it may be cleared (Options A–D, none selected).
3. **Whether the predicate's effective threshold (1 flat observation) is intended**, given the module
   documents N-consecutive and `min_consecutive` gates only `verdict`.
4. **Whether the trap-fired-below-threshold case must produce a `STAGNATION` audit record** (measured: 0).

### The decision required next
An **amendment to ADR-0036** (append-only, so a new record that amends it) answering:

> Is the stagnation latch a **deliberate monotonic safety property**, in which case ADR-0036's truth table
> is corrected and `stagnation_gate`'s inability to change the goal state is documented — or may the
> latch be cleared by demonstrated progress, in which case the amendment must name the **owner** of that
> transition, the **evidence** that clears it (`is_progress_from` already computes it), and whether the
> **reopen event** must be recorded for observability?

Sub-questions the amendment must also settle: (ii) is the effective 1-flat-observation threshold
intended; (iii) must the trap-fired case be audited.

**No production code, configuration or test was changed to reach this conclusion, and none should be
until that decision is ratified.**

## 16. Non-Goals

Not done in this phase, and not authorised by it: clearing `trap_fired`; changing
`may_report_goal_met()`; changing `OscillationTrap`; changing `is_progress_from()`; adding a second
detector or completion predicate; modifying goal precedence; modifying recovery semantics; changing
`turn_succeeded`; adding automatic reopening; enabling `stagnation_gate`; adding persistence for a reopen
event; fixing the audit gap; fixing the docstrings; touching F8, the graph, fanout or the pre-existing WIP.

## 17. Evidence

| Claim | How verified |
|---|---|
| The latch | `.workbuddy-ai/memory/post-m13-reopen/probe.py` (executed; output quoted in §5); AST scan of `stagnation.py` + `graph/loop.py` for any assignment to `trap_verdicts`/`_hashes` |
| flat ⟹ repeated digest | `from_verdict_and_graph` has **zero** call sites in `wisp/` (AST scan); `with_work` preserves `c`/`f`/`n` (`stagnation.py:153-157`) |
| The predicate ignores `min_consecutive` | probe, five values of `min_consecutive` |
| The live path behaves the same | instrumented `observe` on a real `run_turn`, 2 and 3 identical read rounds; plus `tests/reliability/test_post_m13_completion_enforcement.py::TestThePredicateIsALatch` and `::TestF35TheRecordCarriesThePredicate` (**4 passed**) |
| The audit gap | the same live probe: 0 `STAGNATION` events when the predicate closes below the threshold; the gate is `runtime.py:1126` |
| The durable surface | `runtime.py:1248-1270` read in source |
| ADR-0036's two rows | `WISP_ARCHITECTURE_DECISIONS.md`, ADR-0036 truth table and §6 |
| Budgets | `recovery.py:326`/`:479`/`:487-491`; `runtime.py:1149`; `config.py`; `goal.py:51` |
| No production change | the tracked diff is byte-identical to the pre-phase snapshot (`diff-before.patch`, 230,220 bytes) |

**Environment limitations recorded:** `jsonschema` is absent (F8), so every tool call in the live probes
is refused before dispatch. That is sufficient here — a refusal still produces a `tool_result` the runtime
observes, and the digest was stable across calls, which is what the proof needs. It does mean the probes
exercise the *refusal* path rather than a successful tool execution; §5's digest evidence shows both paths
fold through the same `with_work`, so the conclusion is unaffected. A diagnostic `observe` wrapper and a
`sys.path` insert were used inside probe scripts only.

### Read-only verification

| Claim | Method | Result |
|---|---|---|
| No production, test or config file changed **by this phase** | the tracked `git diff` compared byte-for-byte against the pre-phase snapshot | **byte-identical — 230,220 bytes both ways** |
| The protected surfaces are untouched | same comparison, plus a per-file `numstat` | `graph/loop.py`, `goal.py`, `verification.py`, `acceptance.py`, `recovery.py` all **zero**; `stagnation.py` shows `122+3` which is **pre-existing WIP**, and the byte-identical diff proves it did not change this phase |
| No test weakened | `tests/` status inspected | entries exist, but all are **pre-existing WIP** from earlier phases; the byte-identical diff proves this phase added none |
| No unrelated WIP reverted | no `git stash` / `reset --hard` / `checkout --` was run at any point | the tree was only ever read |
| Configuration unchanged | the four values re-read after the phase | `graph_oscillation_guard=True`, `stagnation_gate=False`, `goal_state=False`, `record_verdict=False` — the shipped defaults |
| What this phase added | `git status` | the report `PHASE_POST_M13_STAGNATION_REOPENING_RECON.md`; the probe and snapshot live under `.workbuddy-ai/`, which is gitignored |

### Status log

| Checkpoint | Result |
|---|---|
| `STATUS START` | baseline recorded; config values captured |
| `STATUS RECON` | source trace complete; 10 edges with producer/consumer/authority/persistence |
| `STATUS LATCH` | **proven**; `REOPEN_POSSIBLE: NO`; strengthened vs the earlier report |
| `STATUS AUTHORITY` | ownership resolved; `REOPENING_AUTHORITY: UNDECIDED`; one internal competition named |
| `STATUS REPLAY` | `LIVE_REPLAY_EQUIVALENCE: PROVEN` (given the record); one audit gap measured |
| `STATUS OSCILLATION` | bounded; six distinct budgets enumerated; recovery budget **not live** |
| `STATUS CONTRADICTION` | three source-vs-documentation contradictions; surfaced, not reconciled; phase continued with stated justification |
| `STATUS ADR` | `ADR_AMENDMENT_REQUIRED` |

## 18. Final Status

```
=== STATUS: COMPLETE ===
PHASE: POST-M13-STAGNATION-REOPENING-RECON
MODE: READ-ONLY
PRODUCTION_CHANGES: 0
DECISION_STATE: ADR_AMENDMENT_REQUIRED
LATCH_STATUS: proven (trap_fired is a monotonic latch; the only reset is the turn boundary)
REOPEN_STATUS: no (predicate closed <=> trap_fired; no clearing path inside a turn)
AUTHORITY_STATUS: resolved for detection/completion/recovery/replay; REOPENING_AUTHORITY = UNDECIDED
REPLAY_STATUS: proven for the goal state, given the record (conditional on `goal_state`);
               one auditability gap measured (0 STAGNATION events when the trap closes the predicate)
BOUNDEDNESS_STATUS: bounded (intervention budget 2/turn caps any reopen cycle; no unbounded path)
ADR_STATUS: ADR AMENDMENT REQUIRED
REPORT: PHASE_POST_M13_STAGNATION_REOPENING_RECON.md
NEXT: amend ADR-0036 (append-only) to decide whether the latch is a deliberate monotonic safety property
      — correcting the truth table and documenting that `stagnation_gate` cannot change the goal state —
      or to introduce a bounded reopen rule naming its owner, its evidence and its replay contract.
      No implementation until that decision is ratified.
=============================
```

```
RECON COMPLETE — ADR AMENDMENT REQUIRED
```
