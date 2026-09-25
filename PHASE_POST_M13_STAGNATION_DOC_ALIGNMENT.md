# PHASE POST-M13 — STAGNATION DOCSTRING ALIGNMENT (ADR-0037)

**Documentation-only phase, authorised by ADR-0037's Implementation boundary.** No behavioural change
was made, and none is possible: the edit is proven docstring-only by two independent instruments (§5).

| Field | Value |
|---|---|
| Phase | POST-M13 — stagnation documentation alignment |
| Authority | ADR-0037, *"Authorised in a future implementation phase — documentation only"* |
| Mode | DOCUMENTATION-ONLY |
| `HEAD` | `7c15626` (unchanged) |
| Production changes | **0 behavioural.** Two files edited, both prose |
| Predecessor | `PHASE_POST_M13_STAGNATION_LATCH_ADR_AMENDMENT.md` (ADR-0037) |

---

## 1. Mission

ADR-0037 ratified that the stagnation latch is **monotonic**, but its own §"Implementation boundary"
recorded that three pieces of documentation still described the *pre-amendment* semantics. This phase
corrects them:

1. the module docstring's **"N consecutive"** mitigation — scope it to the **verdict**;
2. `may_report_goal_met()` — state that it is `False` from the **first** flat observation and is
   monotonic for the detector's lifetime;
3. `trap_fired` — align *"not sufficient on its own"* with the predicate's actual rule;
4. `AGENTS.md` — verify its completion-gate section matches ADR-0037's wording.

Nothing else was in scope. The forbidden list in ADR-0037 (`trap_fired` clearing, `may_report_goal_met()`
's rule, `is_progress_from()`, `OscillationTrap`, `min_consecutive` gating the predicate, a reopening
mechanism, a second detector or predicate, persistence, ADR-0035's precedence, recovery semantics,
`turn_succeeded`, enabling `stagnation_gate`, F8, the graph, fanout) was honoured — none was touched.

## 2. Baseline, and why the snapshot is outside the repo

`wisp/core/stagnation.py` was **already modified in the working tree** before this phase (pre-existing WIP
from the M13/POST-M13 work). A `git diff` therefore cannot isolate this phase's change, and `git stash`
is forbidden (discipline #4 — it would revert the pre-existing work in the same file to `HEAD` and discard
it).

So the file was **snapshotted outside the repo** before any edit, and the phase's diff is
snapshot-vs-now:

| Artefact | Path |
|---|---|
| Pre-edit snapshot | `.workbuddy-ai/memory/post-m13-doc-alignment/stagnation.py.before` (`sha256 94f7d2da…`) |
| Pre-edit `AGENTS.md` | `.workbuddy-ai/memory/post-m13-doc-alignment/AGENTS.md.before` (`sha256 270d1bea…`) |
| Docstring-only prover | `.workbuddy-ai/memory/post-m13-doc-alignment/prove_docstring_only.py` |
| Docstring-truth instrument | `.workbuddy-ai/memory/post-m13-doc-alignment/verify_docstrings_are_true.py` |

## 3. What changed

Four docstrings in `wisp/core/stagnation.py`, and one paragraph in `AGENTS.md`.

| # | Surface | Was | Now |
|---|---|---|---|
| 1 | Module docstring, mitigation 1 | *"**N consecutive** … required before stagnation is declared"* — unscoped | scoped to the **verdict**, with the predicate's effective threshold stated as **1** and the reason (`trap_fired` latches) |
| 2 | `StagnationDetector` class docstring | *"so stagnation requires a **run** of them"* — same ambiguity | *"so the **verdict** requires a run of them. It does **not** gate the completion predicate"* |
| 3 | `trap_fired` | *"evidence of stagnation, but **not sufficient on its own**"* — the opposite of the predicate's rule | **the latch**: append-only, per-turn, progress does not clear it; *for the predicate it **is** sufficient* (`not trap_fired`); `observe`/`verdict` remain the verdict's authority |
| 4 | `may_report_goal_met()` | *"`False` while stagnation stands"* — silent on monotonicity | **Monotonic (ADR-0037)**: `False` from the first flat observation, *before* `min_consecutive`; no reopening — not on progress, not on a replan; the only exit is the detector's lifetime; `min_consecutive` gates the verdict only; the arbiter reads this value (`runtime.py:1176`) and the record carries it (`:1265`) |
| 5 | `AGENTS.md`, *"The completion gate is two gates…"* | *"It cannot change the goal state **in practice**"* — framed as an empirical consequence of the current signal source | *"It cannot change the goal state — **the latch is monotonic (ADR-0037)**"*, with the rule, the `min_consecutive` scope, and the corrected reference |

**Edit 2 is one surface beyond ADR-0037's enumerated three**, and is declared rather than slipped in: the
class docstring made the *same* unscoped claim as the module docstring, so leaving it would have left the
exact ambiguity this phase exists to remove. It is a docstring in the named file, so it is within
*"docstrings in `core/stagnation.py` stating the monotonic semantics"*, but it is not one of the three
named items.

**Edit 5 is a correction, not a verification-with-no-change.** ADR-0037 asked that `AGENTS.md`'s wording be
*verified*; verification found it **did not match**. *"In practice"* renders a ratified rule as a
contingent observation — it implies the gate could change the goal state if the signal source changed,
which is precisely what ADR-0037 rejected as an alternative. The paragraph now states the rule and cites
the ADR.

## 4. Isolated diff

Four hunks, all inside docstrings: **46 lines added, 7 removed**. The largest is `may_report_goal_met()`
(+24). No executable line changed.

## 5. Verification

### 5.1 The edit is docstring-only — two independent instruments

