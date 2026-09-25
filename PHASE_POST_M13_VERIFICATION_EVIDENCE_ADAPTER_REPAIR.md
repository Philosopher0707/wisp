# PHASE POST-M13 — VERIFICATION EVIDENCE ADAPTER REPAIR

**Mode:** AUTHORIZED IMPLEMENTATION
**Predecessor:** `PHASE_POST_M13_VERIFICATION_EVIDENCE_AUTHORITY_RECON.md` (Option A, `ADR_REQUIRED: NO`)

---

## 1. Mission Result

**F37 is repaired.** A mutation followed by a genuinely failing verification is no longer recorded as
a success.

| | before | after |
|---|---|---|
| `run_bash("exit 3")` after a real `write_file` | `verify_ok_after_edit = True` | `verify_ok_after_edit = False` |
| | `guard.resolved() = True` | `guard.resolved() = False` |
| | P3 `pass` | P3 **`fail`** |
| | `goal_met` | **`goal_failed`** |

The repair is **one adapter** in one production file: `wisp/core/stateless.py`, **65 lines added, 3
removed, 3 hunks**. Nothing else in `wisp/` was touched.

**Three reachable false-success shapes were found and closed, not one.** The recon identified the
envelope-of-a-failing-command. This phase's reachability probe found two more on the same boundary,
both live:

1. a **non-`ok` envelope** — a `run_bash` that times out at the tool level (`status: "error"`);
2. a **bare block message** — `run_bash` with a dangerous command, refused as the plain string
   `[Blocked: dangerous command — …]`, which is not an envelope at all.

All three read as verified success before the repair. `false_success_after = 0`.

**The fix also had to be scoped, and the scoping is the load-bearing judgement** — see §3 and §7.

---

## 2. Baseline

| Item | Value |
|---|---|
| `HEAD` | `7c15626` (M11) — all POST-M13 phases implemented, verified, documented, uncommitted |
| branch | `main` |
| `wisp/` tracked modifications | 20 — pre-existing WIP, **unchanged** by this phase |
| interpreter | `.venv/bin/python`, `jsonschema` 4.26.0 importable |
| `stagnation_gate` / `goal_state` / `recovery_ladder` | `false` / `false` / `false` — **unchanged** |
| adapter under repair | `wisp/core/stateless.py` — the evidence fold at `:920-930` |
| protected authorities | `verification.py`, `acceptance.py`, `goal.py`, `events.py`, `tool_executor.py`, `bash.py`, `config.py` |
| pre-repair snapshot | `.workbuddy-ai/memory/post-m13-evidence-adapter/stateless.py.before` (outside the tree) |

---

## 3. Root Cause Consumed

The recon's finding, restated as the contract it violated:

```text
tools/bash.py::_format_bash_output   emits "[exit code: N]" FIRST, only when N != 0
core/verification.py::_verify_result_is_success   tests startswith("[exit code:")
        ^ these two are a JOINT contract, documented at verification.py:91-97
          and pinned by tests/test_verification_contract.py
core/stateless.py:924                handed over result_event["result"]
                                     = the executor's JSON envelope, which begins "{"
```

So the parser was right and the argument was wrong. The one production call site was the only place
that violated the contract — every test fed the guard the documented representation, which is why a
green suite never noticed.

**Reachability, measured before writing any code** (`.workbuddy-ai/memory/post-m13-evidence-adapter/reachability_probe.py`):

| probe | value at the fold | status | before |
|---|---|---|---|
| `run_bash` timeout (`sleep 5`, `timeout=1`) | `{"status": "error", "tool": "run_bash", "data": "ToolError: Command timed out…"}` | `error` | **false success** |
| `run_bash` dangerous (`rm -rf /`) | `'[Blocked: dangerous command — recursive deletion of root filesystem]'` | n/a (plain string) | **false success** |
| `write_file` that fails | `{"status": "error", "tool": "write_file", "data": "Cannot open … Is a directory"}` | `error` | reaches the fold; `wrote_code` set (correct) |
| `run_bash` denied pre-dispatch (AUTO_EDIT) | **nothing** — the call is `_blocked` and skipped | — | not reached |

