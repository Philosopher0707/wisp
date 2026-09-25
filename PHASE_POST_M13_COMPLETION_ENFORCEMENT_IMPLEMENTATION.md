# PHASE POST-M13 — COMPLETION ENFORCEMENT IMPLEMENTATION

**Phase type:** production implementation of **ADR-0036**.
**Date:** 2026-09-24
**Predecessors:** `PHASE_POST_M13_COMPLETION_ENFORCEMENT_POLICY_ADR.md` (ADR-0036) ·
`PHASE_POST_M13_LIVE_COMPLETION_SEAM_RECON.md` · `PHASE_POST_M13_AUTHORITY_IMPLEMENTATION.md` (ADR-0035)

---

## 1. Mission result

**M13's stagnation predicate is now a bounded completion-control input at the existing pre-`done` gate.**
It can withhold `done` for at most two replan interventions per turn and then surrenders honestly; it
never vetoes the turn, and the engine is handed a read-only predicate — never the detector.

**One finding changes what enforcement can achieve in practice, and it is reported rather than buried**
(§15.1): on the live path a *flat* observation always repeats the previous state digest, so
`OscillationTrap` fires and `trap_fired` latches — which means **the predicate can never reopen**, and the
gate's interventions cannot convert a `GOAL_STAGNATED` into a `GOAL_MET`. ADR-0036's truth table predicted
that outcome as *one* case; it is in fact the only case. The implementation is faithful to the ADR — it is
the ADR's *predicted* row that is unreachable, and ADR-0036 §6 already ratified the latch as a bounded
consequence.

---

## 2. Baseline

| Property | Value |
|---|---|
| HEAD | `7c15626` — `docs(m11): record the M11 phase; findings F29-F31; repair the commit table` |
| Branch | `main` |
| Working tree | **dirty** — 89 entries (39 tracked-modified + 50 untracked) |
| Diff at baseline | 209,831 bytes total; **88,451 bytes** for `wisp/` + `tests/` |
| Frozen surfaces at baseline | `verification.py`, `acceptance.py`, `recovery.py`, `events.py`, `goal.py`, `wisp/graph/` — **all zero-diff, and still zero-diff after** |
| Focused baseline | **125 passed** across the four suites the phase touches |

Snapshot: `.workbuddy-ai/memory/post-m13-enforce/{head,branch,status-before}.txt`, `diff-before.patch`,
`code-before.patch`.

## 3. Pre-existing dirty-tree disclaimer

