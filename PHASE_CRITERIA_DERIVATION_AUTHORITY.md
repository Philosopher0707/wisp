# PHASE — CRITERIA DERIVATION AUTHORITY (Deliverable 2)

**Deliverable:** `ADR-0048` — the authority over *"does this objective require a green suite?"*
**Type:** decision, append-only. **Baseline:** `af3a89a` + `802413a` (Deliverable 1).

---

## 1. What was produced

| Artefact | What it is |
|---|---|
| **ADR-0048** in `WISP_ARCHITECTURE_DECISIONS.md` (47 → 48) | The decision: R1–R7, the behaviour-change table, four rejected alternatives, risks, the reversal condition, four follow-up questions. Index row added. |
| `tests/reliability/test_criteria_derivation_authority.py` | **55 tests** — the three failure modes reproduced, the seven rules held, non-vacuity. |
| `wisp/core/convergence.py` | `DerivationReason` (3), `CriteriaDerivation`, `explain_acceptance()`, `Objective.derivation`, `_write_derivation()`. `derive_acceptance` and `criteria_for` **unchanged**. |
| `wisp/autonomous.py` | `STRICT_DERIVATION_ENV` + `_strict_derivation_enabled()`, the derivation call and its journal wiring. |
| `AGENTS.md` | The flag row, the authority section, the module-map rows for the three ADR-0045/0046/0048 modules, the extended test block. |

**The decision in one line:** the objective is the authority over what is required; the host owns the
*derivation* and the *validation*, never the *invention*; where the objective is silent the host may infer
"no regression" and may not infer "green"; and where the host cannot tell whether the objective is silent
or merely unparsed, it must **say so**, and the answer is `INCONCLUSIVE`.

---

## 2. The evidence, before deciding

### 2.1 The survey — every objective shape in the live runs and benchmarks

Driven through the real `derive_acceptance` (`.workbuddy-ai/memory/post-m13-criteria-authority/survey.py`),
across eight objectives × two workspaces × three baselines:

| Objective | `_WANTS_FIX_RE` | red baseline: required criteria |
|---|---|---|
| `bench/CREATE_FUNCTION` — *"Add a function shout(name)…"* | **no match** | `no_regression`, `inputs_unchanged`, `symbol:shout` |
| `bench/FIX_BUG` — *"Fix the bug in totals.py"* | **no match** | `no_regression`, `inputs_unchanged` — **guards only** |
| `bench/JSON_EDIT` | **no match** | (bare workspace → none) |
| `bench/SUBAGENT_DELEGATE` | **no match** | (bare workspace → none) |
| `live/progress-positive` — *"Fix the failing test suite…"* | match `'Fix the failing'` | `verify:cmd0`, both guards |
| `live/progress-negative` | match | same |
| `live/recovery-positive` | match | same |
| `live/recovery-hard` | match | same |

Two facts the survey settles: **the four live objectives all promote** (the ADR-0047 R5 widening works
for them), and **none of the four benchmark tasks does** — including `FIX_BUG`, whose own verifier runs
`sum_to(5) == 15`. That is the false-negative that becomes MODE A.

### 2.2 The three failure modes, reproduced end to end

`three_modes.py`, through `derive_acceptance` → `criteria_for` → `acceptance.evaluate` →
`goal.derive_goal_state`:

**MODE A — a false `GOAL_MET`.** On a red baseline, with the objective being this repository's **own
benchmark task** and the attempt changing **nothing**:

```
_WANTS_FIX_RE -> NO MATCH
criteria     -> advisory  verify:cmd0                          `python -m pytest tests/ -x -q` exits 0
                REQUIRED  verify:cmd0:no_regression            ... no more failures than the baseline (1)
                REQUIRED  verify:cmd0:inputs_unchanged         ... its inputs are unchanged
verdict      -> pass        reason=['ALL_REQUIRED_SATISFIED']
goal state   -> goal_met
```

The bug is unfixed, the suite still fails, and the objective is reported met. This is F37's shape (a false
success) arriving through the criteria rather than through the evidence adapter.

**MODE B — a false exhaustion.** `_WANTS_FIX_RE` matches `'make the tests'` inside *"**Do not** make the
tests pass by editing them"*. The absolute criterion is promoted, the objective never asked for a green
suite, and the run ends `fail` → `goal_failed`. Other measured false positives: *"Make the linter pass"*,
*"CI will pass without any test changes"*.