**The Non-OK Envelope Rule is therefore load-bearing, not defensive**: a non-`ok` envelope genuinely
reaches this fold, via a *dispatched* tool that fails at the tool level. The brief's condition —
"if current source proves a non-OK envelope can legitimately reach this fold … report the exact
path" — is met, and the path is: **`ToolExecutor._run_bash_tool` / the generic `_execute_tool`
`except ToolError` branch → a `status: "error"` envelope → `_execute_single` → the event → the fold.**
No new semantics were invented to handle it; the rule's own treatment (skip, leaving the prior
verdict untouched) is exactly what the pre-dispatch denial path already does.

---

## 4. Implementation

Two pieces, both in `wisp/core/stateless.py`.

**a. `_tool_result_output(result)` — a module-level helper next to `_flatten_event`.**

Returns the tool's own output when the result is a **successful** envelope; `None` otherwise.

```text
dict with "status"                -> data  if status == "ok"   else None
JSON string parsing to that shape -> data  if status == "ok"   else None
anything else                     -> None      (a bare block/banner message)
```

**b. The evidence fold**, which now unwraps and applies the Non-OK rule:

```python
t_name = str(result_event.get("name", ""))
t_out = _tool_result_output(result_event.get("result", ""))
if t_out is None:
    if t_name in _VERIFY_TOOLS:
        continue                 # a refused/failed verification is not evidence
    t_out = ""                   # inert: the guard classifies this tool by NAME
elif not isinstance(t_out, str):
    t_out = str(t_out)
t_args = _call_args_by_id.get(result_event.get("tool_call_id", ""), {})
guard.note_tool_result(t_name, t_out,
                       _digest_args(t_args) if isinstance(t_args, dict) else {})
```

`_VERIFY_TOOLS` is **imported from `core/verification.py`**, not re-listed — the authority keeps
declaring which tools it classifies, so adding a verify tool needs no edit here. `tests/…/test_verification_evidence_adapter.py::test_the_verification_authority_is_still_the_only_writer`
asserts by AST that `note_tool_result` still has exactly **one** call site.

**The scoping is deliberate and is the one judgement in this phase.** The guard dispatches by name:

| branch | what the guard reads |
|---|---|
| `name in _MUTATING_TOOLS` | the **name** only — the text is inert |
| `name in _VERIFY_TOOLS` | the **text** — this is the defect's branch |
| `name == "run_tests"` | the text, via `search`/`in` (not positional, so never broken) |

Skipping the fold for a non-`ok` **mutating** result would leave `wrote_code` unset, which moves a
failed `write_file` from `FAIL` to `INCONCLUSIVE` — a semantic change this phase is explicitly not
authorised to make ("do not introduce INCONCLUSIVE"). So the skip is scoped to the tools whose text
is classified; for every other tool the fold still happens and **no non-`ok` envelope is forwarded**
(`""` is passed, which is inert). This satisfies the rule's purpose — no non-`ok` result is ever
treated as verification evidence — while changing nothing else. §7 pins that with a test.

---

## 5. Exact Diff Boundary

```text
production files changed:  1   (wisp/core/stateless.py)
lines added:              65
lines removed:             3
hunks:                     3
```

Isolated from the file's pre-existing WIP by reconstructing the pre-phase content from the three
edits and diffing (`.workbuddy-ai/memory/post-m13-evidence-adapter/`):

| hunk | what |
|---|---|
| `@@ -123` | the new `_tool_result_output` helper |
| `@@ -356` | `_VERIFY_TOOLS` added to the existing `from wisp.core.verification import (…)` |
| `@@ -922` | the evidence fold: unwrap, the Non-OK rule, the name-scoped skip |

**Verified untouched**, by mtime and by AST comparison against `HEAD`:

```
IDENTICAL  wisp/core/verification.py     IDENTICAL  wisp/core/events.py
IDENTICAL  wisp/core/acceptance.py       IDENTICAL  wisp/tool_executor.py
IDENTICAL  wisp/tools/bash.py            IDENTICAL  wisp/config.py
                                           (goal.py and config.py differ from HEAD only by
                                            pre-existing uncommitted WIP — mtimes 2026-09-24,
                                            before this phase began)
```

`find wisp -name '*.py' -newermt '2026-09-25 00:24'` returns **exactly one file**: `wisp/core/stateless.py`.

---

## 6. Envelope Extraction Contract

| input at the fold | classification | forwarded to the authority |
|---|---|---|
| `{"status": "ok", …, "data": "<output>"}` (str or dict) | successful tool output | **`data`**, as text |
| `{"status": "error", …}` (str or dict) | tool-level failure | nothing (verify tools: skipped; others: `""`) |
| `{"status": "POLICY_DENIED"/"USER_DENIED"/…}` (dict) | denial | nothing |
| `'[Blocked: …]'`, `'[Denied: …]'`, any non-envelope string | refusal banner | nothing |
| a JSON string that does not parse, or parses to something without `status` | malformed | nothing |
| `{"status": "ok", "data": <non-str>}` | successful, structured | `str(data)` |

The `_VERIFY_TOOLS` import is the only coupling added, and it is a *read* of the authority's own
declaration rather than a second copy of it.

---

## 7. Successful Verification Control

`write_file` → `run_bash("echo fine")`, live:

```text
guard.wrote_code            = True
guard.verify_ok_after_edit  = True
guard.resolved()            = True
P3 acceptance_verdict       = "pass"
goal_state                  = "goal_met"
```

And the authority receives `"fine\n"` — real output text, not an envelope
(`test_a_successful_shell_result_arrives_as_text`). **The repair did not turn every command into a
failure**, and the P3 matrix (§15) shows `mutate_verified` and `full_coding` unchanged.

---

## 8. Failing Verification Control

`write_file` → `run_bash("exit 3")`, live, with the event inspected to prove the command really failed:

```text
event["result"] = '{"status": "ok", "tool": "run_bash",
                    "data": "[exit code: 3]\\n", "metadata": {…, "exit_code": 3}}'
  -> note_tool_result receives  "[exit code: 3]\n"      (starts with the marker: True)
  -> _verify_result_is_success -> False
  -> guard.verify_ok_after_edit = False
  -> guard.resolved()           = False
  -> P3 "fail"  ->  goal_state "goal_failed"
```

`test_the_command_really_failed` asserts the precondition (`metadata.exit_code == 3` and `data`
starting with the marker) so the verdict cannot be right for the wrong reason.

The two further reachable shapes are covered by
`test_a_tool_level_error_envelope_is_not_evidence` and
`test_a_blocked_command_message_is_not_evidence`.

---

## 9. Non-Vacuity

**Proven twice, by mutation, with the tree restored byte-identical**
(`.workbuddy-ai/memory/post-m13-evidence-adapter/prove_non_vacuous.py`):

| tree | result |
|---|---|
| unmutated (the repaired adapter) | 13 passed, **0 failed** |
| **M1 — the exact pre-repair expression** (`t_out = result_event.get("result", "")`) | 7 passed, **6 failed** |
| **M2 — a partial repair** (unwrap, but forward non-`ok` results too) | 11 passed, **2 failed** |
| restored | SHA-256 `68aaecb0…` — **byte-identical** |

M1 is the RED-first run, performed against the genuine pre-repair source before the fix was written.
M2 is the more informative one: it shows the suite is **not** satisfied by "unwrap and forget" — the
Non-OK rule has to be there as well, and the two tests that catch its absence are precisely the two
non-`ok` controls.

---

## 10. Verification Authority Preservation

```text
IDENTICAL  wisp/core/verification.py   (AST, vs HEAD)
```

- `_verify_result_is_success` — **not touched**; still the same function in the same module.
- `_VERIFY_FAILURE_PREFIX` — unchanged.
- `note_tool_result` — unchanged; still the sole writer, still one call site (AST-asserted).
- `resolved()` / `rejection()` — unchanged: driven directly, a red run still resolves `False` and
  `rejection()` still blocks.