**The tree carries substantial unrelated WIP**, and three of the four production files this phase edits
**already contained it** (M13's own uncommitted changes in `stagnation.py`/`runtime.py`/`stateless.py`, and
Phase 10's work in `config.py`). Consequences, stated rather than glossed:

- The `git diff` for those files shows **far more than this phase's work**. §5 isolates this phase's
  contribution; the remainder is pre-existing and was **not** reverted, reformatted or touched.
- **No `git stash`, `git reset --hard`, `git checkout --` or `git clean` was used.** The baseline is a
  snapshot taken outside the tracked tree.
- One `Edit` in this phase had to be **repaired**: an insertion into `config.py` accidentally joined two
  lines (`"tool_pool_size": {        "type": int,`). It was caught by reading the region back, fixed in the
  same turn, and the final file is verified by an AST parse and by the config tests (§11).

## 4. Files changed

| File | Nature | This phase's contribution |
|---|---|---|
| `wisp/core/stagnation.py` | production | **`compose_replan_nudge()`** — M13's replan intervention text (~40 lines). The rest of the file's diff is M13's own uncommitted work |
| `wisp/core/stateless.py` | production | the `completion_gate` parameter on `turn()`/`_turn_inner()`, the `_MAX_STAGNATION_INTERVENTIONS` bound, the per-turn local counter, and the gate block at the pre-`done` site |
| `wisp/core/runtime.py` | production | the `stagnation_gate_enabled` read, the read-only closure over the per-turn detector, `completion_gate=` at the call site, and the `stagnation_allows_goal_met` record key |
| `wisp/config.py` | production | the `stagnation_gate` / `WISP_STAGNATION_GATE` flag — schema entry, dataclass field, `get_setting` read; **default OFF** |
| `tests/reliability/test_post_m13_completion_enforcement.py` | **new** | 33 tests — S1–S14, the latch (including the live-path confirmation), the intervention's shape, the adversarial set, and the AST ratchets |
| `tests/reliability/test_post_m13_authority_implementation.py` | test | **one hunk**: D6's replay now reads `stagnation_allows_goal_met` instead of `stagnation_verdict` (the F35 fix on the test side) |
| `PHASE_POST_M13_COMPLETION_ENFORCEMENT_IMPLEMENTATION.md` | docs | this report |

**Verification method note.** The phase-delta comparison (§12) works on `git diff`, so it covers **tracked**
files only. The two test files above are **untracked** (created by the previous phase / this one), so their
edits are enumerated here explicitly instead: `test_post_m13_authority_implementation.py` received exactly
one surgical hunk (§4), and `test_post_m13_completion_enforcement.py` is new.

## 5. Exact production changes

### 5.1 `wisp/core/stagnation.py` — the intervention text

```python
def compose_replan_nudge(attempt: int = 1) -> str:
```

Replan-shaped, never retry-shaped. Attempt 1 asks for a materially different next step; attempt 2 says the
previous instruction was not acted on and asks for a change of strategy *now*. The docstring records three
load-bearing facts:

- **why replan**: `FORBIDDEN_RUNGS[FailureClass.STAGNATION] ∋ RETRY` (`recovery.py:131`) — retrying the
  same action against the same state *is* the loop being detected;
- **why generic**: it never names the repeated action. The transcript has it, and naming it would require
  the seam to carry more than a predicate;
- **why `[SYSTEM]`**: *discovered while implementing* — the runtime persists an injection **by that
  prefix** (`runtime.py`, the `system` branch: `if _msg.startswith("[SYSTEM]")`), so the marker is what
  makes a replan the model saw survive resume. A nudge without it would be the one injection silently
  dropped from the transcript.

Home module matters: it mirrors `verification.compose_nudge` and the engine's import of it
(`stateless.py:332-336`), which is GH#27's rule — *"nudge text derives from the invariant's home module …
so the intervention can never drift from the gate"*.

### 5.2 `wisp/core/stateless.py` — the seam

| Site | Change |
|---|---|
| module level | `_MAX_STAGNATION_INTERVENTIONS = 2` — a default on the mechanism, **not** a config key, mirroring `VerificationFloorGuard.max_nudges`; explicitly not shared with the floor guard's budget |
| `turn()` | `completion_gate: Any = None` added; documented as read-only |
| `turn()` → `_turn_inner()` | threaded as a keyword |
| `_turn_inner()` | `completion_gate: Any = None`; `stagnation_interventions_used = 0` as a **local** beside the guard |
| the pre-`done` gate | the new block, **after** the floor guard's `continue` and **before** `guard.resolved()`/`done` |

The gate, verbatim in structure:

```python
if (completion_gate is not None
        and stagnation_interventions_used < _MAX_STAGNATION_INTERVENTIONS
        and iteration + 1 < max_iterations):
    try:
        may_complete = bool(completion_gate())
    except Exception:
        logger.debug("completion gate failed", exc_info=True)
        may_complete = True            # fail open
    if not may_complete:
        stagnation_interventions_used += 1
        from wisp.core.stagnation import compose_replan_nudge
        replan = compose_replan_nudge(stagnation_interventions_used)
        messages.append(nudge_message(replan))
        yield _flatten_event(system(replan, level="warning"))
        continue
```

Three deliberate properties:

- **Imported lazily, inside the withhold branch.** With no gate to evaluate, the engine keeps its **zero
  coupling** to stagnation — the import only materialises when it actually withholds.
- **The third condition is not a budget.** `iteration + 1 < max_iterations` guards the existing loop
  bound: withholding on the last iteration ends the loop, runs the budget wrap-up
  (`stateless.py:957-964`), and converts this honest surrender into a fatal `CODE_ITERATION_BUDGET`.
  *A bound that can turn its own surrender into a budget failure is not a bound.*
- **Nothing else was touched** — not the two failure-path `done` sites (`:313`, `:964`), not the floor
  guard, not the wrap-up.

### 5.3 `wisp/core/runtime.py` — the closure, the flag, the record

```python
stagnation_gate_enabled = bool(
    getattr(getattr(self, "config", None), "stagnation_gate", False))

completion_gate = (
    (lambda: bool(stagnation_detector.may_report_goal_met()))
    if (stagnation_gate_enabled and stagnation_detector is not None)
    else None)
```

passed as `completion_gate=completion_gate` at the `core.turn` call site. The closure is created in
`run_turn` and dies with it, so it is **per-turn by construction** — nothing to cache, nothing to leak
between turns or sessions, and no global state. `None` when the flag is off or there is no detector, which
is exactly today's behaviour: **no detector, no intervention authority** (ADR-0036's non-goal for
subagents/background, which drive `core.turn` directly and pass nothing).