**MODE C — "I cannot tell".** No declared toolchain → no specs → no criteria →
`inconclusive` / `NO_REQUIRED_CRITERIA` → `goal_unverified`. **Already honest**: `evaluate` rule 1 and
`derive_goal_state` already refuse to turn "nothing was checked" into a pass. Its cost is that `GOAL_MET`
is unreachable, so the objective can never complete.

**The distinction the code could not make.** MODE A and MODE C produce *opposite* outcomes from the same
input state — the host could not determine what the objective requires. MODE A reads that as "nothing is
required, so a no-op passes"; MODE C reads it as "nothing is required, so nothing is verified". Both cannot
be right, and the corpus had already decided which: ADR-0035 invariant 1, ADR-0042 and ADR-0045 R4 all say
`INCONCLUSIVE` must not be collapsed. **MODE C is the established behaviour; MODE A is the collapse.**

### 2.3 The tests reproduce each shape

`tests/reliability/test_criteria_derivation_authority.py` — `TestModeAFalseGoalMet`,
`TestModeBFalsePromotion`, `TestModeCNoCriteria`. Each drives the real chain and asserts the shape as it
is. MODE A and MODE B are marked **`DEFECT-PIN`** in their docstrings: they characterise a defect, and
when the derivation is fixed *these* go red — the newly-red set is the bug's documentation (the repo's own
rule). Two of them assert the closure explicitly, so the fix and the defect are pinned side by side.

---

## 3. The decision, and its three answers

**Q1 — What is the authority?** The **objective's own stated conditions**. The host derives; it does not
invent. R1 gives the derivation three outcomes rather than two, because a closed grammar over prose has a
third answer — *"I cannot tell"* — and collapsing it either way is a defect the ADR measures.

**Q2 — Can `derive_acceptance` say "I cannot tell", and what is the conservative branch?** It can, and the
branch is `UNDETERMINED`. Under strict derivation it contributes a **required criterion the harness cannot
evidence**, which `acceptance.evaluate`'s **existing rule 3** turns into `INCONCLUSIVE` — **no new verdict
vocabulary, no new rule, and no `FAIL`**. `GOAL_MET` on a guards-only set still means *"nothing got worse"*
(ADR-0047 R5, honest); what changes is that the host may no longer reach that outcome by **silently**
declining to ask the question. The qualifier that makes it safe: strict mode withholds **only** where the
absolute criterion is **advisory** — i.e. only where the derivation actually made a choice. On a green
baseline `criteria_for` requires it anyway, so nothing is withheld.

**Q3 — Is a structured-criteria path viable under ADR-0045 R1?** **Yes, and the boundary is named.** R1
forbids the **model** declaring criteria. An objective carrying a machine-checkable declaration, **validated
by the host** against the measurable surface and **failing closed** on anything unmeasurable, keeps the exam
with the user and the grading with the host; the model gains no channel. **The trade is named**: a new input
surface is a new way to be wrong, and a malformed declaration must be rejected **loudly** — a silent
downgrade *is* MODE A. The ADR **decides the path is viable and fixes its boundary**; it does not implement
it, and says so.

**The residual, stated not hidden.** R7: **negation is not handled.** *"Do not make the tests pass"* still
promotes. The record makes it *visible* — the cited span sits inside a prohibition a reader can see — but
the grammar was deliberately not widened again, because widening it is the mitigation ADR-0047 R5 already
tried and this phase measures as insufficient.

---

## 4. What changed in production

| File | Change | Behaviour |
|---|---|---|
| `wisp/core/convergence.py` | `DerivationReason`, `CriteriaDerivation`, `explain_acceptance`, `Objective.derivation`, `_write_derivation` | **none** with the flag off; `UNDETERMINED` + advisory → `INCONCLUSIVE` with the flag on |
| `wisp/autonomous.py` | the flag helper; the derivation call; the derivation record | read at the composition point, not inside the pure function |

**`derive_acceptance`'s signature is frozen** (ADR-0009) — it is a thin caller of
`explain_acceptance(..., strict=False)`. **`criteria_for` is untouched**, so its ~40 call sites are
unaffected. Verified by test, not by inspection: `test_the_signature_is_untouched`,
`test_criteria_for_is_untouched`, `test_it_agrees_with_the_non_strict_explanation`.

`WISP_CRITERIA_STRICT_DERIVATION` defaults **OFF**. **Read once**, at `wisp/autonomous.py` — asserted by
`test_no_module_outside_the_composition_point_reads_the_flag`, because a flag read twice can disagree with
itself.

