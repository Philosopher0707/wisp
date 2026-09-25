# PHASE — F8 ERROR CLASSIFICATION (Deliverable 3)

**Deliverable:** F8's second half — a missing validator must be a **system** failure, not a schema verdict.
**Type:** mechanical, non-vacuous test. **Baseline:** `af3a89a` + `802413a` + `7023af0`.

---

## 1. The defect, and why it mattered

`WISP_MIGRATION_STATUS.md` F8's row states it plainly:

> *"The error-classification half is **NOT FIXED** (`SECONDARY_DEFECT_REMAINS`): the broad `except
> Exception` still reports a missing validator as an argument verdict."*

`_validate_tool_args` wrapped `import jsonschema` and `jsonschema.validate` in **one** `try`, so a
`ModuleNotFoundError` from the import and a `ValidationError` from the validator produced the **same
string** — `"Schema validation failed for tool '<name>': <exc>"` — and, at the two dry-run call sites, the
same denial status `SCHEMA_INVALID`.

The consequence is not cosmetic. When a missing capability is reported as a failure of the **input** rather
than of the **system**, every test downstream becomes a test of the wrong thing. Measured: **24** failures
attributed to "pre-existing" that were F8-caused, and six tests in `test_13h2_determinism.py` reporting the
wrong thing for the life of the repository. F37 and F38 are the same shape — a defect that was invisible
because a system failure was being reported as a data failure.

---

## 2. The fix

**One added handler, placed first.** `ModuleNotFoundError` is an `ImportError`, so a single clause covers
both the import raising and a `jsonschema`-internal import failure (a `$ref` resolver, say) — the brief's
*"absent, unimportable, or raises from its own code"*. A schema rejection cannot reach it: `ValidationError`
and `SchemaError` are not `ImportError`s.

```python
try:
    import jsonschema
    jsonschema.validate(instance=args, schema=schema)
    return None
except ImportError as exc:          # ← the fix. Must come FIRST.
    return _capability_missing(name, exc)
except Exception as exc:
    ...
    return _schema_invalid(name, exc)
