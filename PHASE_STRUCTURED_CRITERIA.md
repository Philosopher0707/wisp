# PHASE — STRUCTURED CRITERIA (Deliverable 2)

**Deliverable:** **ADR-0050** — the objective may carry a declared criteria block; the host validates it
against the measurable surface and rejects rather than reinterprets. **Plus the implementation.**
**Baseline:** `dd21f6d` + `3298894` (Deliverable 1). **Predecessor:** `PHASE_CRITERIA_DERIVATION_AUTHORITY.md`.

---

## 1. What was produced

| Artefact | What it is |
|---|---|
| **ADR-0050** in `WISP_ARCHITECTURE_DECISIONS.md` (49 → 50) | Decides what ADR-0048 R6 deferred — the grammar, the placement, the validation surface, the rejection behaviour, the flag. R1–R8, the MODE A/B table, six rejected alternatives. Index row added. |
| `wisp/core/convergence.py` | `CriteriaDeclarationRejected`, `CriteriaDeclaration`, `parse_criteria_declaration()`, `DerivationReason.DECLARED`, and `explain_acceptance(..., use_declaration=)`. `derive_acceptance` and `criteria_for` **untouched**. |
| `wisp/autonomous.py` | `STRUCTURED_DECLARATION_ENV` + `_structured_declaration_enabled()`, `_record_declaration_rejection()`, the wiring, and the rejection path. |
| `tests/reliability/test_structured_criteria.py` | **53 tests** — the grammar, the validation surface, the derivation, MODE A/B on both paths, the flag, the no-model-channel property, and the **wiring** (F67's lesson). |
| `.workbuddy-ai/memory/post-m13-structured/corpus.py` | The objective corpus and the classifier measurement. |

---

## 2. The decision

> **The declaration is a fenced block at the head of the objective, introduced by `--- criteria ---` and
> closed by `--- /criteria ---`, containing one YAML-shaped line per criterion of the form
> `<kind>: <spec>`. `<kind>` is one of exactly two: `command_succeeds` or `symbol_defined`. The host
> validates each spec against the measurable surface; a declaration that names an unmeasurable spec is
> **rejected** with `CriteriaDeclarationRejected`, surfaced to the caller and journalled, and a rejected
> declaration does **not** fall back to the prose grammar. `WISP_CRITERIA_STRUCTURED_DECLARATION` gates
> the path and defaults **OFF**, in which case the derivation runs exactly as it does today.**

| Rule | Substance |
|---|---|
| **R1** placement | The block must be **at the head**. An objective that *discusses* declarations must not accidentally carry one — and a reader must see that the block governs the whole objective |
| **R2** grammar | Closed: two kinds; `#` comments and blank lines allowed; an unknown kind and a malformed line are **rejected**, not skipped |
| **R3** validation | `command_succeeds` iff `argv[0]` resolves (workspace-relative executable, or on `PATH`). `symbol_defined` iff `<path>::<symbol>` is inside the workspace, the file exists, and the symbol is an identifier. **The host validates runnability, never outcome** |
| **R4** rejection | `CriteriaDeclarationRejected` raised, journalled, and the derivation **stops**. Five shapes: unterminated, empty, unknown-kind, malformed-line, unmeasurable-spec |
| **R5** precedence | `DECLARED` is a fourth derivation outcome and **precedes** the inference — the prose grammar is not consulted |
| **R6** no model channel | The objective is authored by the **caller**; the host validates; the harness measures. Structural, not promised |
| **R7** optional | No block → today's behaviour, unchanged |
| **R8** flag | `WISP_CRITERIA_STRUCTURED_DECLARATION`, default **OFF**, read once at the composition point |

**Why a hand-rolled parser when `pyyaml` is declared and available.** A general YAML parser accepts
sequences, nested maps, anchors and non-string scalars — and **every shape it accepts is a shape the host
must then interpret**. A closed grammar that rejects what it does not understand cannot silently
reinterpret, which is R4's whole point. (`pyyaml` **is** in `pyproject.toml` and `uv.lock` — verified, not
assumed — so this is a design choice rather than a constraint, and the ADR says so.)

---

## 3. The corpus, and the measurement ADR-0048's reversal condition needs

ADR-0048's reversal condition is *"a measurement shows that a `UNDETERMINED` classification is reached for
an objective whose requirement WAS determinable from its own words"* — and it recorded that **the
classifier's error rates were unknown**. They are no longer.

