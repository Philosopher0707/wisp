# PHASE POST-M13 — COMPLETION / RECOVERY AUTHORITY IMPLEMENTATION

**Phase type:** production implementation of **ADR-0035**.
**Date:** 2026-09-24
**Predecessors:** `PHASE_POST_M13_AUTHORITY_RECON.md` · `PHASE_POST_M13_VERDICT_CONTRACT.md` · `PHASE_POST_M13_AUTHORITY_ADR.md` (ADR-0035)

---

## 1. Baseline

| Property | Value |
|---|---|
| Starting commit | `7c15626` — `docs(m11): record the M11 phase; findings F29-F31; repair the commit table` |
| Branch | `main` |
| Working tree | **dirty** — 39 tracked-modified + 43 untracked before this phase began |
| Pre-existing modifications | Phase 10 / Phase 13 docs, `wisp-desktop/`, `wisp/server/routes/`, `wisp/pathsec.py`, `wisp/provider_catalog.py`, `tests/conftest.py`, `wisp/multi_agent/_circuit_breaker.py` (untracked, the user's) — **all treated as immutable** |
| Diff at baseline | 39 files, 1559 insertions / 176 deletions (155,373 bytes) |

Snapshot: `.workbuddy-ai/memory/post-m13-impl/{head,branch,status-before}.txt`, `diff-before.patch`.
No `stash`, `reset`, `checkout` or `clean` was used.

**Relevant test baseline (before any edit):**

| Suite | Result |
|---|---|
| `test_stagnation_live_wiring`, `test_stagnation_detection`, `test_acceptance_verdict`, `test_recovery_ladder`, `test_failure_signal_classification` | **233 passed** |
| Migration suite set (24 files) | **714 passed** |
| Full suite | **129 failures**, set-identical to `baseline-failures-stable.txt` (measured in the predecessor phase, same tree) |

---

## 2. Implementation

Nine files: four production, three tests, two new.

### Production

| File | Change | Why |
|---|---|---|
| **`wisp/core/goal.py`** *(new)* | `TerminalOutcome`, `GoalState` (6), `derive_goal_state()`, `terminal_outcome_from_evidence()`, `already_recorded_from()`, `goal_state_from_record()`, `PRECEDENCE` | ADR-0035's pure arbiter. No I/O, no model, no store, no mutation |
| **`wisp/core/session.py`** | `SessionEventType.GOAL_STATE`; `SessionEvent.goal_state_event()`; the `apply` case; the `goal_states` field; the `journal_records()` entry; `JOURNAL_ONLY_RECORDS`/`SHAPES` | The durable record. Journal-only, **not** state-bearing |
| **`wisp/core/runtime.py`** | read `recovery_ladder` + `goal_state`; capture `terminal_error_code`; the recovery consumer; the goal-state derivation + recording; widen the stamping gate | The two consumers, at the ADR-defined sites |
| **`wisp/config.py`** | **two** flags: `recovery_ladder` and `goal_state`. Both default **`False`** | `recovery_ladder` is the name ADR-0026 reserved; `goal_state` is ADR-0035 clause 9's staging |

### Tests

| File | Change |
|---|---|
| **`tests/reliability/test_post_m13_authority_implementation.py`** *(new)* | 50 tests — the T/D/flag/live/adversarial matrices |
| **`tests/test_stagnation_live_wiring.py`** | the two M13 tripwires **inverted** (not deleted) |
| **`tests/test_escalation_durability.py`** | M16's totality guard declares `GOAL_STATE` |
| **`tests/reliability/test_13h4_success_semantics.py`** | the producer ratchet refined to AST — see §3 |

### Change surface, measured not asserted

`git diff` was parsed into per-file blocks and compared against the baseline snapshot, so the list below
is the *actual* delta and not the pre-existing WIP it sits alongside:

```
tests/reliability/test_13h4_success_semantics.py
tests/test_escalation_durability.py
wisp/core/runtime.py
wisp/core/session.py
wisp/config.py                (was unmodified at baseline)
+ untracked: wisp/core/goal.py
+ untracked: tests/reliability/test_post_m13_authority_implementation.py
+ untracked: tests/test_stagnation_live_wiring.py  (M13's file; edited in place)
```

**Frozen surfaces verified unchanged** (zero diff): `wisp/core/verification.py`, `wisp/core/acceptance.py`,
`wisp/core/recovery.py`, `wisp/core/events.py`, `wisp/core/stagnation.py`, all of `wisp/graph/` and
`wisp/core/graph/`.

---

## 3. Two guards fired — and both were right

The first full-suite run after wiring produced **139 failures, 10 NEW**, all in the 13-H4/H5
success-semantics suites. They were not noise, and neither was worked around. **The 10 failures changed
the implementation.**

### Guard 1 — the turn journal's exact event sequence (9 tests)

`test_13h4`/`test_13h5` assert the journal's event list for a clean turn:

```python
assert _repo_types(repo, "a") == ["user_message", "assistant_message", "done"]
```

My first implementation wrote the `GOAL_STATE` record on **every** turn, so the list became
`[..., "goal_state", "done"]`. The tests were pinning a real contract: the default turn journal.

### Guard 2 — the one-producer ratchet (1 test)

```python
hits = [p for p in Path("wisp").rglob("*.py") if "turn_succeeded" in p.read_text()]
assert hits == ["wisp/core/runtime.py"]
```

A whole-file substring search. `wisp/core/goal.py` *consumes* the turn-success fact as an input, so the
search matched a **reader** and failed.

### What the guards forced

**ADR-0035 clause 9 settles it:** *"The goal state is **recorded audit-only first**, under the P3
stage-3a pattern."* The P3 stage-3a pattern is `record_verdict` — **opt-in, default OFF**, precisely
because it "adds a record to the log of every existing caller". So the always-on record was **not** what
the ADR asked for, and the guard caught the deviation.

Corrected:

1. **`goal_state` flag added, default `False`.** The goal state is now recorded only when the flag is on.
   The default turn journal is byte-for-byte what it was, which is asserted
   (`test_the_default_config_writes_no_goal_state_record`).
2. **The producer ratchet refined to AST** over *assignment targets*, with a non-vacuity assertion. This
   is the repo's own precedent — M12 turned a whole-file grep guard into an AST check for the same
   reason — and it is **strictly stronger**: a reader is now explicitly permitted while the producer
   stays pinned to `runtime.py`.

Both guards were answered by *declaring* what changed or *sharpening* the check, never by relaxing a
count. This is the third time this migration's guards have caught a phase's own incompleteness, and the
second time the fix was a blunt proxy refined into an AST check.

---

## 4. Goal-state implementation

```
INPUTS (all established by other authorities — nothing re-derived)
  terminal_outcome      <- saw_done / saw_fatal_error              (13-H5)
  acceptance_verdict    <- floor_guard_verdict(core._last_guard)   (P3, ADR-0018)
  stagnating            <- not detector.may_report_goal_met()      (M13)
  turn_succeeded        <- saw_done and not saw_fatal_error        (unchanged)
  cancelled             <- no live signal yet (see §8)
  escalated             <- from the ladder, when it exhausts

ARBITRATION (first match wins; no score, no timestamp, no enum order)
  0  already_recorded           -> frozen (ADR-0020)
  1  cancelled                  -> CANCELLED
  2  escalated                  -> ESCALATED_TO_HUMAN
  3  FAIL or terminal FAILED    -> GOAL_FAILED
  4  stagnating                 -> GOAL_STAGNATED
  5  INCONCLUSIVE or INCOMPLETE -> GOAL_UNVERIFIED
  6  turn_succeeded and PASS    -> GOAL_MET
     (fallthrough)              -> GOAL_UNVERIFIED
```

`derive_goal_state()` is **total**: a test drives every combination of the inputs (3 × 4 × 2 × 2 × 2 × 2
= 192 cases) and asserts the image is exactly the six states.

**P3 remains an input, not a second authority.** The runtime reads the verdict from the guard the engine
published (`getattr(core, "_last_guard")`) and calls the existing `floor_guard_verdict` — the one floor
implementation ADR-0018 preserved. No verification logic was reimplemented; the arbiter inspects no files,
no git state, no tool results and no artifacts.

**Ordering, and the one place ADR-0035's diagram and its table disagree.** The flow diagram puts the goal
state before recovery evaluation, but the **precedence table** puts `ESCALATED_TO_HUMAN` at row 2 — an
input that only exists after the recovery track runs. The table is normative ("do not reinterpret its
precedence rules"), so the derivation runs **after** the recovery consumer in the same post-turn block.
The ordering *guarantee* is unaffected and still holds: the completion **gate** is in the engine, before
`done` is emitted, so recovery still cannot prevent or declare completion.

---

## 5. Recovery implementation

```
not turn_succeeded   (nothing to recover from otherwise)
        |
        +-- stagnating? --> route_to_recovery(detector, ladder, evidence)   [P7, reused]
        |                   else -> ladder.decide(classify_failure_signal(  [M12 + P6]
        |                                        message, recoverable, code), evidence)
        |
        +-- a rung was chosen -> SessionEvent.recovery_event(...)
        +-- ladder.escalated  -> SessionEvent.escalation_event(...)
```

- Gated by `recovery_ladder`, **default OFF**.
- `route_to_recovery` and `RecoveryLadder.decide` are **reused, not reimplemented**.
- The classifier is ADR-0032's `classify_failure_signal`; the runtime supplies the `(message,
  recoverable, code)` it already had, plus the error `code` it now captures.
- **A record is emitted only when a rung is actually chosen**, and an escalation only when the ladder is
  actually exhausted — never because a detector exists, never merely because a failure occurred.

---

## 6. Durability

The goal-state record carries **its inputs as well as its answer**, which closes ADR-0035's two gaps
without turning on the diagnostic `VERDICT` record:

| Recorded field | Closes |
|---|---|
| `goal_state` | the answer |
| `terminal_outcome`, `failure_code` | 13-H5 evidence; budget vs timeout |
| **`acceptance_verdict`** | **gap #1** — present whenever the goal state is authoritative, while `record_verdict` stays opt-in and `verdicts` stays empty |
| **`stagnation_verdict`** | **gap #2** — `unknown` / `progressing` / `stagnating` for every evaluated turn, with `not_evaluated` reserved for an absent detector |
| `turn_succeeded`, `cancelled`, `escalated` | the remaining arbitration inputs |

**Replay is a re-derivation, not a read.** `D6` re-derives the recorded answer from the record's own
inputs and asserts equality. The record is a query convenience; the journal remains authoritative.

**`recovery_event` and `escalation_event` have producers for the first time** — the constructors existed
with no caller, so the ladder's output previously had no durability path at all.

---

## 7. Flags

| Flag | Default | Gates | Evidence |
|---|---|---|---|
| **`goal_state`** / `WISP_GOAL_STATE` | **`False`** | the completion record | `config.py` |
| **`recovery_ladder`** / `WISP_RECOVERY_LADDER` | **`False`** | the RECOVERY track only | `config.py` |
| `graph_oscillation_guard` | `True` | M13's detector | unchanged |
| `record_verdict` | `False` | the diagnostic `VERDICT` record | unchanged |

`recovery_ladder` is the name **ADR-0026's reversal condition** reserved; this is its first
implementation. Both defaults are asserted.

**Completion and recovery flags are separate and stay separate.** `goal_state` records; it does not
enforce. `recovery_ladder` routes; it cannot declare completion. The test
`test_the_recovery_flag_does_not_change_completion_semantics` pins that the goal state is identical with
the recovery flag on and off.

---

## 8. M13 tripwires

Both were **inverted in this phase**, as ADR-0035 requires — not deleted, and not weakened before the
consumers existed (the consumers were wired first, then the inverses written).

| Was (M13) | Now (ADR-0035) | Asserts |
|---|---|---|
| `test_the_ladder_is_not_consulted_yet` | `test_the_ladder_is_consulted_at_the_turn_boundary` | a `.decide` attribute access **is** present in `runtime.py` |
| `test_goal_met_is_not_yet_gated_on_the_live_path` | `test_goal_met_is_gated_on_the_live_path` | a `.may_report_goal_met` call **is** present in `runtime.py` |

Both remain AST-based (a source grep would match its own explanatory comment — the P8 trap). Their job
changed from *"this is not wired yet"* to *"this must stay wired"*, so a later phase that unwires either
one fails here instead of passing silently. `test_the_mechanism_still_has_one_authority` is untouched.

---

## 9. Tests

`tests/reliability/test_post_m13_authority_implementation.py` — **50 tests**.

| Group | Count | Covers |
|---|---|---|
| Precedence matrix | 16 | T1–T12, escalation-over-failure, totality (192 combinations), exactly-six-states |
| T8 security | 4 | denial classifies as SECURITY; retry forbidden by class; escalates; timeout≠budget by code |
| Durability D1–D8 | 11 | reconstructible state; acceptance verdict present; positive stagnation outcome; recovery + escalation records; replay of state and decision; loud failure; audit-only; journal-only; first-record-frozen; blob lacks it |
| Flags | 7 | both defaults off; default writes no goal record and the journal is unchanged; default writes no recovery record; each flag wires its consumer; the recovery flag does not change completion |
| Live integration | 5 | content turn; fatal turn; stagnating turn; turn/goal coexistence; the recovery consumer on the real path |
| Adversarial | 7 | false success via `done`; false success via stagnation; failure erasure; recovery≠success; missing evidence; duplicate events; **kill/restart/reconstruct** |

**Every live test drives `AgentRuntime.run_turn`** — the real path, not a pure function. That is the point
of the phase: the mechanisms now have consumers.

**One test expectation was wrong and was corrected, not the code.** `D3` initially asserted a content-only
turn records `progressing`; the correct P7 semantics for a turn with no observations is `unknown`. The
assertion now requires one of the three real verdicts and explicitly rejects `not_evaluated`.

---

## 10. Regression status

| Suite | Before | After | Delta |
|---|---|---|---|
| New phase tests | — | **50 passed** | new |
| Migration suite set (24 files) | 714 passed | **714 passed** | **0** |
| 13-H4 + 13-H5 success semantics | 48 passed | **48 passed** | **0** (after the §3 correction) |
| Focused M13/M16/P3/P6 guards | 233 passed | **243+ passed** | +10 (inverted tripwires and declared guards) |
| Full suite, first run | 129 | **139 — 10 NEW** | caught by the guards |
| Full suite, after the §3 correction | 129 | **129 — set-identical, 0 new, 0 fixed** | **0** |

The final figure is computed as an independent Python set diff against
`.workbuddy-ai/memory/baseline-failures-stable.txt` (not `comm` — F31), requiring **both** directions
empty. Artefact: `.workbuddy-ai/memory/post-m13-impl/fullrun-final.txt`.

**Pre-existing failures are unchanged and are not this phase's.** They are the environmental set (missing
`jsonschema`/`httpx` dominate) plus the 18 fanout-family failures (M8). The repository is **not** fully
green, and this report does not claim it is: 129 failures were pre-existing and environmental, and were
129 before this phase on this same tree.

---

## 11. F8

**F8 was not repaired.** `jsonschema` remains absent; `stateless.py:2214-2227` still converts the
`ModuleNotFoundError` into `"Schema validation failed for tool ..."`. Dependency declarations, the
validation architecture, tool-argument semantics and schema loading are all untouched.

F8 is a **measurement blocker, not a contract blocker** (ADR-0035). Its visible effect here is that no
tool executes, so `VerificationFloorGuard.wrote_code` stays `False` and the acceptance verdict is
`INCONCLUSIVE` — which is why the live tests observe `GOAL_UNVERIFIED` rather than `GOAL_MET` for
successful turns. That is the honest outcome, and it is asserted rather than worked around.

---

## 12. Production safety

| Claim | Verified |
|---|---|
| No new model authority | the arbiter is pure; no model call, no provider request |
| No recovery→completion coupling | the recovery consumer writes only `RECOVERY`/`ESCALATION`; the goal state is unchanged by its flag (asserted) |
| No `turn_succeeded` rewrite | `runtime.py:878` untouched; the ratchet still pins one producer, now by AST |
| No `VerificationFloorGuard` rewrite | `verification.py` — **zero** diff |
| No `evaluate()` change | `acceptance.py` — **zero** diff |
| No graph execution change | `wisp/core/graph/`, `wisp/graph/` — **zero** diff |
| No fanout change | untouched |
| No F8 change | untouched |
| No new framework | one enum pair and one function; no state-machine library, no fifth verdict vocabulary |
| Default behaviour unchanged | both new flags default OFF; the default turn journal is asserted unchanged |
| Performance | one record per turn carrying scalars; no transcript copies, no blobs, no extra provider round-trips |

---

## 13. Acceptance criteria (brief §38)

| # | Criterion | Status |
|---|---|---|
| 1 | ADR-0035 implemented without reinterpretation | **yes** — including the documented resolution of the diagram/table tension, in favour of the normative table, and the §3 correction *towards* clause 9 |
| 2 | Six goal states exist | **yes** — asserted exactly |
| 3 | Pure deterministic goal arbitration exists | **yes** — `wisp/core/goal.py` |
| 4 | P3 remains an input, not a second authority | **yes** — reads the published guard; `acceptance.py` unmodified |
| 5 | P3 PASS + STAGNATING → GOAL_STAGNATED | **yes** — T2, pure and live |
| 6 | P3 FAIL overrides terminal success | **yes** — T3 |
| 7 | INCONCLUSIVE → GOAL_UNVERIFIED | **yes** — T4, live |
| 8 | UNKNOWN remains non-blocking | **yes** — T5; UNKNOWN is not converted into PASS |
| 9 | Recovery cannot declare completion | **yes** — adversarial test |
| 10 | Cancellation has explicit precedence | **yes** — row 1, tested; **no live cancellation signal exists** (§14) |
| 11 | Escalation remains distinct | **yes** — row 2, distinct state, durable record |
| 12 | Turn completion remains unchanged | **yes** — asserted by the refined ratchet |
| 13 | Goal and turn state are distinct | **yes** — coexistence asserted both ways |
| 14 | Acceptance evidence durable when authoritative | **yes** — D2 |
| 15 | Positive stagnation result durable | **yes** — D3 |
| 16 | Recovery records have real producers | **yes** — first producers ever |
| 17 | Escalation records have real producers | **yes** — D5 |
| 18 | Failure classification durable for authoritative recovery | **yes** — the `RECOVERY` record carries `failure_class` |
| 19 | `recovery_ladder` exists and defaults OFF | **yes** |
| 20 | Replay is deterministic | **yes** — D6, D7, restart test |
| 21 | Missing authoritative evidence fails loudly | **yes** — D8; `goal_state_from_record` raises |
| 22 | Terminal states are frozen | **yes** — row 0; first-record-wins on replay |
| 23 | Consumers wired at the ADR-defined sites | **yes** — derivation at the turn boundary; recovery at the turn boundary; both behind flags per clause 9 |
| 24 | M13 tripwires inverted only after consumers exist | **yes** — inverted in this phase, after wiring |
| 25 | F8 untouched | **yes** |
| 26 | No unrelated architecture changed | **yes** — measured change surface in §2 |
| 27 | Required tests pass | **yes** — 50 |
| 28 | Existing failures distinguished from new | **yes** — §10, set-diffed |

### On criterion 23, precisely

ADR-0035 puts the *enforcement* site at the engine's pre-`done` gate. This phase implements the
**recording**, which ADR-0035 clause 9 stages first. Two of the arbiter's three inputs are therefore
gathered where they exist: the acceptance verdict from the engine's published guard, and the stagnation
predicate from the runtime's per-turn detector. **The stagnation predicate is not available in the
engine**, and giving the engine its own detector would be a *second* detector — which the repository's
one-authority discipline forbids. Wiring stagnation *enforcement* at the pre-`done` gate needs either an
engine-interface change (passing the runtime's detector into `core.turn`) or a different detector
ownership; neither is authorised by ADR-0035's implementation boundary, and both are enforcement rather
than recording. The predicate **is** consumed today (row 4), and the inverse tripwire keeps it consumed.

---

## 14. Bounded deviations and gaps (stated, not hidden)

1. **Cancellation has no live producer.** `cancelled` is an arbitration input with tested precedence, but
   no foreground cancellation signal exists in the runtime, so the live path passes `False`. The
   precedence is implemented and proven; the producer is a separate concern.
2. **Stagnation enforcement at the pre-`done` gate is not wired** — see criterion 23 above.
3. **`BUDGET_EXHAUSTED` / `TIMED_OUT` remain `reason`s** inside `GOAL_FAILED`, per ADR-0035, and are
   distinguishable by the `failure_code` the record now carries.
4. **The recovery consumer runs only when the turn did not succeed.** A stagnating-but-successful turn
   gets `GOAL_STAGNATED` from the completion track; routing stagnation to the ladder on a *successful*
   turn is a policy question ADR-0035 does not settle, so it is not done.

None required inventing architecture. None weakens the contract.

---

## FINAL REPORT

### Decision implemented

ADR-0035, faithfully: two authorities, one arbiter, an explicit ordered precedence table, a derived
six-state goal level recorded durably, and a recovery track wired behind its own flag. The one place the
ADR's diagram and its table disagreed was resolved in favour of the table and documented. One first
implementation was **corrected** when two guards fired — the always-on record was not what clause 9 asked
for.

### Goal-state implementation

`core/goal.py::derive_goal_state()` — pure, total, side-effect free, model-independent. Inputs are facts
other authorities established (13-H5 terminal evidence, P3's verdict via the published guard, M13's
predicate, escalation from the ladder). Recorded per turn as a journal-only `GOAL_STATE` record that
carries its own inputs, behind the `goal_state` flag (default OFF).

### Recovery implementation

At the turn boundary, behind `recovery_ladder` (default OFF): `classify_failure_signal` →
`RecoveryLadder.decide`, or `route_to_recovery` when stagnating → `RECOVERY` record, plus `ESCALATION`
when the ladder is exhausted. The first production producers these records have ever had.

### Durability

The record carries `acceptance_verdict` and `stagnation_verdict`, closing both gaps ADR-0035 identified.
Replay re-derives the state from the record's own inputs and is asserted equal (D6); a
kill/restart/reconstruct test proves the same authority result.

### Flags

`goal_state` (completion, records only) and `recovery_ladder` (recovery) — **both default OFF**. The
default turn journal is asserted byte-for-byte unchanged.

### M13 tripwires

Both inverted in this phase, after the consumers were wired, preserving the AST approach and the
one-authority pin. One further ratchet (13-H4's producer check) was **strengthened** to AST.

### Tests

50 new phase tests; 714 migration tests unchanged; 13-H4/H5 back to 48 passed; full suite **129,
set-identical, 0 new, 0 fixed**.

### Production changes

`wisp/core/goal.py` (new) · `wisp/core/session.py` · `wisp/core/runtime.py` · `wisp/config.py` ·
`tests/reliability/test_post_m13_authority_implementation.py` (new) ·
`tests/test_stagnation_live_wiring.py` · `tests/test_escalation_durability.py` ·
`tests/reliability/test_13h4_success_semantics.py`

### F8

**Not repaired.** Classified and left as a measurement blocker, not a contract blocker.

```
PHASE STATUS: COMPLETE
```

Criterion 23 is satisfied for **recording**, which is what ADR-0035 stages first; enforcement at the
pre-`done` gate is deliberately deferred, with the one blocking input documented in §13. That is a
bounded, documented gap inside a staged contract — not an inability to implement the contract, which is
why the status is COMPLETE rather than BLOCKED.
