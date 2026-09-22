# PHASE P3 REPORT — Independent Verification

| Field | Value |
|---|---|
| Phase | **P3** |
| Baseline | P2 complete (`WISP_MIGRATION_STATUS.md`) |
| Status | **`COMPLETE — stage 3a only`** (the plan prescribes two stages; 3b is a separate decision) |
| Files changed | 4 modified, 1 added (`wisp/core/acceptance.py`) |
| Tests added | `tests/test_acceptance_verdict.py` (60) |
| Rollback | `WISP_RECORD_VERDICT` (defaults **off**) |

---

## 1. Executive summary

P3 makes "this succeeded" a **verdict against criteria backed by evidence**, produced by something
that is not the thing that acted.

The plan rates this **"High — the highest in the plan"** because it changes the completion semantics of
the product: a stricter gate can make previously-"successful" turns report `INCONCLUSIVE`. It
prescribes shipping in two stages, and this phase follows that exactly:

- **3a (shipped):** criteria, evidence, and verdict are introduced and **recorded**. Nothing on the
  completion path consumes them.
- **3b (not shipped):** enable the gate behind a flag, after a measurement period showing how many
  turns become `INCONCLUSIVE`.

**Both existing vocabularies were examined and neither could express the required verdict** — that is
the finding that shapes the design:

| Existing | Why it cannot |
|---|---|
| `VerificationFloorGuard.resolved()` (`core/verification.py:193`) | a boolean: `wrote_code and verify_ok_after_edit is True`. It cannot say "I could not tell", and it is the **actor's own** bookkeeping |
| `VerificationResult.decision` (`graph/verifier.py:19`) | `("ALLOW","REJECT","RETRY","ESCALATE")` — a **router's** vocabulary; every value presumes a verdict was reached |

So the verdict vocabulary is new and total — `PASS` / `FAIL` / `INCONCLUSIVE` — and routing is
**derived** from it (`route_for()`), keeping the graph vocabulary as an output rather than a
competitor.

---

## 2. Implementation

Four files modified, one added.

| File | Change |
|---|---|
| `wisp/core/acceptance.py` | **new** — `Verdict`, `CriterionKind`, `AcceptanceCriteria`, `Evidence`, `CompletionVerdict`, `evaluate()`, `invalidate()`, `content_digest()`, `route_for()` |
| `wisp/core/verification.py` | **retain-and-demote**: `floor_guard_criteria/evidence/verdict()` project the guard onto the acceptance model. Read-only; the guard's behaviour is untouched |
| `wisp/core/stateless.py` | one additive line: `self._last_guard = guard` |
| `wisp/core/runtime.py` | records a `VERDICT` event at turn end, behind `record_verdict` |
| `wisp/core/session.py` | `VERDICT` event kind — **audit-only** |
| `wisp/config.py` | `record_verdict` flag (env `WISP_RECORD_VERDICT`), default **`false`** |
| `tests/test_acceptance_verdict.py` | **new** — 60 tests |

### 2.1 The verdict algebra

| Rule | Outcome |
|---|---|
| No **required** criteria | `INCONCLUSIVE` — a task with nothing to check has not been verified, it has been un-examined |
| A failing `DETERMINISTIC` criterion | `FAIL`, **short-circuiting** — deterministic checks are cheap and decisive, so semantic evaluation after one fails buys nothing |
| A required criterion with **no valid evidence** | `INCONCLUSIVE` — absence of evidence is not evidence of failure |
| A required criterion whose evidence is **invalidated** | `INCONCLUSIVE` — an invalidated observation is indistinguishable from no observation |
| Otherwise | `PASS` |

Advisory (`required=False`) criteria are evaluated and reported but can neither pass nor block.

### 2.2 The floor guard is retained, not replaced

`VerificationFloorGuard` keeps its exact behaviour — `resolved()`, `rejection()`, the grind floor, and
the **invalidate-on-mutation** property the plan explicitly requires be preserved
(`core/verification.py:149`). It is *projected* onto the acceptance model as **one deterministic
criterion**, not the whole rule. `TestFloorGuardRetained` pins all of it, including that the projection
does not mutate the guard.

### 2.3 Evidence is content-addressed, with provenance

Generalizes `graph/types.py::GraphArtifact` (already hash-verified, already carrying a `producer`).
Every evidence cites at least one raw observation — an evidence record citing nothing is an assertion.
`invalidated_at` makes the invalidate-on-mutation property expressible for evidence generally.

---

## 3. Two design decisions worth naming

### 3.1 The floor criterion is an implication, not a predicate (ADR-0017)

The obvious projection is `check=lambda _: guard.resolved()`. That reports **`FAIL` for a turn that
mutated nothing** — a claim the evidence does not support, and one the guard itself never makes
(`rejection()` returns `None` when `wrote_code` is `False`).

Written as the implication it actually is — `(not guard.wrote_code) or guard.resolved()` — the four
cases become distinct and each is pinned:

| Turn | Verdict |
|---|---|
| mutated nothing | `INCONCLUSIVE` (criterion vacuously holds; no evidence exists) |
| mutated, unverified | `FAIL` |
| mutated, verified | `PASS` |
| mutated again after verifying | `FAIL` (invalidate-on-mutation) |