**The corpus** (`corpus.py`) is collected **by AST**, not by regex: every string literal the repository
passes as an objective to `derive_acceptance` / `explain_acceptance` / `converge_on_objective`, plus every
module-level `*_OBJECTIVE` constant, plus the four benchmark prompts. **13 distinct objectives**, of which
**11 are hand-labelled** as decidable (one is labelled *"states nothing"*, one is unlabelled).

Measured on a **red** baseline — which is load-bearing: with no baseline `criteria_for` makes the absolute
criterion required for *every* objective, so nothing discriminates:

| | |
|---|---|
| required when it should be | **4** |
| required when it should **not** (MODE B) | **1** |
| **not** required when it should be (**MODE A**) | **1** |
| correctly not required | **5** |
| **accuracy on labelled, decidable objectives** | **9/11 = 82%** |

The two errors, named:

- **MODE A — `FIX_BUG`**, this repository's own benchmark task. `UNDETERMINED`, so on a red baseline the
  absolute criterion is advisory and a no-op satisfies the guards. **The classifier is not wrong about the
  objective: the objective is incomplete, and there is no way for it to say so.**
- **MODE B — the prohibition** (*"Do not make the tests pass by editing them"*), read as a requirement.

### The finding that refines ADR-0048's reversal condition

**The classifier's answer is a function of `(objective, workspace)`, not of the objective alone.** A
symbol criterion is derivable only when the named file **exists**, so three objectives move from
`UNDETERMINED` to `UNSTATED` when measured against a workspace containing their fixture file:

| Objective | repo as workspace | fixture workspace |
|---|---|---|
| *"Add a function shout(name) to strings_util.py…"* (×2) | `undetermined` | **`unstated`** |
| *"In strings_util.py there is a greet() function…"* | `undetermined` | **`unstated`** |

`UNSTATED` is the **correct** answer for all three — the objective asks for a function, and the suite is not
part of what it states — and all three are truth-`False`, so all three are *correct* verdicts. **Corrected
for each objective's own workspace the accuracy is 12/14 = 86%.** ADR-0048's reversal condition is
therefore under-specified: *"determinable from its own words"* is not a property of the words.

---

## 4. What closes, and what does not

| Mode | Without a declaration | With a valid declaration |
|---|---|---|
| **A** — a required suite silently advisory | **unchanged — still reachable** | **CLOSED.** `DECLARED` makes the suite criterion **required**, so a no-op cannot pass |
| **B** — a prohibition read as a requirement | **unchanged** | **CLOSED, structurally.** The prose is not consulted, so a prohibition inside it cannot be read as a requirement |

**The trade, stated plainly: the declaration does not make the classifier better, it makes the objective
complete.** An objective that states its conditions gets them measured; one that does not keeps today's
behaviour and today's defect. That is why the flag defaults OFF, and both `DEFECT-PIN` classes in
`test_criteria_derivation_authority.py` still pass — with their docstrings updated to record the closure
and to point at `test_structured_criteria.py::TestModeA` / `TestModeB`, where the declared path is driven.
**The pins were updated with the reasoning, not deleted.**

---

## 5. Falsification

Eight probes, each restoring the tree byte-identical (sha256 verified), controls green either side:

| Probe | Caught by |
|---|---|
| P1 a rejected declaration silently falls back to the prose grammar | ✅ 2 tests |
| P2 the declared criterion is not promoted (MODE A reopens) | ✅ *(after the gap below was closed)* |
| P3 the declared path is skipped entirely | ✅ 7 tests |
| P4 the flag defaults ON | ✅ 6 tests |
| P5 the command-runnability validation is dropped | ✅ 1 test |
| P6 the workspace containment check is dropped | ✅ 2 tests |
| P7 the `DECLARED` reason label is wrong | ✅ 3 tests |
| CONTROL, before and after | ✅ green |

### P2 did not falsify on the first attempt — and the reason is a real test defect

Un-promoting the declared criterion (`promote_absolute=False`) was **not caught**. The cause: my red
baseline was keyed **`verify:cmd0`**, but a declared spec's criteria id is **`declared:cmd0`** — so
`criteria_for` saw `base is None` and made the absolute criterion **required regardless of
`promote_absolute`**. **The test was not measuring the promotion at all**; it passed for a reason unrelated
to what it claimed.

This matters beyond the test, because in **production** the baseline *does* cover the declared id: the
first `explain_acceptance` call produces the declared specs, the probe measures *those* specs, and the
second call closes the criteria over a baseline keyed `declared:cmdN`. So the mutation would have reopened
MODE A in production while the suite stayed green.