**One lint error was introduced and removed.** Replacing `derive_acceptance` with `explain_acceptance` in
`autonomous.py` orphaned the import; ruff caught it (`F401`) and it was removed. The final ruff set is
**identical to HEAD's** (11 errors, all pre-existing — see §6.2).

---

## 5. Falsification

Six mutation probes, each restoring the tree byte-identical (sha256 verified), with controls before and
after:

| Probe | Caught by |
|---|---|
| P1 strict mode stops withholding (MODE A reopens) | 3 tests |
| P2 the strict criterion becomes satisfiable (`check=lambda _: True`) | 1 test |
| P3 promote on every objective (MODE B) | 7 tests |
| P4 widen `derive_acceptance`'s signature | 1 test |
| P5 stop journalling the derivation | 2 tests |
| P6 make the flag default ON | 1 test |
| CONTROL, before and after | green |

**A design flaw was found by a test, not by review.** `test_a_green_baseline_still_completes_under_strict`
failed on the first run: the initial rule withheld the strict criterion for **every** `UNDETERMINED` spec,
including on a green baseline where `criteria_for` requires the absolute criterion anyway — so it blocked
an objective whose suite already passed. The fix is R2's **advisory** qualifier, and the test that found it
is the one that pins the blast radius. *This is the same shape as F63: the failing test was the finding.*

The probe harness again needed the **stale-`.pyc` purge** established in Deliverable 1 — P2 and P3 are
size-preserving edits, so a same-second restore leaves mutated bytecode cached.

---

## 6. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <36 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **After Deliverable 1** | 1035 tests — 1034 passed, 1 failed |
| **After Deliverable 2** | **1090 tests — 1089 passed, 1 failed** (105.94 s) |
| **The one failure, both runs** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** |
| **Now-passing** | none |
| **Delta** | **+55 tests, +55 passed** — exactly the new file |

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

### 6.1 Two stale figures in the verification authority, corrected

`CONTEXT.md` §11's heading read *"849 tests (848 pass, 1 fails)"* and its command block omitted the four
NEXT-mission files. `AGENTS.md`'s block read the same and is now **1090 / 1089**, with the six new files
added. Both were the **pre-NEXT** count; neither had been updated by the four phases that followed.
Recorded rather than silently corrected, because §11 is the verification authority and a stale count in it
is the same defect class as the §23 pointer in Deliverable 1's report.

### 6.2 `ruff check wisp/` is not green, and never was

`CONTEXT.md` §11 lists *"Gates (both must be green): `ruff check wisp/`"*. Measured at `af3a89a`:
**11 errors**, including an `F821` undefined name in `wisp/auth/principal.py` and
`wisp/context_assembler.py`. This change introduces **zero** new ones (verified by an `LC_ALL=C comm`
diff of the two error sets, files and codes). **Reported, not repaired** — 11 pre-existing lint failures
across unrelated modules are outside this deliverable's authority, and repairing them would sweep a
15-file diff into a documentation/decision commit.

---

## 7. Honest limits

- **The decision ships a mechanism that is OFF by default.** With the flag off, MODE A is **still
  reachable**. The ADR says so in its reversal condition. A reader who wants MODE A closed must turn the
  flag on — and must accept that an `UNDETERMINED` objective then stops completing until it is clarified.
- **The classifier's error rates are unmeasured.** `UNSTATED` is inferred from the presence of a
  `SymbolSpec`, which is a *proxy* for "the objective stated something checkable". An objective that names
  a symbol **and** requires a green suite without saying so lands in `UNSTATED` and keeps MODE A's shape.
  The reversal condition needs numbers this phase does not have; the three-mode probe is the instrument,
  and it needs a corpus of real objectives.
- **MODE B is unchanged, by decision.** The record makes the false promotion visible; it does not fix it.
  Stating that as R7 rather than leaving it unmentioned is the whole point.
- **The `UNDETERMINED` classification is reachable for a well-formed objective** whose wording the grammar
  does not know — `FIX_BUG` is exactly that. The record makes it visible; it does not make it right.
- **The structured path is decided, not implemented.** R6 fixes the boundary and names the trade; the
  grammar, the validation surface and the rejection behaviour are follow-up question 1.
- **The derivation record is read by nothing.** That is by design — it is a record — but it means the
  journal line's only consumer today is a human. Whether anything *should* act on it is not decided here.