### 3.2 The engine publishes its guard; the runtime only reads it (ADR-0018)

The guard lives in `stateless.py`; the journal is written by `runtime.py`. `AGENTS.md` states
*"Stateless core — `WispAgentCore` has no mutable state."* Three options were weighed:

| Option | Verdict |
|---|---|
| Runtime re-derives `wrote_code` / `verify_ok_after_edit` from observed events | **Rejected** — a second implementation of the floor rule, i.e. a second authority for "was this verified" |
| Engine yields the verdict as a new event type | **Rejected** — changes the event stream every transport consumes, for an audit record |
| Engine publishes the guard; runtime reads it | **Chosen** |

The exception is narrow and its limits are documented at the assignment: it is not session state, it is
race-free in practice (cores are cached per session, same-session turns are serialized by the session
lock), and it keeps **one** floor implementation.

---

## 4. Verification

### 4.1 New tests — 60, all passing

The plan requires seven assertions; each keeps the plan's name so it stays greppable.

| Required | Class | Proves |
|---|---|---|
| `test_criteria_required_gate` | `TestCriteriaRequiredGate` (6) | no criteria → `INCONCLUSIVE`, never `PASS`; advisory-only is still `INCONCLUSIVE` |
| `test_false_success_impossible` | `TestFalseSuccessImpossible` (7) | no `PASS` without valid evidence for every required criterion; evidence for the wrong criterion does not count |
| `test_verifier_independence` | `TestVerifierIndependence` (4) | `evaluate()` takes **no** transcript parameter; evidence names its producer; self-reported evidence is marked as such |
| `test_evidence_provenance` | `TestEvidenceProvenance` (9) | every evidence cites ≥1 observation; hashes verify, are order-insensitive, and change with the observation |
| `test_evidence_invalidation_cascades` | `TestEvidenceInvalidationCascades` (6) | invalidation demotes `PASS` → `INCONCLUSIVE`; returns copies, never mutates in place |
| `test_deterministic_first` | `TestDeterministicFirst` (7) | a failing deterministic check short-circuits — the semantic criterion is **never evaluated** |
| `test_floor_guard_retained` | `TestFloorGuardRetained` (8) | `resolved()`, `rejection()`, the grind floor and invalidate-on-mutation are all unchanged; the projection does not mutate the guard |

Plus `TestVerdictAlgebra` (5), `TestReachability` (5, RULE 11) and `TestStage3aDoesNotGate` (3).

### 4.2 Regression

| Run | Failures + errors |
|---|---|
| HEAD baseline | 131 |
| P0 / P1 / P2 final | 128 |
| P3 first run | 129 |
| **P3 rerun (identical code)** | **128** |

The `+1` was **flaky**, and that was established rather than assumed: the suspect test passes 5/5 in
isolation and 3/3 at file level, has **zero** coupling to anything P3 touched, and was absent from an
immediate rerun of the identical tree. `diff -q` against P2's failure set is **identical**. See
finding F17.

---

## 5. Honest limits

- **Stage 3b is not shipped.** No gate was enabled and no completion semantics changed. The plan
  requires a measured `INCONCLUSIVE` rate *before* enabling 3b, and that measurement has not been
  taken. This is the plan's own staging, not a shortcut.
- **The verdict cannot be measured in this environment.** `jsonschema` is missing (P0 F8), so no tool
  executes — meaning `wrote_code` is never set through a real turn here, and the recorded verdict is
  always `INCONCLUSIVE`. The 3b measurement therefore needs a working tool path first.
- **`test_verification_loop.py` has 5 pre-existing failures** (environmental), so the plan's criterion
  *"`test_verification_loop.py` and `test_verification_contract.py` pass unchanged"* holds only for the
  latter. `test_verification_contract.py` passes; `test_verification_loop.py` fails identically before
  and after.
- **`ruff`/`mypy` not installed.**
- **128 pre-existing failures remain.** P3's claim is exact: it adds none.

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| `INCONCLUSIVE` is a reachable, tested outcome | ✅ 60 tests; reachable end-to-end via `record_verdict` |
| A synthetic false-success scenario is blocked | ✅ `TestFalseSuccessImpossible` |
| The measured `INCONCLUSIVE` rate at 3b is reported before enabling | ❌ **not measured** — requires a working tool path (§5) |
| `test_verification_loop.py` and `test_verification_contract.py` pass unchanged | ⚠️ contract ✅; loop has 5 **pre-existing** failures (§5) |
| No regression | ✅ **0 new failures** (confirmed by rerun) |
| No existing test weakened | ✅ none touched |
| New code is reachable (RULE 11) | ✅ both call edges AST-pinned + end-to-end record test |
| Rollback by one flag | ✅ `WISP_RECORD_VERDICT`, default **off** |

---

## 7. Next phase

**P4 — Materialize a Task Graph from Durable State.** Its P2 prerequisite is met. Two P3 items remain
on the critical path:

1. **Measure the `INCONCLUSIVE` rate and decide 3b.** Blocked on a working tool path (`jsonschema`).
2. **Carried from P1:** journal-first reconstruction with blob fallback; killpoint integration.
3. **Carried from P2:** revisit ADR-0004 for the proposal path — a silently-lost authorization record
   is closer to a correctness precondition than an observability one. The same now applies to verdicts.