And the F35 record key (§9).

### 5.4 `wisp/config.py` — the flag

`stagnation_gate` / `WISP_STAGNATION_GATE`, **default `False`**, following the existing three-site pattern
(schema entry, dataclass field, `get_setting` read). The description states the separation from
`graph_oscillation_guard`: that flag disables the **detector**; this one disables **enforcement**. Recording
and enforcing are different concerns (ADR-0002), so the rollback has two levels.

## 6. The seam implemented

```
M13 (runtime, per turn)                    ← the single stagnation authority
   │  may_report_goal_met()
   │  read-only closure, no detector object
   ▼
core.turn(..., completion_gate=…)
   ▼
pre-done gate  (stateless.py:819-863)      ← AFTER guard.rejection(), BEFORE done
   │
   ├── predicate open ............................. done
   ├── closed ∧ budget ∧ a round remains .......... replan nudge → next iteration
   └── closed ∧ (exhausted ∨ last iteration) ...... done  (honest surrender)
```

## 7. M13 ownership proof

Proven by AST ratchet in `tests/reliability/test_post_m13_completion_enforcement.py`, not by assertion:

| Claim | Ratchet |
|---|---|
| The engine imports **only** M13's message text | `_stagnation_imports(stateless.py) == {"compose_replan_nudge"}` |
| The engine never touches detector internals | no `ast.Attribute` named `consecutive_flat` / `min_consecutive` / `OscillationTrap` / `ProgressSignal` / `may_report_goal_met` / `StagnationDetector` / `trap_fired` / `observe` anywhere in `stateless.py` |
| The gate object is only ever **called** | zero `ast.Attribute` reads off `completion_gate`; exactly **one** `ast.Call` of it |
| The counter is not shared state | `stagnation_interventions_used` appears as a `Name` store and **not** as any `ast.Attribute` |
| The bound is 2 | the module-level assignment to `_MAX_STAGNATION_INTERVENTIONS` is the constant `2` |

`verification.py`, `acceptance.py`, `recovery.py`, `events.py`, `goal.py` and `wisp/graph/` are
**byte-identical** to the baseline (§12) — so the floor guard's, P3's, the ladder's and the arbiter's
semantics are unchanged **by construction**, not merely by test.

## 8. Bounded intervention semantics

| Property | Value | Where |
|---|---|---|
| Bound | **2 per turn** | `_MAX_STAGNATION_INTERVENTIONS` |
| Owner | the engine, beside `guard.nudges_used` | `_turn_inner` local |
| Reset | every turn — a fresh local per `_turn_inner` call | `tests/…::test_s11_a_second_turn_gets_a_fresh_budget` |
| At exhaustion | **surrender**: no message, no event, fall through to `done` | `test_s2_s3_s4_…` |
| Last iteration | **cannot be withheld** | `test_s5_the_final_iteration_cannot_be_withheld` |
| Journaled | **no** — not authority, not a goal-state input | §9 |
| Shared with the floor guard | **no** | separate counters, separate questions |

**The intended end state, asserted end to end:** a stagnating turn emits `done`, `turn_succeeded = True`,
and `goal_state = GOAL_STAGNATED`.

## 9. F35 — discovery and fix

**The defect, confirmed by reading the source and then by test.** The arbiter's row-4 input and the
recorded evidence were two different computations:

| Fact | Was | Now |
|---|---|---|
| the arbiter's `stagnating` | `not may_report_goal_met()` (`runtime.py:1138-1140`) — includes `trap_fired` | **unchanged** |
| the recorded evidence | `detector.verdict` (`:1194-1197`) — ignores `trap_fired` | **`stagnation_allows_goal_met`** |