- `floor_guard_criteria` / `floor_guard_evidence` / `floor_guard_verdict` — unchanged; 1 criterion,
  verdict `fail` for a red run.
- `tests/test_verification_contract.py` — **14 passed, unmodified**. It still pins
  `_format_bash_output ↔ _verify_result_is_success`, which is exactly the contract the adapter now
  conforms to.

---

## 11. P3 Preservation

```text
IDENTICAL  wisp/core/acceptance.py   (AST, vs HEAD)
```

No change to `evaluate`, to `floor_guard_criteria`, or to the criterion's meaning. The P3 verdict
moved only where the *input* moved — proven by the before/after matrix (§15), where **exactly one of
eight rows changed** and it is the row the defect was in.

---

## 12. Goal-State Preservation

```text
IDENTICAL  wisp/core/goal.py   (AST, vs HEAD)
```

`P3 pass → GOAL_MET` and `P3 fail → GOAL_FAILED` both still hold, and both are exercised by the new
suite. The arbiter was handed a corrected verdict; it was not touched.

---

## 13. Security / Approval Preservation

```text
AUTO_EDIT + run_bash   ->  POLICY_DENIED, not executed     (test_a_pre_dispatch_denial_keeps_its_existing_contract)
ASK_ALL  + run_bash    ->  real execution                  (the success control above)
```

`test_f8_tool_execution_restored.py` — **11 passed**, unchanged — still asserts that authorization
hard-denies in the default mode and that an approved command really executes.

The denial control asserts the contract the Non-OK rule exists to preserve: a denied verification
leaves `verify_ok_after_edit is None` (not `False`, not `True`) and P3 `fail`, exactly as before.
**No `INCONCLUSIVE` was introduced, no failure class was added, no authority moved.**

---

## 14. Replay Behavior

No change, and none authorised. `acceptance_verdict` remains a derived value persisted as a fact;
replay still reads it and is still faithful. **Historical records keep their pre-repair verdicts** —
a journal written before this repair still says `pass` for a turn whose verification failed, and that
is correct behaviour for an immutable record. New turns record the corrected verdict. No migration,
no journal rewrite, no new replay authority.

---

## 15. P3 Stage-3b Re-measurement

The identical 8-shape matrix, measured **both ways** in separate processes (an in-process file swap
cannot work — every module that did `from wisp.core.engine import WispAgentCore` already holds a
reference to the old code). Population stated, not sampled: it is a matrix over reachable turn
shapes, not a traffic sample.

| shape | tools | BEFORE P3 | BEFORE goal | AFTER P3 | AFTER goal |
|---|---:|---|---|---|---|
| `no_tools` | 0 | inconclusive | goal_unverified | inconclusive | goal_unverified |
| `read_only` | 1 | inconclusive | goal_unverified | inconclusive | goal_unverified |
| `mutate_unverified` | 1 | fail | goal_failed | fail | goal_failed |
| `mutate_verified` | 2 | pass | goal_met | pass | goal_met |
| **`mutate_failed_verification`** | 2 | **pass** | **goal_met** | **fail** | **goal_failed** |
| `schema_rejected_call` | 1 | inconclusive | goal_unverified | inconclusive | goal_unverified |
| `policy_denied_call` | 1 | inconclusive | goal_unverified | inconclusive | goal_unverified |
| `full_coding` | 3 | pass | goal_met | pass | goal_met |

```text
P3 PASS          3  ->  2
P3 FAIL          1  ->  2
P3 INCONCLUSIVE  4  ->  4

rows whose verdict changed:  ['mutate_failed_verification']   (pass -> fail)
```

