# PHASE_OUTCOME_CLASSIFICATION_VIOLATION — the code was wrong, and the guard was right

**Baseline:** `HEAD` = `5c129d5` (§0 records `aa47ae0`; the delta is the handoff commit itself, the
known one-behind pattern). Working tree = 29 entries, of which exactly **one** is a tracked
modification — `wisp/core/graph/__init__.py`, the user's pre-existing edit (`CONTEXT.md` §8 row R9).
ADR count 57. **No drift.**

**Deliverable:** 1 of 2. **Type:** a mechanical fix — **no ADR**. See §3.
**Report:** this file. Code: `wisp/core/stateless.py`. Tests:
`tests/reliability/test_outcome_classification_delegation.py` (new),
`tests/test_outcome_classification_authority.py` (one floor added).

---

## 1. The violation, driven

`tests/test_outcome_classification_authority.py::test_no_module_reimplements_tool_result_status_classification`,
run against the tree as it stood:

```
E  AssertionError: a module classifies a tool-result status by comparing
   .get("status") to "ok"/"error" directly — classify through wisp.core.events
   instead: {'wisp/core/stateless.py': [158, 167]}
```

The two lines:

```python
return result.get("data") if result.get("status") == "ok" else None        # :158
return parsed.get("data") if parsed.get("status") == "ok" else None        # :167
```

