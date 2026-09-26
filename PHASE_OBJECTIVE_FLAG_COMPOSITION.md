# Phase — The objective-path two-flag composition

**Repository:** `/Users/philosopher/iCloud Drive (Archive)/Documents/wisp`
**Baseline:** `HEAD` = `7bb8f8a` (the ADR-0055 landing). Working tree = the 29 WIP entries of
`CONTEXT.md` §8 — **no drift**.
**Decision:** **ADR-0056** — the two criteria flags are **independent** on the objective path; the one
interaction that exists is **derivation order**, not flag coupling.
**Change:** an ADR, a guard, and a committed probe. **No production behaviour moves** — the measured
behaviour already *is* the decision.

---

## 1. The question, and why its precedent did not settle it

ADR-0050's follow-up 1:

> *"Should the derivation require a declaration when the objective names a verification command but
> states no requirement? … **The two flags are independent today; whether they should compose is not
> decided here.**"*

ADR-0053 R8 had already answered the same question for the **turn** path — **independent** — with a
specific reason:

> *"Coupling them would make the turn path's behaviour depend on a flag read at a different composition
> point — two read sites for one concern."*

**That reason does not transfer.** The objective path reads both flags at **one** composition point:

```
wisp/autonomous.py:307   strict          = _strict_derivation_enabled()
wisp/autonomous.py:308   use_declaration = _structured_declaration_enabled()
wisp/autonomous.py:318   explain_acceptance(objective_text, workspace,
                                            strict=strict, use_declaration=use_declaration)
```

One read site, two parameters. So the question is whether the **decision** transfers when its reason
does not.

---

## 2. The measurement — the 2×2 matrix, driven

`explain_acceptance` on this repository's own MODE A objective (ADR-0048's defect-pin) on a red
baseline:

| objective | `strict` | `use_declaration` | reason(s) | `undetermined` | criteria |
|---|---|---|---|---|---|
| no declaration | F | F | `undetermined` | `[]` | 3 |
| no declaration | F | **T** | `undetermined` | `[]` | 3 |
| no declaration | **T** | F | `undetermined` | **`['verify:cmd0']`** | 4 |
| no declaration | **T** | **T** | `undetermined` | **`['verify:cmd0']`** | 4 |
| declared | F | F | `undetermined` | `[]` | 3 |
| declared | F | **T** | **`declared`** | `[]` | 1 |
| declared | **T** | F | `undetermined` | `['verify:cmd0']` | 4 |
| declared | **T** | **T** | **`declared`** | `[]` | 1 |

Four facts, each read off the table:

1. **`strict` alone changes behaviour** (row 3 vs row 1): adds `verify:cmd0:requirement_declared`.
2. **`use_declaration` alone changes behaviour** (row 6): the criteria collapse to `declared:symbol0`,
   reason `DECLARED`.
3. **A declaration pre-empts `strict`** (row 8 == row 6): `undetermined` is empty either way. The early
   return at `convergence.py:1029-1041` — a declaration is authoritative, so the prose grammar is never
   consulted and the `UNDETERMINED` machinery never runs. `strict` is **recorded and inert**.
4. **`use_declaration=True` without a block is a no-op** (row 4 == row 3).

---

## 3. The decision

> **R1 Independent.** Each flag gates its own parameter of `explain_acceptance`, read once at
> `autonomous.py:307-308`. Neither implies the other.
> **R2 The interaction is derivation order**, and it is a property of `explain_acceptance`, not of the
> flags: a declaration is authoritative, so `strict` has nothing to withhold.
> **R3 "Dependent" rejected on measurement** — `strict` alone withholds (fact 1), so making it
> conditional on the declaration flag would silently disable ADR-0048's fix for the measured false
> `GOAL_MET`.
> **R4 "Composed" rejected, and not implemented** — requiring a declaration is a new policy no
> measurement supports, it would turn a silent-defect fix into a hard refusal for every objective
> written before ADR-0050, and fact 4 shows the code does not do it.
> **R5 `explain_acceptance`'s parameters are not widened** (ADR-0009): composition is a *policy* at the
> composition point, not a third parameter.
> **R6 `derive_acceptance` is unaffected** — frozen signature, `strict=False`, never `use_declaration`.
> **R7 The relationship to ADR-0053 R8**: the *reason* does not transfer, the *decision* does — and for
> a stronger reason, since the two flags gate different parameters, so coupling would not remove a read
> site; it would add a rule.
> **R8 The non-violations are asserted by tests.**
> **R9 Rollback**: none needed — no production change.

---

## 4. The guard

`tests/reliability/test_objective_flag_composition.py` — **7 tests**:

| Test | Property | Breaks if |
|---|---|---|
| `test_strict_alone_withholds_on_the_prose_path` | R3 — `strict` is independent | `strict` becomes conditional on the declaration flag |
| `test_declaration_alone_changes_the_derivation` | R1 — `use_declaration` alone is not inert | a declaration stops short-circuiting the grammar |
| `test_a_declaration_pre_empts_strict` | **R2 — the normative reading** | the declaration path starts producing `undetermined` entries |
| `test_use_declaration_without_a_block_is_a_no_op` | R4 — the composed reading is not implemented | enabling declarations starts refusing undeclared objectives |
| `test_explain_acceptance_signature_is_not_widened` | R5 | a third parameter appears |
| `test_derive_acceptance_is_unaffected` | R6 | `derive_acceptance` starts consulting a declaration |
| `test_both_flags_are_read_once_at_one_composition_point` | R1's shape — **AST-parsed** | a flag read moves, or one is dropped |

The AST test pins the *shape*: one composition point, two reads within two lines, both passed as
separate keywords to `explain_acceptance`. A string scan would read the comments that describe the flags.

---

## 5. Verification

```
the new guard                              7 passed
non-vacuity probes                         4/4 CAUGHT, tree restored byte-identical
ADR-0048's MODE A / MODE B defect-pins
  + the structured-criteria + gate guards  145 passed
regression (44 files)                      1269 tests — 1268 passed, 1 failed
                                           (F38, pre-existing)
ruff (the new file)                        All checks passed
ruff check wisp/                           11 errors — unchanged (F71)
```

**The non-vacuity probes:**

| # | Mutation | Test it must fail | Result |
|---|---|---|---|
| NV1 | `strict` becomes dependent on the declaration flag | `test_strict_alone_withholds_on_the_prose_path` | **CAUGHT** |
| NV2 | a declaration stops pre-empting `strict` | `test_a_declaration_pre_empts_strict` | **CAUGHT** |
| NV3 | `explain_acceptance`'s signature is widened | `test_explain_acceptance_signature_is_not_widened` | **CAUGHT** |
| NV4 | the composition point stops reading the declaration flag | `test_both_flags_are_read_once_at_one_composition_point` | **CAUGHT** |

`__pycache__` purged on both sides of every probe; each file restored and verified byte-identical by
sha256.

**The flag's declared effect is unchanged.** `AGENTS.md` says `WISP_CRITERIA_STRICT_DERIVATION` *"lets
the acceptance-criteria derivation decline to complete an objective whose requirement it could not
determine"* — measured, that is still exactly what it does, and it does it **without** the declaration
flag. The composition fact is added to the table, not a correction to it.

---

## 6. Findings

**F82 — the two "canonical" test blocks listed different file sets.** Found while computing this
deliverable's count, not while looking for it. `AGENTS.md`'s block and `CONTEXT.md` §11's block both
describe themselves as *the* canonical suite, and until this phase they **differed**:

| Block | files | extra |
|---|---|---|
| `AGENTS.md` | 43 | `test_precedence_canonical.py`, `test_structured_criteria.py` |
| `CONTEXT.md` §11 | 41 | — |

So the two headings' counts described different suites, and **neither was the intersection of the two
runs** the method calls for. The two files are guard files from the precedence and structured-criteria
phases — they were added to `AGENTS.md`'s block and never to §11's. **Both blocks now list the same 43
files**, and both headings carry the count measured from that block (**1294 tests — 1293 pass, 1
fails**). This is F71's class one level up: F71 said *"never quote a count from prose"*; F82 says **a
count is only canonical if there is one block.**

**No instrument defect this deliverable.** All four non-vacuity probes falsified on the first run, and
the measurement agreed with the code. Recorded as such rather than padded — a phase that finds nothing
of that kind is allowed to say so.

**The brief's framing was right and its precedent was not.** The brief said *"The precedent the ADR must
reason against"* and correctly identified ADR-0053 R8. What it did not state — and what the ADR had to
establish — is that **R8's reason does not transfer** (two read sites there, one here). Treating R8 as
directly applicable would have produced an ADR whose argument did not reach its own subject.

---

## 7. What this does not decide

- **Not** whether objectives *must* declare. That is a policy with its own evidence, and it is ADR-0056's
  named reversal condition.
- **Not** the residual: `CriteriaDerivation.strict` records `True` when the declaration path pre-empted
  it, so the boolean cannot distinguish *"strict acted"* from *"strict had nothing to act on"*. The
  `reasons` tuple does distinguish them (the reason is `DECLARED`, not `UNDETERMINED`). Making the
  boolean explicit is a record change and its own decision.
- **Not** the turn path's composition — decided by ADR-0053 R8 and unchanged.