**Corrected interpretation.** The before-population's three `PASS` rows were `mutate_verified`,
`mutate_failed_verification` and `full_coding`. One of them — `mutate_failed_verification` — was
**not a pass at all**; it was the defect. The honest pre-repair rate for "a mutating turn that
verifies successfully" was therefore **2 of 8, not 3 of 8**, and the `INCONCLUSIVE` rate is
**unchanged at 4/8 = 50.0%** — the F8 measurement's headline number survives, but its PASS/FAIL split
was wrong and is now corrected.

`INCONCLUSIVE` is still the dominant outcome, and it is still a *real* outcome rather than the only
possible one. `ADR-0016_MEASUREMENT: PRODUCED`; the precondition remains `NOT_YET_DETERMINABLE`
because a traffic sample needs a live provider.

---

## 16. Reliability Regression

| suite | result |
|---|---|
| `tests/test_verification_contract.py` | **14 passed** (unmodified) |
| `tests/reliability/test_verification_evidence_adapter.py` (new) | **13 passed** |
| focused post-M13 + F8 + contract set | **220 passed** |
| `tests/reliability/` chunk, per file (F36 — cannot run in one process) | **378 passed, 0 failed** across 18 files **(corrected 2026-09-25: this row read "386", a counting error — the per-file data recorded by this phase sums to 378, and a fresh per-file run reproduces it file for file)** |
| canonical migration set (`CONTEXT.md` §11) | **849 tests — 848 pass, 1 fails** |
| `tests/test_harness_scaffolding.py`, `test_acceptance_verdict.py`, `test_invariant_single_source.py`, `test_repl_audit_pindown.py` | 136 passed, **1 failed** |

The two failures are **pre-existing and unrelated**: `test_node_identity.py::…test_a_parallel_round_is_journaled_as_one_exchange_per_call`
(**F38**, a test that encoded the F8 environment) and
`test_repl_audit_pindown.py::test_dead_daemon_with_model_is_unreachable_not_ok` (a provider-listing
test, present in `baseline-failures-stable.txt`). **New failures introduced by this phase: 0.**

