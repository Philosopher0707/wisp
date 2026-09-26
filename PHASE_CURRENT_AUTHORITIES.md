# PHASE — CURRENT AUTHORITIES (Deliverable 1)

**Deliverable:** `CURRENT_AUTHORITIES.md` — the current-state-of-the-authorities page.
**Type:** documentation. **Introduces no decision.**
**Baseline:** `af3a89a`. **Predecessor:** `PHASE_MULTI_TURN_PRODUCTIVE_RECOVERY.md` (ADR-0047).

---

## 1. What was produced

| Artefact | Lines | What it is |
|---|---|---|
| `CURRENT_AUTHORITIES.md` | 162 | The page. Six authorities, the run-level aggregation, the precedence matrix, and a section for the claims it could **not** pin. |
| `tests/reliability/test_current_authorities_pins.py` | 28 tests | The guard: the page cannot rot silently. |

The page states **current** state only. It cites ADRs; it re-derives none. It is declared
**regenerated, not edited in place**, and it names the commit it was generated at.

Every claim carries two pins — an `ADR-XXXX §Y` and a `path:line`. **37 code pins**, all
mechanically verified (§4). Six authorities, each with **owner / cannot-decide / current-ADRs /
durable record fields**:

| Authority | Current owner | Cannot decide |
|---|---|---|
| turn predicate | `goal.terminal_outcome_from_evidence` (`wisp/core/goal.py:132`); `turn_succeeded` derives (`wisp/core/runtime.py:954`) | goal state · recovery · verification |
| stream state | `guarded_provider_stream` (`wisp/core/provider_stream.py:113`) | turn success · acceptance · goal state |
| acceptance verdict | `acceptance.evaluate` (`wisp/core/acceptance.py:213`) | goal state · recovery · whether to gate |
| progress verdict | `progress.evaluate_progress` (`wisp/core/progress.py:155`) | acceptance · goal state · which rung |
| goal state | `goal.derive_goal_state` (`wisp/core/goal.py:146`) | recovery rung · whether `done` was withheld |
| recovery ladder state | `RecoveryLadder.ladder_state` (`wisp/core/recovery.py:742`) | completion · goal state |

**Run-level aggregation**, stated once (ADR-0047 R13): *a run's state is the ladder's escalation if
it surrendered, otherwise the last attempt's derived state.*

**Precedence matrix** reproduced as rows 0–7, pinned to `goal.PRECEDENCE` (`wisp/core/goal.py:108-117`)
and to `derive_goal_state` (`wisp/core/goal.py:190-213`). The combination F60 moved is marked.

---

## 2. What was found **unpinnable** — the two findings

The brief's constraint is that a claim which cannot be pinned is a **finding**, not a claim. Two
surfaced. Both are recorded in the page's §5 and are **not decided** there.

### F-1 — the arbitration has drifted further than ADR-0047 states

ADR-0047 R1 says: *"Only one input combination moved: fatal error + `PASS`, `GOAL_FAILED` →
`GOAL_MET`."* `WISP_MIGRATION_STATUS.md` F60 repeats it: *"Exactly one input combination moved."*

**Measured.** A differential of `derive_goal_state` at `af3a89a` against the same function at
`b9af5f0^` — the last revision before ADR-0047 — over the **full** input space
(`terminal_outcome` × `acceptance_verdict` × `stagnating` × `turn_succeeded` = 48 combinations):

```
total combinations: 48
MOVED: 7
  outcome=succeeded  acceptance=pass         stagnating=False turn_succeeded=False  goal_unverified -> goal_met
  outcome=failed     acceptance=pass         stagnating=False turn_succeeded=False  goal_failed -> goal_met
  outcome=failed     acceptance=pass         stagnating=False turn_succeeded=True   goal_failed -> goal_met
  outcome=failed     acceptance=pass         stagnating=True  turn_succeeded=False  goal_failed -> goal_stagnated
  outcome=failed     acceptance=pass         stagnating=True  turn_succeeded=True   goal_failed -> goal_stagnated
  outcome=incomplete acceptance=pass         stagnating=False turn_succeeded=False  goal_unverified -> goal_met
  outcome=incomplete acceptance=pass         stagnating=False turn_succeeded=True   goal_unverified -> goal_met
```

Seven cells, in **three distinct semantic classes**:

| Class | Cells | Documented? |
|---|---|---|
| (a) `FAILED` + `PASS` → `GOAL_MET` | 2 | **Yes** — ADR-0047 R1 |
| (b) `turn_succeeded=False` no longer blocking `PASS` | 1 (`SUCCEEDED`+`PASS`) | **Yes** — ADR-0047 R2 |
| (c) **`INCOMPLETE` + `PASS` → `GOAL_MET`** (was `GOAL_UNVERIFIED`) | 2 | **No** |
| (c) **`FAILED` + `PASS` + `stagnating` → `GOAL_STAGNATED`** (was `GOAL_FAILED`) | 2 | **No** |