`may_report_goal_met()` is `False` on `consecutive_flat >= min_consecutive` **or** `trap_fired`
(`stagnation.py:303-305`); `verdict` returns `STAGNATING` on the first term only (`:276-278`). So with the
trap fired below the flat threshold, the live derivation yields `GOAL_STAGNATED` while the record says
`"progressing"` — and the replay test reconstructed **from the record**
(`test_post_m13_authority_implementation.py:350`). **Live and replay disagreed.**

**The fix — one key, from the same computation:**

```python
"stagnation_allows_goal_met": not _stagnating,
```

`_stagnating` is `bool(detector is not None and not may_report_goal_met())`, so `not _stagnating` **is** the
predicate's value when a detector exists, and `True` when none does — exactly what row 4 consumed. Taking
it from the same variable makes divergence **structurally impossible**: there is no second call to drift.
Replay reads this field; the human-readable `stagnation_verdict` stays as M13's own verdict, and the *pair*
explains the disagreement (`verdict != "stagnating"` ∧ predicate closed ⇔ the trap fired).

**Not the "fix" the brief forbade:** `may_report_goal_met()` was **not** changed to match `verdict`,
`trap_fired` was **not** removed, and M13's semantics are untouched. The record was aligned to the live
predicate, which is the authority.

**The F35 regression test drives the exact case** (`test_s13_trap_fired_below_the_flat_threshold`): two
identical observations close the predicate via the trap while `consecutive_flat < min_consecutive`; the
test asserts the record still says `progressing`, that `stagnation_allows_goal_met is False`, and that
replay from the record alone yields `GOAL_STAGNATED`.

## 10. Test matrix

| # | Requirement | Test | Result |
|---|---|---|---|
| S1 | predicate open ⇒ no intervention | `test_s1_an_open_predicate_emits_done_with_no_intervention` | pass |
| S2 | first closed predicate withholds + replans | `test_s2_s3_s4_the_gate_withholds_twice_then_surrenders` | pass |
| S3 | second intervention is distinct | same test (`"Stagnation loop (repeat)"`) | pass |
| S4 | exhaustion ⇒ `done`, `turn_succeeded=True`, `GOAL_STAGNATED` | same test | pass |
| S5 | final iteration cannot be withheld | `test_s5_the_final_iteration_cannot_be_withheld` | pass |
| S6 | floor guard preserved | `test_s6_the_floor_guard_is_consulted_first` + `…_still_surrenders_honestly` | pass (see §15.2) |
| S7 | both closed ⇒ one nudge, no bypass | `test_s7_the_floor_guard_block_ends_in_continue` | pass (see §15.2) |
| S8 | gate absent ⇒ `done` | `test_s8_no_gate_is_todays_behaviour` | pass |
| S9 | gate raises ⇒ `done` | `test_s9_a_raising_gate_permits_done` | pass |
| S10 | flag off ⇒ old path, recording intact | `test_s10_the_flag_off_preserves_the_old_path` | pass |
| S11 | per-turn reset | `test_s11_a_second_turn_gets_a_fresh_budget` | pass |
| S12 | no shared state | `test_s12_concurrent_turns_share_no_state` (two runtimes, `asyncio.gather`) | pass |
| S13 | trap-fired F35 replay | `test_s13_trap_fired_below_the_flat_threshold` (+ restart, + live==replay) | pass |
| S14 | `UNKNOWN` non-blocking | `test_s14_unknown_stays_open_and_never_becomes_stagnation` | pass |

**Adversarial and ratchet tests:** `max_iterations=1`; `max_iterations=2`; a permanently closed predicate
still finishing; a predicate that flips open after one replan; a fatal error with a closed predicate
(`GOAL_FAILED`, row 3 > row 4); a closed predicate on a dead provider (cannot promote a failed turn); a
duplicate `GOAL_STATE` frozen against rewrite (ADR-0020); the intervention's non-retry wording; the text's
home module; the `[SYSTEM]` marker; and the five AST ratchets of §7.