Canonical set: **836 before → 849 after** (+13, exactly the new file's test count), with the same one
pre-existing failure.

---

## 17. F10 Attribution Status

**Already reconciled.** The ledger's F10 row was corrected during the F8 provisioning phase and reads:
*"CORRECTED 2026-09-24 (F8 provisioning): they were F8-caused, not pre-existing. The file now reads
39 passed, 0 failed."* Nothing further is needed, and this phase's evidence does not bear on it —
`test_13h2_determinism.py` is green here too (39 passed), consistent with the correction.

---

## 18. Remaining Risks

| Risk | Assessment |
|---|---|
| The name-scoped skip is narrower than the rule's letter | **Deliberate, and tested.** The rule's *purpose* — no non-`ok` result is treated as verification evidence — holds for every tool. Widening the skip to all tools would move a failed mutation from `FAIL` to `INCONCLUSIVE`, which the same brief forbids. `test_a_failed_mutation_still_counts_as_an_attempted_mutation` pins the current behaviour; if the wider rule is wanted it is a one-line change plus that test |
| `turns_used` no longer advances for a refused verification | A consequence of the skip. It only affects the grind-floor pacing, and only when a verify tool is refused *after* dispatch — rare, and the pre-dispatch denial path already behaved this way |
| `run_tests` still receives the envelope when its result is non-`ok` | Harmless: `_run_tests_is_evidence` uses `search`/`in`, never `startswith`, so it is not positional and was never broken. `_tool_result_output` returns `data` for its successful results anyway, which is strictly more correct |
| Historical journals keep the wrong verdict | Expected and documented (§14). A migration would be a separate decision |
| The defect class may exist elsewhere | The recon's duplicate-authority search found no second consumer of this input. `graph/verifier.py::gate_tests_green` reads a **typed int** and is a different subsystem |
| A future verify tool added to `_VERIFY_TOOLS` | Covered automatically — the adapter reads the authority's own set |

---

## 19. ADR Status

```text
ADR_REQUIRED: NO
```

Preserved from the recon, and re-confirmed against what was actually implemented. The repair changed
**no** contract: verification authority, the event contract, parser semantics, P3 semantics, replay
semantics and the failure taxonomy are all unchanged (§10–§14). No stop condition in §24 arose. No ADR
was written; the decision log remains at `ADR-0037`.

---

## 20. Exit Criterion

```text
=== EXIT CRITERION ===
FALSE_SUCCESS_REPRODUCED_BEFORE:   YES   (3 reachable shapes, measured)
FALSE_SUCCESS_AFTER:               0

FAILING_VERIFICATION:              verify_ok_after_edit=False, resolved=False,
                                   P3=fail, goal=goal_failed
SUCCESSFUL_VERIFICATION:           unchanged (True / pass / goal_met)

VERIFICATION_AUTHORITY:            unchanged (AST-identical file)
P3_AUTHORITY:                      unchanged (AST-identical file)
GOAL_AUTHORITY:                    unchanged (AST-identical file)
EVENT_CONTRACT:                    unchanged
TOOLEXECUTOR:                      unchanged (AST-identical file)
SECURITY:                          unchanged (AUTO_EDIT denies, ASK_ALL executes)
REPLAY:                            historical records preserved

NON_VACUITY:                       PROVEN (2 mutations, 6 and 2 failures, tree restored)
P3_REMEASUREMENT:                  PRODUCED (3/1/4 -> 2/2/4; one row changed)

STAGNATION_GATE:                   FALSE
ADR_REQUIRED:                      NO
UNAUTHORIZED_AUTHORITY_CHANGE:     NO

EXIT: COMPLETE
=======================
```

---

## 21. Final Status

```text
=== STATUS: COMPLETE ===
PHASE: POST-M13-VERIFICATION-EVIDENCE-ADAPTER-REPAIR
MODE: AUTHORIZED-IMPLEMENTATION

ROOT_CAUSE:
stateless.py evidence adapter passed JSON envelope
instead of envelope["data"]

REPAIR:
_tool_result_output() unwraps a SUCCESSFUL tool-output envelope to its `data`
and returns None otherwise; the evidence fold forwards that text to
note_tool_result, and for a verify tool (imported _VERIFY_TOOLS) skips the fold
entirely when there is none. Non-verify tools still fold, with no envelope.

PRODUCTION_FILES_CHANGED:
1   (wisp/core/stateless.py — 65 added, 3 removed, 3 hunks)

TEST_FILES_CHANGED:
1   (tests/reliability/test_verification_evidence_adapter.py — new, 335 lines, 13 tests)

FALSE_SUCCESS_BEFORE:
YES   (3 reachable shapes: failing command, tool-level error, blocked command)

FALSE_SUCCESS_AFTER:
0

FAILED_VERIFICATION:
PASS   (verify_ok_after_edit=False, resolved=False, P3=fail, goal=goal_failed)

SUCCESSFUL_VERIFICATION:
PASS   (verify_ok_after_edit=True, resolved=True, P3=pass, goal=goal_met)

VERIFICATION_AUTHORITY:
PRESERVED

P3_AUTHORITY:
PRESERVED

GOAL_AUTHORITY:
PRESERVED

SECURITY:
PRESERVED

REPLAY:
PRESERVED

P3_STAGE3B:
PASS 3->2, FAIL 1->2, INCONCLUSIVE 4->4 (unchanged);
exactly one row changed — mutate_failed_verification: pass/goal_met -> fail/goal_failed

ADR_REQUIRED:
NO

STAGNATION_GATE:
FALSE

NEXT:
the P3 Stage-3b measurement is now trustworthy for the mutating-verification
row. Two things remain open and are NOT authorised here:
  (a) the denial-vs-failure projection question — a denied verification yields
      P3 FAIL rather than INCONCLUSIVE, which the recon reported and this phase
      deliberately did not change;
  (b) the stagnation-gate enablement decision, which still needs a superseding
      ADR and the ADR-0016 measurement over a live-provider population.
The immediate next step is to re-run the measurement over a real traffic sample
once a live provider is available, then return to enablement.

EXIT:
COMPLETE
========================
```