Closed by making the baseline's id a parameter (`_red("declared:cmd0")`) and pointing the two declared-path
tests at a baseline that **covers the id under test**. The lesson is the phase's own: *a probe that does
not falsify means the test is not testing what you think* — and here the probe found a test whose fixture
made its assertion vacuous.

### A tripwire fired, and it was the tripwire that was wrong

The full regression turned up a **second failure**: `TestR6NoModelChannel::test_no_function_takes_criteria_from_model_output`
— the ADR-0045 R1 tripwire, firing on the new `use_declaration` parameter. **It fired correctly and its
verdict was wrong.**

`use_declaration: bool` is a **boolean switch**; it cannot carry criteria. But the tripwire asserted
**set equality** on `explain_acceptance`'s parameters, so *any* new parameter tripped it regardless of
type — it checked *which* parameters exist, not *whether any can carry criteria*. It was the third
instance in this mission of a guard that had pinned a **state** rather than a **property** (the page guard
in Deliverable 1 was the first, and the same class again).

The first rewrite replaced set equality with an explicit allow-list. A probe then showed that a **harmless**
`verbose: bool` also tripped it — which is worse, not better: a check that fires on every innocuous
addition trains the reader to extend the list without thinking, defeating the check. So it was rewritten a
second time to test the two things that actually make a channel:

| Probe | Result |
|---|---|
| **T1** a parameter annotated `tuple[AcceptanceCriteria, ...]` | **caught** |
| **T2** a parameter named `declared_specs`, unannotated | **caught** |
| **T3** a harmless `verbose: bool` | **green** — no longer a false alarm |

**The residual is stated in the test**: a parameter typed `Any` under an innocuous name could still carry
criteria, and no inspection of a signature can rule that out.

---

## 6. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <39 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **After Deliverable 1** | 1143 tests — 1142 passed, 1 failed |
| **After Deliverable 2** | **1197 tests — 1196 passed, 1 failed** (110.16 s) |
| **The one failure, both runs** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** — the failure set is identical in both directions |
| **Now-passing** | none |
| **Delta** | **+54** — 53 in `test_structured_criteria.py` and 1 net in the criteria file (the tripwire above, rewritten and given a non-vacuity test) |

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

**Gates, measured not asserted** (F71): `ruff check wisp/` → **11 errors**, unchanged by this phase; the
pinned `mypy` → **1844 errors in 228 files**, unchanged. This phase introduces no new error in either
(three unused imports in the new test file were found by ruff and removed).

---

## 7. Honest limits

- **The flag defaults OFF, so MODE A is still reachable in production.** The declared path is a new
  *source*, not a new *behaviour*; nothing in the default configuration parses a declaration.
- **A declaration can be wrong about the objective.** The host validates *measurability*, not *intent*. An
  objective declaring a command that always exits 0 is well-formed and measures nothing. The mismatch is
  reviewable — the declaration is in the objective and recorded in the derivation — but the host cannot
  detect it.
- **The corpus is small.** 13 objectives, 11 labelled, one workspace each. The 82% / 86% figures are a
  *measurement*, not an estimate, and they are **not** a confidence interval. A larger corpus could move
  them; the instrument is committed so it can be re-run.
- **`DECLARED` short-circuits the prose, including prose that states a *stronger* requirement.** An
  objective whose declaration is weaker than its prose is measured against the declaration. That is the
  intended reading of "the objective has said", but it is a way to under-specify.
- **`CriteriaDeclarationRejected` is a new exception on a path that could not previously raise.** Callers
  that do not expect it will see a traceback. That is deliberate (R4 — loud, never a fallback), and the
  flag's default protects existing callers.
- **The wiring test drives a stub runtime, not a provider.** `TestTheWiring` exercises
  `converge_on_objective` end to end for the derivation and the rejection path — the F67 defect class —
  but not a real model, a real tool call, or a multi-attempt recovery. It covers *crashing*, not *being
  wrong*.
- **The two flags are independent and their interaction is undecided.** `WISP_CRITERIA_STRICT_DERIVATION`
  and `WISP_CRITERIA_STRUCTURED_DECLARATION` compose trivially today (a declared objective never reaches
  `UNDETERMINED`, so strict mode has nothing to withhold), but no ADR states that they *should* compose
  that way. ADR-0050's follow-up question 1 records it.