**Plus the latch pin** (`TestThePredicateIsALatch`) — two tests recording *why* the intervention's reach is
bounded, so the property is asserted rather than folklore.

## 11. Focused results

| Run | Result |
|---|---|
| `tests/reliability/test_post_m13_completion_enforcement.py` (new) | **33 passed** |
| The 12 affected suites (incl. 13-H4/H5, doc-drift, M13, M9, M11, ADR-0035) | **381 passed** |
| The migration suite (24 files + both post-M13 files) | **796 passed** — was 714 before this phase |
| AST parse of all four changed modules | ok |

## 12. Full-suite comparison

### Method — and why it is not the usual two runs

**The full suite could not be run in one process.** The host is **memory-starved** (≈145 MB free of
16 GB, load average 4.5), so the kernel kills the run: the first attempt died at **67%** (4,724 outcomes,
no summary) with `exit=137`, and a second attempt died the same way. This is the same *shape* as F34 (the
disk) — an environmental limit that presents as a mysterious mid-run death.

The suite was therefore split into **six chunks** (five by interleaved test file, then the fifth bisected
into halves because it too was killed at full size), each run separately, and the failure sets **unioned**:

| Chunk | Result |
|---|---|
| 0 | 42 failed, 1404 passed, 3 skipped |
| 1 | 11 failed, 1441 passed, 2 errors |
| 2 | 21 failed, 1292 passed, 3 errors |
| 3 | 22 failed, 1332 passed, 10 errors, 3 skipped, 2 xfailed |
| 4a | 6 failed, 631 passed, 1 error |
| 4b | 4 failed, 677 passed, 1 error |

**Union: 123 failures. Baseline: 129. `NEW = 1`, `GONE = 7`.**

**This is weaker than the two-run intersection the repo prescribes, and it is stated rather than
implied.** Two things make it adequate evidence anyway, and both are checked:

- the **frozen surfaces** (`verification.py`, `acceptance.py`, `recovery.py`, `events.py`, `goal.py`,
  `wisp/graph/`) are **byte-identical** to the baseline — so those authorities cannot have changed;
- the **migration suite is 796 passed with zero failures**, and the 12 affected suites are **381 passed**.

### The 12 regressions this phase found — and fixed

The **first** chunked run showed **12 NEW failures**, all in turn-path tests (`test_agent_runtime.py`,
`test_headless_runtime_integration.py`, `test_multi_transport_integration.py`,
`test_repl_audit_pindown.py`, `test_runtime_injected_context.py`). Diagnosed in isolation:

```
TypeError: _MockCore.turn() got an unexpected keyword argument 'completion_gate'
```

**A real defect I introduced, and it is the recon's own blind spot.** The seam recon argued the kwarg was
backward-compatible because *"`core.turn` has ~65 call sites and already accepts extra kwargs"*. That is
true of **callers** of `core.turn` — and false of **implementations** of `turn()`. Every `_MockCore` in the
suite implements the pre-ADR-0036 signature, and passing `completion_gate=None` unconditionally made them
raise. It is exactly ADR-0009's "do not widen a pinned internal signature to carry a new concern".

**Fixed** by passing the kwarg **only when there is a gate**, so with the flag off (the default) the call is
byte-for-byte the old call. Re-running the five files: **68 passed, 3 failed — all three pre-existing**
(verified against the baseline). Then the chunks were re-run: chunks 0–2 dropped from 91 to 74 failures.

### The 1 NEW and 7 GONE that remain — both explained, neither mine

All eight are **sandbox/bash** tests, and **none touches a file this phase changed**.

| Set | Tests | Explanation |
|---|---|---|
| **1 NEW** | `test_keyboard_interrupt_propagation.py::…::test_keyboard_interrupt_escapes_run_bash` | **Reproduced directly.** The sandbox router now reports *"no Docker daemon available — commands execute directly on host"*, so the host path runs the real command; the test patches `asyncio.create_subprocess_shell`, which the host path does not use, so `sleep 100` runs to its 60s timeout. It has **never appeared in any of the 7 recorded failure sets**, and it fails in isolation — so it is an environment change, not interference and not this phase |
| **7 GONE** | 4 × `test_tools.py::TestToolRunBash`, 3 × `test_v04_subsystems_integration.py::TestScenarioBSandboxAndServerAuth` | **A chunking artifact.** They fail in **every** recorded full-suite run (baseline, `m13/final1`, `post-m13/fullrun1` — 4 and 3 each) and **pass in isolation** (7 passed in 2.6s). Their subject is sandbox routing, i.e. exactly the cross-test interference that a different file grouping perturbs |