Class (c) is the substance. The pre-ADR-0047 body ordered row 5
(`acceptance == "inconclusive" or outcome == INCOMPLETE → GOAL_UNVERIFIED`) **before** row 6
(`turn_succeeded and acceptance == "pass" → GOAL_MET`). ADR-0047 removed row 5's second term and
moved `PASS` above the fall-through, which is what makes `INCOMPLETE`+`PASS` reach `GOAL_MET`.

**Is that intended?** Plausibly yes — it is the same principle as F60 (the objective's evidence
decides where it is decisive; the attempt's outcome decides only where it is not), applied to
`INCOMPLETE` rather than `FAILED`. **But ADR-0047 does not say so**, and the reachable case is real:
a turn that exhausts its iteration budget without emitting `done`, while the harness measures every
criterion satisfied, now reports `GOAL_MET`.

**Not decided here.** The page states the code's current behaviour and points at this finding. Whether
(c) is a ratified consequence or a third undocumented change is a decision for a future phase, and it
would be taken against ADR-0047's own text — not by this page.

The `FAILED`+`PASS`+`stagnating` cell also deserves naming, because it touches a rule ADR-0047 R3
states explicitly: *"a fatal error must outrank stagnation, so a heuristic cannot soften a fact."*
With a `PASS` present, a fatal error **does not** outrank stagnation — it yields `GOAL_STAGNATED`.
R3's rule holds where R3's own test exercises it (no `PASS`), so this is not a contradiction of R3;
it is a cell R3's prose does not cover.

### F-2 — the precedence table is total only in the code

ADR-0035's arbiter table has **rows 0–6** and no fall-through row. The combination
`terminal_outcome = SUCCEEDED` with **no** acceptance verdict matches none of them, and ADR-0035's
taxonomy gives `GOAL_UNVERIFIED` the requirement `P3 INCONCLUSIVE ∨ terminal INCOMPLETE` — which that
combination does not satisfy. The implementation has always had a row 7 for it (it is the final
`return`), so **the ADR's table was never total; the code's was.**

ADR-0047 then revised the table **without restating it**: it says *"Row 4 is now «fatal terminal
error, and no P3 PASS»"* and *"Amends ADR-0035's precedence rows 3–6"*. Under ADR-0035's own
numbering, **row 4 is stagnation** — not the fatal clause. ADR-0047's numbering exists only in the
implementation (`goal.PRECEDENCE`, which has 8 rows, 0–7).

**Consequence, and why it matters more than a typo.** `WISP_ARCHITECTURE_DECISIONS.md` is append-only
by design, so an amendment that renumbers without restating leaves **two live numberings** for one
table. "Row 4" is now ambiguous between the two ADRs and the code. The brief for *this* deliverable
inherited the ambiguity: it asked for *"rows 0–6 … with the fatal-error clause qualified"*, which is
ADR-0035's numbering crossed with ADR-0047's qualifier — a combination that exists in neither
document. The page states rows **0–7** and says which is which.

This is precisely the drift the deliverable exists to prevent, and it was found by trying to write
the page.

---

## 3. What ADR conflicts surfaced

**None between two ADRs.** ADR-0047 does not contradict ADR-0035; it *amends* it, and the amendment
is coherent. What surfaced is narrower and is F-2: an amendment whose **numbering** was not restated,
so the two documents cannot be read independently. There is no decision to take here, and none was
taken.

One boundary was checked and found **clean**: ADR-0047 R3's three-rule cycle (`fatal > stagnation`,
`stagnation > PASS`, `PASS > fatal`) is a genuine cycle, and the code's resolution — qualifying only
the clause whose meaning depends on the verdict — is the one the ADR describes. The cycle is real;
the conjunction is the right shape for it.

---

## 4. The guard, and its non-vacuity

`tests/reliability/test_current_authorities_pins.py` — **28 tests**, three independent properties:

1. **Every pin resolves** — each `path:line` names a real, non-blank line; pins must be fully
   qualified; each of the six authority sections must carry ≥2 of its own.
2. **The matrix is the arbiter's** — row-by-row against `goal.PRECEDENCE`, then each row's
   `condition → result` driven through the **real** `derive_goal_state`.
3. **The page declares itself derived** — the regeneration contract, the no-decision disclaimer, and
   the generation commit are asserted as prose tripwires.

**Falsification.** Six mutation probes, each restoring the tree byte-identical (sha256 verified):

| Probe | Caught |
|---|---|
| P1 insert a blank line at the top of `goal.py` (shifts every pin) | ✅ |
| P2 swap the arbiter's rows 5/6 in `goal.py` (**size-preserving**) | ✅ |
| P3 delete the regeneration contract from the page header | ✅ |
| P4 make one pin relative (`goal.py:190-191`) | ✅ |
| P5 falsify a matrix row's **result** | ✅ *(after the gap below was closed)* |
| P6 falsify a **Cannot-decide** claim | ✅ |
| CONTROL, before and after all probes | ✅ green |

### Two instrument defects found while falsifying — both recorded

**P5 did not falsify on the first attempt.** Flipping row 6's result from `GOAL_MET` to
`GOAL_UNVERIFIED` in the page was **not caught**: the guard asserted each row *existed* but never
asserted it named the right **result**. The probe is the only reason this was found — the test looked
correct. Closed by parsing the result cell. *This is the mission's own instruction working as
intended: a probe that does not falsify means the test is not testing what you think.*