`result` and `parsed` are the executor's tool-result envelope (`{"status", "tool", "data",
"metadata"}`) — the function's own docstring says so. `status == "ok"` is **the success test**, and
`test_ok_is_the_only_success` pins `"ok"` as the taxonomy's only `SUCCESS`. So the comparison *is* a
second classifier for a vocabulary `stateless.py` does not own.

Introduced by `ade4dc6` (POST-M13 execution semantics) — confirmed by
`git log -S'result.get("status") == "ok"' -- wisp/core/stateless.py`, which returns exactly that
commit for both lines.

**Why it stayed invisible for four phases.** Not because nothing checked — because the block that
would have run the check **aborts at collection**. `CONTEXT.md` §11's Phase-10 block names this file
and two others that import `fastapi.testclient`, and `httpx` is not installed in `.venv`. The guard
has been RED since `ade4dc6` and *no run in this environment ever reached it*. That is
`PHASE_CORPUS_INTEGRITY.md` §2.1's finding, and this mission is its consequence.

---

## 2. Which side is wrong — established by driving, not by reading

The brief's framing: *"The report's classification as 'contract update' may be correct (updating the
guard is a change to a pinned test) or it may be wrong (fixing the code is the fix, and the guard
needs no change)."*

**The code is wrong. The guard needs no change.**

| question | measurement |
|---|---|
| Is `result` the tool-result envelope? | yes — the function's docstring names the executor's envelope, and the envelope is what `stateless.py` receives on the live path |
| Is `status == "ok"` the taxonomy's success test? | yes — `test_ok_is_the_only_success` asserts `["ok"]` is the only status mapping to `OutcomeClass.SUCCESS` |
| Is the rule's subject this comparison? | yes — the guard's detector is exactly `<x>.get("status") == "ok"｜"error"`, and its docstring says so precisely to avoid flagging event types, plan status and health status that share the key name |
| Is there a load-order obstacle? | no — `stateless.py` already imports `wisp.core.events` (line 29); `events` is a leaf module (ADR-0040 R6) |
| Is there a legitimate exception? | no. The alternative reading — *this is a pre-dispatch state where the outcome is not yet a tool result* — is **false**: the function's whole purpose is to decide whether a **completed** tool result carries output. |

**This is not F38's class, and the corpus said it was.** `PHASE_CORPUS_INTEGRITY.md` §2.1 recorded
both red guards as *"contract updates — exactly F38's class"*. F38 is a **test** that encoded the
broken environment as the contract; the test was wrong and the code was right. Here the test asserts
the canonical rule and the **code violates it** — the test is doing its job. The corpus-integrity pass
lumped two unlike things together:

| guard | which side is wrong | correct classification |
|---|---|---|
| `test_no_module_reimplements_tool_result_status_classification` | **the code** | a live violation — fix the code (this report) |
| `test_tool_executor_construction_sites_are_known` | **the test's count** | a contract update — a third site was legitimately added (Deliverable 2) |

Recorded as **F86**. It is F78's shape one level up: a claim about *two* red guards, derived from
*one* of them.

---

## 3. The fix, and why it is not an ADR

```python
# wisp/core/stateless.py — both branches
return result.get("data") if not is_error_outcome(result) else None
return parsed.get("data") if not is_error_outcome(parsed) else None
```

plus `is_error_outcome` added to the existing `from wisp.core.events import (...)` block. The
docstring now names the authority and states why the `"status" not in result` guard above is **not**
redundant with the classifier: `classify_result` defaults a missing status to `"ok"` (success), while
this function must treat a status-less dict as *not an envelope at all*.

**No ADR.** ADR-0043's principle — *the classifier owns no vocabulary* — is unchanged; the rule is
unchanged; the code is brought into compliance. An ADR is warranted when a **decision** is made. None
was: there was no defensible alternative reading (§2), so the "exception" branch the brief allowed for
never opened. `test_derive_acceptance`'s precedent applies in reverse — the corpus's convention is to
write an ADR when a choice exists, and to write a **report** when the measurement settles it.

**ADR-0012's caution applies and is satisfied.** ADR-0012 kept P1 out of `stateless.py` because it is
*"the hottest, most heavily guarded file in the codebase"*, with the reversal condition *"if a future
phase needs the result journaled from inside the engine … revisit"*. This change is not P1's journaling
and does not touch the turn loop's structure: it replaces two expressions inside one pure helper with
calls to an already-imported function. The change could not be moved out of `stateless.py` — the
comparison lives inside a function that is *about* the envelope, so relocating it would be a larger
refactor of the hot file, not a smaller one. The guards ADR-0012 leans on are the ones this report
re-runs: the engine's AST invariants, the gate-order corpus, and the transcript-shape invariant (§5).

---

## 4. The equivalence proof — the production function, before and after

Not a restatement of the expression: the **real `_tool_result_output`** was driven over a 35-case
corpus before the edit and again after, and the two JSON dumps compared.

```
35 cases -> identical (sha256 of the dump unchanged)
```

The corpus covers every status in the taxonomy, an unknown status, a case-variant (`"OK"`), an empty
status, a status-less dict (the early-return path), well-formed and malformed JSON strings, a JSON
string whose value is not a dict, leading whitespace, and non-envelope values (`int`, `None`, `list`,
plain text, the executor's `[Blocked: …]` / `[Denied: …]` strings).

Probe: `.workbuddy-ai/memory/post-m13-gate-enablement/tool_result_output_differential.py`.

---

## 5. The four non-violations — asserted, parsed, not scanned

`tests/reliability/test_outcome_classification_delegation.py`, 6 tests.

| # | non-violation | how it is asserted |
|---|---|---|
| 1 | `turn_succeeded`, `VerificationFloorGuard`, `goal.PRECEDENCE` untouched | `PRECEDENCE` pinned **by content** (8 rows 0–7; row 4 is the fatal clause bounded by the P3-PASS escape, ADR-0047 R1; rows 5/6/7 = `GOAL_STAGNATED` / `GOAL_MET` / `GOAL_UNVERIFIED`); `VerificationFloorGuard`'s four public methods; and `turn_succeeded`'s projection **parsed from the AST** as `_goal_outcome is TerminalOutcome.SUCCEEDED` (13-H5) |
| 2 | `core.events`'s public surface unchanged | `classify_result`'s parameter list; the 8-name `OutcomeClass` vocabulary; the 7-key `OUTCOME_BY_STATUS` table; **totality** — every class is a mapped value or the fallback; and `"ok"` is the only success |
| 3 | the engine's gate chain unchanged | the AST of `ToolExecutor.execute`: the first call site of `policy_hard_deny`, `authorize`, `_get_write_tools`, in that order (mirrors ADR-0055 R8) |
| 4 | the transcript shape unchanged (ADR-0029) | the AST of `Session.apply`: the `reply` dict literal's keys are exactly `{role, content}`, and `reply["tool_call_id"]` is a **conditional subscript assignment** — one, never a literal key |

Plus two properties of the fix itself: the delegating function calls `is_error_outcome` **twice**
(a count, not a presence check — the defect was that *one* of two identical branches was migrated, and
a `"in src"` scan would be satisfied by the docstring that names it), and no
`.get("status") == "ok"/"error"` survives in `stateless.py`.

**The delegation guard is AST-parsed throughout.** The brief's rule — *a check over Python code must
parse it, not scan it* — is why the reply-shape test walks the AST instead of grepping for `"name"`.

---

## 6. Non-vacuity — nine probes, each a real violation

`.workbuddy-ai/memory/post-m13-gate-enablement/outcome_classification_nonvacuity.py`

| probe | what it breaks | result | evidence |
|---|---|---|---|
| NV1 | the exact defect returns at line 158 | **CAUGHT** | 3 failed |
| NV2 | the **half**-migration returns (line 167 only) | **CAUGHT** | 2 failed |
| NV3 | a **semantic** mutation no AST guard can see (`is_error_outcome({"status": "ok"})`) | **CAUGHT** | the differential DIFFERS |
| NV4 | the scan stops reaching the package (the new floor) | **CAUGHT** | 1 failed |
| NV5 | `goal.PRECEDENCE` row 6 changes outcome | **CAUGHT** | 1 failed |
| NV6 | `VerificationFloorGuard.reset_turn` is renamed | **CAUGHT** | 1 failed |
| NV7 | `classify_result`'s parameter is renamed | **CAUGHT** | 1 failed |
| NV8 | a gate leaves `ToolExecutor.execute` | **CAUGHT** | 1 failed |
| NV9 | the tool-reply dict gains a literal `name` key | **CAUGHT** | 1 failed |

**9/9 CAUGHT. All 7 touched files restored byte-identical (sha256).**

**NV3 is the probe worth keeping.** It mutates the delegation into a *semantically* different call
that is still a call to `is_error_outcome` — so every AST guard passes and only the **differential**
notices. It is the direct demonstration of this mission's own discipline: an instrument must be
matched to the property, and the AST guards and the differential are matched to *different*
properties.

### The floor (one deliberate strengthening)

`test_no_module_reimplements_tool_result_status_classification` iterates
`(REPO / "wisp").rglob("*.py")` and asserted only `not offenders`. **A check whose subject is a
collection and has no floor passes vacuously when the collection is empty** — the brief's rule, and
this guard is the instrument that just failed to fire for four phases. Three lines added: count the
modules scanned, and fail below 100 (380 modules at HEAD). This changes no rule and weakens nothing;
NV4 is its proof. The brief said the guard *"needs no change"* — its **contract** does not; this is
the floor the mission's own method requires.

---

## 7. The brief's claims, each driven

| claim | verdict |
|---|---|
| *"`wisp/core/stateless.py:158,167` compare `.get("status")` to `"ok"`/`"error"` directly"* | **true** — reproduced exactly |
| *"Introduced by `ade4dc6` (POST-M13 execution semantics)"* | **true** — `git log -S` returns `ade4dc6` for both lines |
| *"`stateless.py` already imports `wisp.core.events`"* | **true** — line 29 (14 `wisp.*` imports); no load-order obstacle |
| *"ADR-0012 says the engine is the hottest, most heavily guarded file"* | **true**, and its reversal condition does not apply (§3) |
| *"The report's classification as 'contract update' may be correct or may be wrong"* | **it is wrong for this guard** — F86 (§2) |
| *"`WISP_MIGRATION_STATUS.md`'s §2.1 row for this guard"* | **FALSE.** `WISP_MIGRATION_STATUS.md` §2.1 is *"P0 — Wire the Orphaned Durable Layer / 2.1 Objective"*. **Neither guard is named anywhere in that file** (grep for `outcome_classification｜m4_governance｜protected_path_guard｜server_policy_gate` → 0 hits). The rows the brief means are in `PHASE_CORPUS_INTEGRITY.md` §2.1, which the brief itself cites correctly earlier in the same document. **The sixth consecutive brief with a wrong citation.** The row is closed in the file that has it (§8). |

---

## 8. Findings

**F85 — a count is canonical only if it is measured after the LAST change to any member.** Found
while computing this deliverable's own block count. The canonical block reported **1316** tests for
its 45 files, not the **1312** both headings claimed. No code changed between the two measurements.
The cause: the corpus-integrity pass measured the block in **Deliverable 1**, set both headings, and
**Deliverable 2** then added **four** tests to `tests/reliability/test_current_authorities_pins.py` —
a block member — without re-running it. Verified three ways: the file collects **34** tests; `git show
aa47ae0 --stat` shows **+112 lines** added to it in D2; and reverting this mission's own `stateless.py`
change does not move the count (1316 either way), which rules out the alternative explanation.
F71 said *never quote a count from prose*; F82 said *a count is canonical only if there is ONE block*;
**F85 says the measurement must follow the last change, not the change that motivated measuring.**

**F86 — the corpus classified two unlike guards as one class.** `PHASE_CORPUS_INTEGRITY.md` §2.1
called both red guards *"contract updates — exactly F38's class"*. For the M4 count guard that is
right; for the outcome-classification guard it is wrong — the code violated the rule and the guard
was correct. See §2 for the table. The lesson is F78's one level up: a claim about **two** things,
derived from **one**.

**The brief's citation error** (§7) — sixth consecutive.

---

## 9. Corpus updates in this deliverable

- **`tests/test_outcome_classification_authority.py`** — the floor (§6). The guard's rule is
  unchanged; it now fails if it scans nothing.
- **`CONTEXT.md` §11** — the canonical block gains the two outcome-classification files (the guard
  that was red for four phases, and its new delegation guard), so a guard that was invisible because
  its block aborts now lives in a block that runs. Both headings re-measured: **1390**.
- **`CONTEXT.md` §11** — the count-history note gains F85, and the Phase-10 note's
  outcome-classification half is corrected: it is **not** a contract update.
- **`AGENTS.md`** — the block, its heading, and its note about what the block was extended with.
- **`CONTEXT.md` §0 / §3** — in the follow-up commit.

---

## 10. Residuals, open

1. **The M4 count guard is still RED** — Deliverable 2, and it is the one that *is* a contract update.
2. **`httpx` is now installed — CLOSED 2026-09-27.** `tests/test_protected_path_guard.py` and
   `tests/test_server_policy_gate.py` both run and pass, so the counts are quotable. The obstacle was
   **TLS, not network** (F-T9), which is why "re-attempts the offline install" was the wrong remedy.
3. **`CONTEXT.md` §11's Phase-10 note still says the third `ToolExecutor` site is `wisp/acp_session.py`
   from `cef3e90`.** Driven, that is wrong on both counts — Deliverable 2 corrects it.
4. **`PHASE_CORPUS_INTEGRITY.md` §2.1's own text carries the same wrong claim** about the third site.
   It is a phase report (a historical record) — the same rule as the two PHASE_10 reports applies:
   annotate, do not rewrite. Deliverable 2 annotates it.

---

## 11. Verification

| check | result |
|---|---|
| the guard that was RED | **68 passed** (was 67 passed, 1 failed) |
| the new delegation guard | **6 passed** |
| the two together | **74 passed** |
| the equivalence differential | **35/35 identical** before vs after |
| non-vacuity | **9/9 CAUGHT**, 7 files restored byte-identical (sha256) |
| the canonical block (47 files) | **1390 tests — 1389 passed, 1 failed (F38)**; both blocks identical |
| the Phase-10 runnable block + config + doc-drift + the WS suites | **273 tests — 272 passed, 1 failed** (the M4 count guard, Deliverable 2's item) |
| `ruff check wisp/` | **11 errors — unchanged** (F71); the changed files are clean |
| `mypy` | **not re-run** — stated as an argument, not a measurement |

**Rollback:** the revert of a two-expression delegation and one import. No behaviour moved (§4), so
the revert is safe in either direction.