**So: no new failure is attributable to this phase**, and the one that is new is an environment condition
that should be recorded (it is: ledger **F36**).

| | Result |
|---|---|
| Pre-phase baseline | `.workbuddy-ai/memory/baseline-failures-stable.txt` — 129 |
| This phase (union of 6 chunks) | 123 |
| New attributable to this phase | **0** |
| Regressions found and fixed during the phase | **12** (`_MockCore.turn()`, §12) |
| Frozen surfaces | **byte-identical**, before and after |
| Phase delta (per-file hunk comparison of `git diff`) | **exactly four files**: `wisp/config.py`, `wisp/core/runtime.py`, `wisp/core/stagnation.py`, `wisp/core/stateless.py`. No frozen surface, no graph, no fanout |

## 13. Rollback flags

| Level | Flag | Effect |
|---|---|---|
| **1 — stop enforcing** | `stagnation_gate` / `WISP_STAGNATION_GATE`, **default OFF** | the gate is `None`; M13 still observes, the goal record still carries the predicate, the goal state is unchanged — only the intervention stops |
| **2 — stop detecting** | `graph_oscillation_guard` / `WISP_OSCILLATION_GUARD`, default **true** | the detector is disabled, so `may_report_goal_met()` returns `True` and both the gate and row 4 go quiet |
| **3 — stop recording** | `goal_state` / `WISP_GOAL_STATE`, **default OFF** | no `GOAL_STATE` record at all |

Neither requires a code change, and none touches `turn_succeeded`.

## 14. Safety / non-goal verification

| Forbidden | Verified |
|---|---|
| `VerificationFloorGuard` / its criterion | **zero-diff** on `verification.py`; its contract re-pinned by test |
| P3 semantics / `evaluate()` | **zero-diff** on `acceptance.py` |
| Recovery ladder | **zero-diff** on `recovery.py`; recovery still gated on `not turn_succeeded` (`runtime.py:1149`) |
| `turn_succeeded` | **not touched** — still one producer (`runtime.py:902`), still pinned by 13-H4's AST ratchet |
| failure-path `done` sites | **not touched** (`:313`, `:964`) |
| graph execution / fanout | **zero-diff** on `wisp/graph/`, `wisp/multi_agent/` |
| F8 / `jsonschema` | **not repaired**; `pyproject.toml` and the validation path untouched |
| M1, M5–M8, M10 | untouched |
| a second detector | none — M13 is the only one, and the engine imports only its message text |
| global M13 state | none — the closure is per turn, the counter is a local, and no ContextVar was added |
| a planner | none — the intervention is a message |
| runtime re-invoking `core.turn` | none — the intervention happens inside the existing iteration |
| steering-only implementation | none — the floor guard's own nudge path is reused |
| tests weakened | none — one test was **strengthened** (§4's D6 hunk reads the authoritative field) |

## 15. Deviations and findings

### 15.1 The predicate is a one-way latch, so the intervention cannot convert `GOAL_STAGNATED` into `GOAL_MET` — **reported, not hidden**

**What ADR-0036 predicted** (truth table): a stagnation from the *flat run* could be cleared by a
successful replan, yielding `GOAL_MET`; a stagnation from the *trap* could not, because `trap_fired`
latches.

**What the implementation found:** the two cases are not two cases. On the live path the state digest is
built from the *accumulating* action and artifact sets (`stagnation.py:316-328`), and any growth in either
is progress by `is_progress_from` (`:161-179`). So a **flat** observation necessarily repeats the previous
digest, which necessarily fires `OscillationTrap`, whose verdict list is never cleared
(`graph/loop.py:118-125`) — so `trap_fired` is True whenever the predicate is closed. **The predicate is
closed iff the trap has fired, and it never reopens.** Pinned by
`TestThePredicateIsALatch::test_progress_does_not_reopen_a_latched_predicate`, which shows
`consecutive_flat` returning to 0 on real progress while the predicate stays closed.

**Consequences, stated plainly:**

- The gate's interventions **cannot change the goal state**. Its observable effect is the transcript (two
  replan messages, two extra rounds) and the *chance* that the model produces better work — not the
  recorded authority.