**The first probe harness reported a false control failure.** The control run after the probes read
`1 failed, 14 passed` on a tree that was byte-identical to the green one. The cause was the
**harness**, not the test: P2's mutation is size-preserving, so a same-second restore left a stale
`wisp/core/__pycache__/goal.*.pyc` holding the mutated bytecode. Fixed by purging that `.pyc` on both
sides of each probe. *The same class as F41 and F54 — an instrument that does not reproduce the real
control flow reports its own defect as the subject's.* Recorded because the probe results are only
trustworthy once the instrument is.

**One probe also caught a real weakness in the page**: `progress.py` is ambiguous in this repository —
`wisp/core/progress.py`, `wisp/progress.py`, and `wisp/transport/progress.py` all exist — so the
short-form pin `progress.py:50` did not identify a file. All 37 pins are now fully qualified. A
resolver that guessed would have been a second authority for "where the code lives", which is why the
fix was to qualify the page rather than to make the guard cleverer.

---

## 5. Navigability observations (corpus-level, reported not repaired)

Two facts about the corpus that bear on the deliverable's *purpose*, both confirmed and neither
changed here:

1. **The migration-wide findings log is split.** `WISP_MIGRATION_STATUS.md` §23 carries **F1–F44**;
   the 19 findings this mission's predecessors produced — **F45–F63** — live in that same file's §0,
   not in §23. The brief cites *"§23 (F60–F63)"*; they are not there. `CONTEXT.md` §0 calls the file
   *"the phase ledger, findings F1–F63"*, which is true of the file and false of §23. **A reader
   following the brief's pointer lands on a table that stops 19 findings short.** Recorded; not
   reorganized, because moving rows is an editorial decision this deliverable is not authorised to
   take.
2. **A shell-`grep` false negative recurred.** `grep -nE '\bF(4[5-9]|5[0-9]|6[0-3])\b'` over
   `WISP_MIGRATION_STATUS.md` returned **nothing**, and the Grep tool returned the rows immediately.
   This is the fourth recorded instance of BSD `grep` reporting absence for a pattern that is
   present (CONTEXT.md §6, §10 instance 9). It is the same failure shape the migration keeps
   finding — *a tool that cannot see the whole picture reports absence as death* — and it was caught
   only because the negative result was cross-checked before being believed.

---

## 6. Regression

The canonical migration suite (CONTEXT.md §11, extended with the four NEXT-mission files and this
deliverable's guard):

```
env -u PYTHONPATH -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
  .venv/bin/python -m pytest <34 files> -q -p no:cacheprovider --tb=no -rf
```

**Set, not verdict — measured, not estimated:**

| | |
|---|---|
| **After this deliverable** | **1035 tests — 1034 passed, 1 failed** (104.17 s) |
| **Before this deliverable** (same command, guard file removed) | 1007 tests — 1006 passed, 1 failed |
| **The one failure, both runs** | `tests/test_node_identity.py::TestANodeReferencesItsWorkUnit::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing and unchanged |
| **New failures** | **none** — the failure set is identical in both directions |
| **Now-passing** | none |
| **Delta** | **+28 tests, +28 passed** — exactly the new guard file; **0 production files touched** |

`CONTEXT.md` §11's heading still reads *"849 tests (848 pass, 1 fails)"*. That figure is the
**pre-NEXT** count: it does not include the four NEXT-mission files
(`test_next_convergence_controller.py` 34, `test_next_autonomous_wiring.py` 17,
`test_progress_aware_recovery.py` 39, `test_multi_turn_productive_recovery.py` 50), and the §11
command block was never extended with them either. The measured set above is the current one.
Recorded rather than silently corrected, because §11 is the verification authority and a stale count
in it is the same defect class as the F45–F63 pointer in §5.1.

The full suite was **not** run: F36 says it cannot run in one process on this host. The method here
is weaker than a two-run intersection and is stated as such.

---

## 7. Honest limits

- **The page is a projection, and a projection can be faithful to a defect.** It reports that
  `INCOMPLETE`+`PASS` yields `GOAL_MET` because that is what the code does. It does not assert the
  code is right, and F-1 is the reason the distinction is stated explicitly rather than left to a
  reader's inference.
- **The pins are line-level, not content-level.** A pin asserts that a specific line exists and is
  non-blank; it does not assert that the line still *says* what the page claims. The matrix check
  (§4 property 2) is content-level for the one table where that matters most; the rest rely on the
  reader following the pin. A content-level check for all 37 would need the page to carry the
  expected text, which would make it a second copy of the code — the defect class this migration
  exists to remove.
- **"Cannot decide" is asserted, not proven.** The guard counts the claims; it cannot prove that a
  given authority *in fact* never decides the other's question. That is what the authority tables in
  ADR-0035 §Authority table and ADR-0042 exist for, and the page cites them rather than re-proving
  them.
- **F-1 and F-2 are findings, not repairs.** Nothing was changed in `goal.py`. The differential is
  reproducible; the interpretation is left open deliberately.