`prove_docstring_only.py` compares the snapshot against the edited file. Neither instrument can be
satisfied by a behavioural change, and both had to agree:

| Instrument | Method | Result |
|---|---|---|
| Docstring-stripped AST equality | parse both, delete every module/class/function docstring node, compare `ast.dump` | **identical** |
| Recursive `co_code` equality | compile both, walk every code object, compare the instruction stream | **identical** |

```
docstring-stripped AST identical : True
recursive co_code identical      : True
VERDICT: DOCSTRING-ONLY (no behavioural change)
```

Comments never reach the AST, so instrument 1 covers the prose comments for free. Instrument 2 is the
stronger claim: docstring *text* lives in `co_consts[0]` and never appears in `co_code`, so every byte of
compiled behaviour is unchanged.

### 5.2 The docstrings describe the code they are attached to

A docstring that states the wrong rule is worse than none, so each written claim was executed rather than
asserted by eye. `verify_docstrings_are_true.py` drives a real `StagnationDetector` — **16 claims, all
hold**:

| Claim | Evidence |
|---|---|
| a fresh detector permits `GOAL_MET` | `may_report_goal_met()` is `True` |
| the predicate closes on the **first** flat observation | `trap_fired` true, `consecutive_flat` (1) **<** `min_consecutive` (2), verdict **not** `stagnating`, predicate **already `False`** |
| genuine later progress does **not** reopen it | `consecutive_flat` resets to 0, verdict returns to `progressing`, `trap_fired` stays latched, predicate **still `False`** |
| the only exit is the detector's lifetime | a fresh detector permits `GOAL_MET` again |
| a disabled detector permits `GOAL_MET` | `graph_oscillation_guard` off → `True` (the rollback switch) |
| an empty observation is refused (F32) | not appended, trap does not fire, predicate stays `True` |

This instrument lives **outside `tests/`** deliberately: ADR-0037's implementation boundary authorises
documentation surfaces only, so adding a test file would have exceeded it. It is a verification
instrument, not a regression test, and it is recorded here rather than committed.

### 5.3 The focused suite is unchanged

```
env -u PYTHONPATH .venv/bin/python -m pytest \
  tests/test_stagnation_detection.py tests/test_stagnation_live_wiring.py \
  tests/reliability/test_post_m13_authority_implementation.py \
  tests/reliability/test_post_m13_completion_enforcement.py -q
→ 154 passed
```

No test weakened, none updated, none added. Combined with §5.1 this is stronger than a passing suite: the
bytecode is provably identical, so the result is confirmation rather than the argument.

## 6. Discovered, reported, not fixed (scope discipline)

Three items, all outside the surfaces ADR-0037 authorised. None is a behaviour problem.

| Where | Says | Reality |
|---|---|---|
| `tests/test_stagnation_detection.py:11-13` | *"The mitigation is structural — N consecutive flat observations, and a **strictly shrank** metric"* | the **same unscoped claim** the module docstring just had corrected. It is prose in the test file's own docstring, not an assertion, so nothing breaks — but it is a stale echo of the pre-ADR-0037 semantics. `tests/` is not an authorised surface for this phase |
| `CONTEXT.md:44` and `:971` | findings `F1–F34` / `F1–F24` | the ledger's last finding is **F36**; the two lines disagree with each other. Already reported by the ADR-0037 phase and by the prior context reload |
| `WISP_MIGRATION_STATUS.md` | *"**17 commits**"*; its commit table calls `a055b39` "most recent" and duplicates `d80f582` | `CONTEXT.md` §3 lists **33** rows. The findings log also has **no F34 row** — F34 is referenced inside F36's prose but never defined |

## 7. Honest limits

- **`ruff` / `mypy` were not run** — neither is installed in this venv, and there is no network to install
  them. As in every prior phase, the lint/type criterion is **unmet, not skipped**.
- **Nothing enforces these docstrings against the code.** The instrument in §5.2 proves them true *today*;
  it is not part of the suite, so a future change to `may_report_goal_met()` could silently invalidate the
  prose. Making it a committed test would require a superseding ADR, since ADR-0037 authorises
  documentation surfaces only.
- **Line numbers are cited in the new prose** (`runtime.py:731`, `:1176`, `:1265`). They are correct as of
  this phase and drift as the file changes — the repository's existing convention, not a new hazard.

## 8. Final status

```
=== STATUS: COMPLETE ===
PHASE: POST-M13-STAGNATION-DOC-ALIGNMENT
MODE: DOCUMENTATION-ONLY
AUTHORITY: ADR-0037 (implementation boundary)
PRODUCTION_CHANGES: 0 behavioural  (docstring-stripped AST + co_code both byte-identical)
FILES_EDITED: wisp/core/stagnation.py (4 docstrings), AGENTS.md (1 paragraph)
SURFACES_BEYOND_THE_ENUMERATED_THREE: 1 — the StagnationDetector class docstring; declared in §3
LATCH_SEMANTICS_DOCUMENTED: MONOTONIC
MIN_CONSECUTIVE_DOCUMENTED: verdict threshold only; the predicate's effective threshold is 1
TRAP_FIRED_DOCUMENTED: the latch; sufficient for the predicate, not for the verdict
AGENTS_MD_VERIFICATION: MISMATCH FOUND AND CORRECTED — "in practice" -> the monotonic rule
FOCUSED_TESTS: 154 passed
DOCSTRING_TRUTH: 16/16 claims hold against the live code
BEHAVIOURAL_IMPLEMENTATION: NOT_AUTHORISED — enforcement stays behind `stagnation_gate` (default OFF)
NEXT: nothing further is authorised. The remaining documented gaps (§6) are one-line prose fixes on
      surfaces this phase was not authorised to touch.
=============================
```