- ADR-0036's truth-table row *"closed (flat run) → replan clears it → `GOAL_MET`"* is **unreachable under
  the shipped configuration**. It would only be reachable if `min_consecutive` were 1 (then one flat
  observation closes the predicate before any digest repeat), and nothing configures that.
- This is **not a blocker and not a contract violation**: the implementation matches ADR-0036's decision
  exactly, and ADR-0036 §6 already ratified the latch as "bounded and conservative". The gate can never
  turn a `GOAL_MET` into anything worse than `GOAL_STAGNATED`, which is the architecture's
  under-claim-success bias.
- **The refinement is available and out of scope.** ADR-0036 §6 names it: a runtime-side policy change
  (the closure is the runtime's) *plus* a change to what is recorded — never one without the other, or
  F35's divergence returns. Changing it would mean changing `may_report_goal_met()`, i.e. M13's semantics,
  which this phase is forbidden to touch. **Recommended as the next decision if enforcement is ever
  enabled for real.**

### 15.2 S6/S7 are verified structurally, not behaviourally — and why

The brief requires S6 ("the floor guard's exact behaviour is preserved; M13 must not bypass it") and S7
("both closed ⇒ no double nudge"). **The floor guard cannot be made to reject on a live turn in this
environment**, for two compounding reasons, both verified:

1. `_validate_tool_args` needs `jsonschema`, which is absent (F8), so every call is refused before
   dispatch; and
2. a refused call is yielded from the **pre-dispatch** path (`stateless.py`, the `_blocked` branch), which
   **never calls `guard.note_tool_result`**. A probe confirmed it: a `write_file` round produced a
   `tool_result` with `name='write_file'`, and the guard still read `wrote_code=False`, `turns_used=0`.

So `wrote_code` can never become True here and `rejection()` always returns `None`. S6/S7 are therefore
proven three ways instead — and each is a real test, not a substitute for one:

- **ordering**, by AST: `guard.rejection()` precedes `completion_gate()` in `_turn_inner`, and the floor
  guard's rejection branch ends in `continue` (so the stagnation gate is unreachable while it rejects);
- **contract**, by unit test: `VerificationFloorGuard` still rejects a mutated unverified turn and still
  surrenders honestly after its budget — the exact property ADR-0036 says the new gate must inherit;
- **negative control**: with `verification_loop=False`, the stagnation gate *does* fire on the same turn
  shape — so its silence above is the ordering, not an unreachable gate.

**Corroborated by a pre-existing failure.** `test_runtime_injected_context.py::test_verification_nudge_persisted_in_transcript`
is in the stable baseline and asserts that **two** verification nudges reach the transcript — which
requires `rejection()` to fire twice. It gets zero. The floor guard's inability to reject here is therefore
not an inference from my probe; it is already visible in the baseline's own failure set.

### 15.3 The `[SYSTEM]` prefix is load-bearing (discovered while implementing)

The runtime persists a mid-turn injection **by prefix** (`runtime.py`: `_msg.startswith("[SYSTEM]")`), not
by a nudge marker. The intervention text therefore had to carry it or the replan would be the one
injection missing from the resumed transcript. Pinned by
`test_the_intervention_is_persisted_in_the_transcript`.

### 15.4 Two test bugs of mine, caught by the tests themselves

- My first `TestTheFloorGuardIsUntouched` assumed the floor guard would reject — §15.2 shows why it
  cannot. Rewritten, not deleted.
- My "engine references M13 internals" ratchet was a **whole-file substring search**, and it matched my own
  explanatory docstring (`observe()` mutates) — the P8 trap, in a test I had just written to prevent a
  different one. Replaced with an AST check over `ast.Attribute` names.

### 15.5 The seam recon's compatibility argument was right about callers and wrong about implementations — and the suite caught it

The recon reasoned that an optional kwarg on `core.turn` is backward-compatible because *"`core.turn` has
~65 call sites and already accepts extra kwargs"*. **That is true of callers and false of
implementations.** `turn()` is also an *interface*: every `_MockCore` in the suite implements the
pre-ADR-0036 signature, so passing `completion_gate=None` unconditionally made 12 turn-path tests fail with
`TypeError: _MockCore.turn() got an unexpected keyword argument 'completion_gate'`.

Fixed by passing the kwarg **only when a gate exists**, so with the flag off (the default) the call is
byte-for-byte the old call. This is ADR-0009's rule — *"do not widen a pinned internal signature to carry a
new concern"* — and the general lesson is worth keeping: **an optional parameter is only optional at the
call site that adopts it; every alternative implementation of the interface pays for it.** The full-suite
comparison is what caught it (§12); the focused suites could not have, because they all drive the real core.

## 16. Remaining gaps

| Gap | Status | Note |
|---|---|---|
| The latch (§15.1) | **deferred — needs a decision** | the intervention cannot change the goal state; refining it means changing M13 |
| Cancellation has no live producer | **pre-existing** | precedence implemented and tested; nothing emits it |
| S6/S7 behavioural coverage | **deferred — environment** | needs F8 repaired, or a `tool_executor` in the harness |
| Subagent/background enforcement | **deferred by design** | no detector ⇒ no intervention authority (ADR-0036 non-goal) |
| The intervention count is not journaled | **deferred by decision** | ADR-0036 §7: not authority, and journaling it needs a stateful closure or a second publication |
| F8 / `jsonschema` | **untouched** | still blocks the whole tool path, and therefore ADR-0016's measurement |
| Enforcement enabled by default | **not enabled** | `stagnation_gate` ships OFF |

## 17. Acceptance checklist

- [x] ADR-0036 is implemented exactly
- [x] M13 remains the only stagnation detector — proven by AST
- [x] The engine receives only a read-only predicate/callable — proven by AST
- [x] No detector object crosses into `stateless.py` — proven by AST
- [x] Stagnation can withhold `done` only within the bounded budget
- [x] The bound is exactly two interventions per turn — proven by AST
- [x] The last iteration cannot be withheld
- [x] The replan message is not a retry instruction
- [x] The intervention text belongs to `stagnation.py`
- [x] Fail-open behaviour is implemented (absent, raising, disabled)
- [x] `stagnation_gate` defaults OFF
- [x] Existing default behaviour is unchanged with the flag OFF
- [x] `turn_succeeded` semantics unchanged (zero-diff producer; 13-H4 ratchet green)
- [x] `VerificationFloorGuard` semantics unchanged (zero-diff + contract test)
- [x] P3 semantics unchanged (zero-diff)
- [x] The recovery ladder is unchanged (zero-diff)
- [x] **F35 is fixed** — the record carries the predicate the arbiter consumed
- [x] Live and replay agree for trap-fired stagnation (tested, incl. restart)
- [x] `P3 PASS + STAGNATING → GOAL_STAGNATED`
- [x] `P3 PASS + UNKNOWN + success → GOAL_MET`
- [x] A successful stagnating turn may end as `turn_succeeded = True`
- [x] No global mutable M13 state — proven by AST (local counter, no attribute)
- [x] No new planner
- [x] No graph/fanout changes (zero-diff)
- [x] No F8 changes
- [x] Existing tripwires remain meaningful (the two inverted M13 tripwires still pass)
- [x] No tests were weakened (one was strengthened)
- [x] No unrelated pre-existing WIP was reverted

## 18. Final status

**implemented and verified:** the seam, the bound, the last-iteration rule, fail-open, the flag, the F35
fix, the replan text, and the whole test matrix — with the full-suite comparison in §12 and the frozen
surfaces byte-identical.

**deferred, with reasons:** §16 — the latch refinement (needs an M13 decision), S6/S7 behavioural coverage
(needs F8), subagent enforcement (by design), the intervention count (by decision), and enabling the flag.

**blocked:** nothing.

```
PHASE STATUS: COMPLETE
```

The one thing a reader should carry away is §15.1: **the gate is correct, bounded and reversible, and it
cannot change the goal state** — because the predicate it consults is a latch. That was ADR-0036's own
ratified consequence, now measured rather than assumed, and it is the first thing to decide before
enforcement is enabled for real.