```

**The failure carries its kind.** `ValidationFailure` is a `str` subclass with a `.kind`:

| Kind | Meaning |
|---|---|
| `SCHEMA_INVALID` | the validator **ran** and rejected the arguments — a **data** failure |
| `CAPABILITY_MISSING` | the validator **could not run** — a **system** failure |

A `str` subclass **on purpose**: `_validate_tool_args` returns `Optional[str]`; three call sites interpolate
the value into a message or an event payload, and one puts it in a JSON-serializable `data` field — so the
value has to *be* a string for every existing consumer. Subclassing keeps `isinstance(x, str)`, `if x:` and
f-string rendering working, and adds the one thing F8 needs. A dataclass is the obvious shape and the wrong
one: it would reach `{"status": "error", "data": <object>}` and stop being serializable.

**`SCHEMA_INVALID`'s literal now has one home.** `VALIDATION_SCHEMA_INVALID = DENIAL_SCHEMA_INVALID`,
imported from `core/events.py` — the same spelling the denial envelope publishes, not a second copy.

**The second door is closed too.** The recon recorded it: *"There is a second, identical import at `:2292`
inside the `write_file` retry — it fails the same way and is dead in this environment."* The retry block's
`import jsonschema as _js2` failed identically and its `except Exception: pass` swallowed it, so a
`write_file` call fell through to the schema verdict — a second route into the same defect. `test_a_write_file_call_is_not_laundered_by_the_retry_block`
pins it.

**Measured, both environments:**

| Environment | Input | Result |
|---|---|---|
| validator present | `read_file(path="a.txt")` | `None` — accepted |
| validator present | `read_file({})` | kind `SCHEMA_INVALID`, message **byte-identical** to the historical form, does not mention `jsonschema` |
| validator **absent** | `read_file(path="a.txt")` | kind `CAPABILITY_MISSING`, names the capability, says the arguments **were never checked**, and warns that re-issuing unchanged will fail identically |
| validator absent | `write_file(path=…, content=…)` | kind `CAPABILITY_MISSING` — not laundered by the retry |

---

## 3. Acceptance criteria

| Criterion | Result |
|---|---|
| A missing validator produces a system failure, not a schema verdict | ✅ `.kind == CAPABILITY_MISSING`, message names the capability and disclaims the arguments |
| A genuine schema rejection is unchanged | ✅ same prefix, same kind, same text; `str(rejection) == str(rejection_before)` measured |
| The test is non-vacuous (RED against the unmodified tree) | ✅ **8 of 21 fail** against unmodified behaviour — §5 |
| The AST of the surrounding function is otherwise unchanged | ✅ **sha256 of `ast.dump` with the validation `Try` removed is identical** at HEAD and after: `67eb5115…` |
| The canonical suite is unchanged in both directions | ✅ §6 |
| If the fix cannot distinguish the two without changing a contract, that is an ADR — stop and report | ✅ §4 — the *mechanical* half needs no contract change; the *status* half does, and is reported not done |

---

## 4. The part that **would** need an ADR — reported, not done

The brief's escape hatch is explicit, and it is the honest place to stop.

**What is fixed:** the failure `_validate_tool_args` **produces** is a system failure, distinguishable by a
caller **without parsing prose**, and its text names the capability. That is criteria 1 and 2.

**What is not fixed, and is a contract question:** the **denial status** the model eventually sees. At the
two dry-run call sites the refusal is stamped `tc_event["_denial"] = "SCHEMA_INVALID"`, and that status is a
**published vocabulary**, not a local string:

| Surface | Location | Why it is a contract |
|---|---|---|
| `_DENIAL_STATUSES` | `wisp/core/events.py:312` | the frozenset of legal denial kinds |
| `OUTCOME_BY_STATUS` | `wisp/core/events.py:346` | the **canonical** status→class classifier; `SCHEMA_INVALID → OutcomeClass.INVALID` |
| `TERMINAL_OUTCOME_CLASSES` | `wisp/core/events.py:358` | `INVALID` is non-retryable — a *verdict*, not a blip |
| the prompt's DENIALS-ARE-FINAL list | `wisp/context_assembler.py:157` | the model is told `SCHEMA_INVALID` *"never succeeds on retry"* |
| `DENIAL_SCHEMA_INVALID` | `wisp/core/recovery.py:48, :154` | consumed by the M12 classifier (ADR-0032) |
| the two stamping sites | `wisp/core/stateless.py:629, :647` | where the kind becomes a status |

Changing it needs **either** a new member in a published denial taxonomy (additive, but a taxonomy
extension consumed by the classifier and quoted to the model) **or** routing this refusal through a
different envelope than `denial_result()` (a shape change to the refusal path, and 13F.1 R2's scope).
**Neither is a mechanical fix, and the brief forbids taking one silently.**

**The residual, stated precisely.** With the fix in place, a missing validator still yields the *status*
`SCHEMA_INVALID` downstream, so the model's tool-result envelope still reads as an argument verdict even
though the error **event** and the **returned failure** say otherwise. The attribution is correct where it
is produced and incorrect where it is published. Whether to reconcile them is a decision for an ADR, and
the surfaces above are its exact blast radius.

---

## 5. Non-vacuity

Six probes, each restoring the tree byte-identical (sha256 verified), controls green either side:

| Probe | Result |
|---|---|
| **N1** the whole file against **HEAD's `stateless.py`** | **caught** — collection error (the test pins the new API) |
| **N2** delete the `except ImportError` clause | caught — collection error |
| **N3** keep the clause, report `SCHEMA_INVALID` | caught — **8 failed** |
| **N4** mutate the `write_file` salvage, *outside* the block | caught — 1 failed (the AST digest) |
| **N5** move the clause **after** the broad handler | caught — collection error |
| **N6** restore HEAD's **exact observable behaviour** with the API present | caught — **8 of 21 failed** |
| CONTROL, before and after | green |

**N6 is the meaningful one.** N1/N2/N5 fail at *collection* because the test imports names the mutation
removes — a RED, but not proof that the *behavioural* assertions discriminate. N6 keeps the API and restores
only the behaviour (the historical message text and the schema kind), so it is "today's code" as the tests
can see it. The eight that fail are exactly the ones that assert the distinction:

```
test_a_missing_validator_is_a_capability_failure
test_the_message_does_not_blame_the_arguments
test_it_says_the_arguments_were_never_checked
test_it_does_not_invite_a_retry
test_a_write_file_call_is_not_laundered_by_the_retry_block
test_the_two_kinds_are_distinct_values
test_a_caller_can_branch_without_reading_the_message
test_the_model_is_told_the_host_is_broken          ← the end-to-end one
```

**The AST pin is itself non-vacuously checked.** `test_the_helper_can_see_a_change` mutates the schema
lookup — outside the validation block — and asserts the digest *does* move. Without it, a helper that
accidentally stripped the whole function would make the digest match trivially. *A scanner reporting
"absent" is not evidence of absence* until shown to find the thing that is there.

---

## 6. Regression

```
env -u PYTHONPATH .venv/bin/python -m pytest <37 files> -q -p no:cacheprovider --tb=no -rf
```

| | |
|---|---|
| **After Deliverable 2** | 1090 tests — 1089 passed, 1 failed |
| **After Deliverable 3** | **1115 tests — 1114 passed, 1 failed** (108.95 s) |
| **The one failure, both runs** | `test_node_identity.py::…::test_a_parallel_round_is_journaled_as_one_exchange_per_call` — **F38**, pre-existing |
| **New failures** | **none** — the failure set is identical in both directions |
| **Now-passing** | none |
| **Delta** | **+25 tests, +25 passed** — 21 in `test_f8_error_classification.py` and 4 in `test_criteria_derivation_authority.py` (the wiring class that F-0's bug demanded) |

The full suite was **not** run: F36 says it cannot run in one process on this host. The method is weaker
than a two-run intersection and is stated as such.

`ruff check wisp/` — the error set is **identical to HEAD's** (11 pre-existing). This change introduces
zero new ones, verified by an `LC_ALL=C comm` diff of the two sets.

**`mypy` — measured, and it is the reason F-0 was found.** `CONTEXT.md` §11 lists `mypy` as the second
gate and describes it as green; it is **not**. Measured with the pinned toolchain
(`uv run --no-project --with "mypy==2.3.1" mypy wisp/`):

| | |
|---|---|
| **At HEAD** | `Found 1844 errors in 228 files (checked 378 source files)` |
| **After this deliverable** | `Found 1844 errors …` |
| **NEW (line-insensitive, on file + message)** | **0** |
| **GONE** | **0** |

An intermediate state of this change introduced **11** new mypy errors, including the `TypeError` in F-0.
All 11 are fixed. Two of them were worth the toolchain on their own: `"CriteriaDerivation" object is not
iterable` (the live bug) and `"ValidationFailure" has no attribute "kind"` — the latter because
`__slots__` alone leaves the attribute invisible to a checker, which is exactly the claim the class exists
to make; it now carries a class-level annotation.

**`mypy` is 1844 errors from green.** `CONTEXT.md` §11 calls both gates green and neither is; §11's counts
and its gate claim are corrected in the handoff update.

---

## 7. Findings — reported, not repaired

### F-0 — **mypy caught a live bug in this change that every test missed**

The most important finding of this deliverable is about its own instrument.

`explain_acceptance` returns a **`CriteriaDerivation` dataclass**, not a `(criteria, specs)` tuple — the
signature it replaced returned a tuple. The wiring in `converge_on_objective` was written as:

```python
_, derived_specs = explain_acceptance(objective_text, workspace, strict=strict)
```

which **iterates a frozen dataclass** and raises `TypeError` on the first line of the production path.

**All 55 tests passed.** Every one of them calls `explain_acceptance` or the `ConvergenceController`
*directly*; none drove `converge_on_objective`. So the production wiring was covered by nothing, and the
first real `wisp converge` run would have crashed.

`mypy` found it: `"CriteriaDerivation" object is not iterable  [misc]` — one of **11** new errors the
change introduced. All 11 are now fixed (0 remain; §6), and the four tests in
`TestTheProductionWiring` drive the real caller. Probe **W1** reintroduces the exact shipped line and
**4 tests fail**.

This is the F41/F54 defect class — *a fixture that does not reproduce the production control flow* — and
it is the second time in this mission that the instrument, not the subject, was the defect. **It is also
the reason `mypy` was run at all**: the brief does not require it, and the tests were green.

### F-1 — the `write_file` retry block is reachable but **inert**

`_validate_tool_args`'s `write_file` branch re-validates the **same `args`** against the **same `schema`**
after the first `validate` raised. Nothing mutates `args` between the two calls — the salvage runs *before*
the `try` — so a deterministic validator raises identically and the `return None` inside the retry is
**unreachable**.

**Driven, not read.** An instrumented `jsonschema.validate` counts the calls: an invalid `write_file` with a
`path` produces **2** calls (so the block *is* reached) and returns the same failure (so it cannot change
the answer). It doubles the validation cost of every invalid `write_file` call and reads as a fallback
without being one.

**Reported, not removed.** Removing it is a behaviour-neutral simplification but a larger diff than the
brief authorises, and the AST criterion asks for the surrounding function to be otherwise unchanged. The
inertness is recorded in a comment at the site (comments are not AST nodes, so the pin is unaffected).

### F-2 — the same schema failure already has **two** classifications

The pre-dispatch path reports a schema failure as a **denial** (`_denial = "SCHEMA_INVALID"` →
`denial_result()`), while the defense-in-depth path in `_execute_tool` (`stateless.py:2007`) reports the
identical condition as an **ordinary error** (`{"status": "error", "data": schema_error}` →
`OutcomeClass.ERROR`). One condition, two published classifications, differing on retryability. This is the
same defect class §4 describes, and it is the evidence that the status question is genuinely undecided
rather than merely unimplemented. **Reported; it is part of the same ADR.**

### F-3 — `CONTEXT.md` §6's editable-install claim is **inverted** (found in Deliverable 2)

Recorded there; repeated here because it changes how every command in this repo must be run.

`CONTEXT.md` §6 states: *"`__editable___wisp_0_1_0_finder` resolves `wisp` ahead of `sys.path`, so
**`PYTHONPATH` cannot override which package is imported**."* Measured:

- the finder's `MAPPING` points at `/Users/philosopher/Documents/wisp/…` — the **pre-move** path — and
  **that directory does not exist**;
- from a foreign cwd with no `PYTHONPATH`: `ModuleNotFoundError: No module named 'wisp'`;
- with `PYTHONPATH` set to the repo root: it imports.

So the finder is a **no-op**, `PYTHONPATH` **can** override, and what actually resolves `wisp` is the
**cwd** (`./wisp`, because `python -m pytest` puts the cwd first). The practical rule — *change files in
place to compare against a baseline* — is still correct, but for a different reason, and the real hazard is
the one the doc does not name: **run from anywhere but the repo root and `wisp` resolves to nothing**, or
to a foreign tree if one is on `PYTHONPATH`. **Reported, not repaired** (repairing the venv is an
environment change, and CONTEXT.md §9 keeps F8's env work separate from architecture); the §6 row is
corrected in the handoff update.

---

## 8. Honest limits

- **The status is still wrong downstream.** §4. The fix corrects the attribution where the failure is
  *produced*; the published denial status is unchanged, and changing it is an ADR.
- **The capability path is unreachable in this environment**, because `jsonschema` is now provisioned. It is
  exercised by hiding the module from `sys.modules` — the technique the F8-provisioning phase established —
  which reproduces the *import* failure but not, say, a partially-installed `jsonschema` whose own import
  fails deep inside. The clause covers both; only the first is measured.
- **The AST pin is a digest, not a proof of intent.** It shows the non-validation statements are
  byte-identical in AST; it does not show they are *correct*. That is what the rest of the suite is for.
- **F-1's inertness is proven for a deterministic validator with unmutated inputs.** A validator that
  mutated its own schema or the instance could make the retry live; `jsonschema` does neither, and the
  measurement above confirms the observed behaviour. Stated as a boundary rather than as impossibility.
- **`ruff` and `mypy` are not green at HEAD** — 11 ruff errors and **1844** mypy errors. This change adds
  none to either (both verified by set diff). Repairing them is outside this deliverable.
- **The tests that now cover the wiring cover it for *one* objective shape.** `TestTheProductionWiring`
  drives `converge_on_objective` with a stub root and a `done`-only event stream. It would catch a
  `TypeError` on the derivation line — the bug that shipped — but it does not exercise a real provider, a
  real tool call, or a multi-attempt recovery. The wiring is covered against *crashing*, not against
  being *wrong*.
