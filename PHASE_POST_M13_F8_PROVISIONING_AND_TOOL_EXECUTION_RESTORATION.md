# PHASE POST-M13 — F8 PROVISIONING & REAL TOOL EXECUTION RESTORATION

**Mode: AUTHORIZED-IMPLEMENTATION.** The primary repair was provisioning, not code — **0 production changes,
0 manifest changes, 0 default changes**. `jsonschema 4.26.0` was installed **entirely offline** from the local
uv cache, at exactly the locked versions.

| Field | Value |
|---|---|
| `HEAD` | `7c15626630f6dca8f37e27c638e59df0acb216e5` · branch `main` |
| Production changes | **0** |
| Config defaults changed | **0** |
| Lockfile / manifest | **unchanged** (SHA-256 verified before and after) |
| Dependencies installed | **5** — all at locked versions, none added to the project |
| Real tool execution | **PROVEN** |
| P3 Stage-3b measurement | **PRODUCED** (with a stated contamination) |
| Verdict | `EXIT: COMPLETE` |

---

## 1. Mission Result

**F8 is gone.** A structurally valid tool call now passes validation and a tool genuinely executes:
`read_file` returned real content, `write_file` created a real file on disk, and an approved `run_bash`
executed a real command and captured its stdout.

The repair was exactly what the recon predicted — **provisioning**. The dependency was already declared in
`pyproject.toml` and already locked in `uv.lock`; the virtualenv simply did not have it. It was installed
**offline**, from the wheels already in the local uv cache, at the exact versions the lock names.

Three things were established beyond "the import works":

1. **Authorization and approval are intact and authoritative.** The default `auto_edit` mode still
   **hard-denies** shell; an approved `ask_all` turn really executes. F8's repair did not soften either.
2. **24 previously-failing tests were F8-caused, not pre-existing.** The whole reliability chunk is now
   green — **0 failed**, where it read 24 failures before. *(This line said "385 passed"; corrected
   2026-09-25 to a counting error — the figure was **365** at this phase; see the correction note in §13.)*
3. **Provisioning exposed a pre-existing verification-evidence defect** that F8 had been masking: a mutation
   followed by a **failing** verification is recorded as P3 **PASS**. This is reported, not repaired
   (`VerificationFloorGuard` changes are explicitly out of scope), and it contaminates one row of the
   measurement — which is stated rather than hidden.

---

## 2. Entry Criterion

```
=== ENTRY CRITERION ===
F8_RECON_COMPLETE: YES
ROOT_CAUSE_CONFIRMED: YES
JSONSCHEMA_DECLARED: YES
JSONSCHEMA_LOCKED: YES
JSONSCHEMA_ABSENT_FROM_VENV: YES
PRIMARY_REPAIR_IS_PROVISIONING: YES
ADR_REQUIRED_FOR_PRIMARY_REPAIR: NO
LOCAL_WHEEL_CACHE_AVAILABLE: YES
IMPLEMENTATION_AUTHORIZED: YES
ENTRY: PASS
========================
```

---

## 3. Baseline (recorded before any modification)

| Fact | Value |
|---|---|
| `HEAD` / branch | `7c15626630f6dca8f37e27c638e59df0acb216e5` · `main` |
| Tree | 41 tracked modifications (**20 under `wisp/`**), 60 untracked |
| Interpreter | CPython **3.12.8** · venv `<repo>/.venv` |
| `jsonschema` | `pip show` → *not found*; `import` → `ModuleNotFoundError` |
| `uv` on `PATH` | **no** — but present at `~/.local/bin/uv` (0.12.5) and `/opt/homebrew/bin/uv` |
| `.venv/bin/pip` | **broken shebang** — `#!/Users/philosopher/Documents/wisp/.venv/bin/python3.12`, the pre-move path. The "obvious" repair (`pip install`) would have failed for this reason alone |
| `uv.lock` SHA-256 | `0b318e2b6830c69e1e6a777dfff0a418e99cdb049c011a22f5f06f65126a3841` |
| `pyproject.toml` SHA-256 | `b5302a289e2305b7ef58a4e68e0a3617a307889403345a39c70388030256e5ae` |
| Declared dependency | `pyproject.toml` → `[project].dependencies` → `"jsonschema>=4.0",` |

---

## 4. Provisioning Method

**Offline, from the existing cache — no network was used at all.**

`uv` is not on `PATH`, but the binary exists (recon §5's "inspect whether a uv binary exists elsewhere").
The cache holds the **unpacked** wheels, not just metadata:

```
~/.cache/uv/wheels-v6/pypi/jsonschema/4.26.0-py3-none-any -> ~/.cache/uv/archive-v0/UpANzW03KhgE3wWnCkLPM
```

Dry-run first, to confirm the resolution against the lock before writing anything:

```
$ UV_OFFLINE=1 uv pip install --python .venv/bin/python --offline --dry-run 'jsonschema==4.26.0'
Resolved 6 packages in 43ms
Would install 5 packages
 + attrs==26.1.0  + jsonschema==4.26.0  + jsonschema-specifications==2025.9.1
 + referencing==0.37.0  + rpds-py==2026.6.3
```

Every one of those versions was then checked against `uv.lock` **before** installing:

| Package | `uv.lock` | Resolved | Match |
|---|---|---|---|
| `attrs` | 26.1.0 | 26.1.0 | ✓ |
| `jsonschema` | 4.26.0 | 4.26.0 | ✓ |
| `jsonschema-specifications` | 2025.9.1 | 2025.9.1 | ✓ |
| `referencing` | 0.37.0 | 0.37.0 | ✓ |
| `rpds-py` | 2026.6.3 | 2026.6.3 | ✓ |

```
$ UV_OFFLINE=1 uv pip install --python .venv/bin/python --offline 'jsonschema==4.26.0'
Installed 5 packages in 10ms
```

**No newer version was resolved, no lockfile was modified, no project metadata was touched, and no
certificate workaround was needed.** §6's TLS path was not required and was not used.

---

## 5. Dependency Verification

```
JSONSCHEMA:
DECLARED   = YES   (pyproject.toml, [project].dependencies)
LOCKED     = YES   (uv.lock, 4.26.0)
INSTALLED  = YES
IMPORTABLE = YES
VERSION    = 4.26.0
```

Integrity, verified after the install:

| Artefact | Before | After | Verdict |
|---|---|---|---|
| `uv.lock` | `0b318e2b…3841` | `0b318e2b…3841` | **unchanged** |
| `pyproject.toml` | `b5302a28…e5ae` | `b5302a28…e5ae` | **unchanged** |
| `wisp/core/stateless.py` | `a50deabe…95cc` | `a50deabe…95cc` | **unchanged** |
| `wisp/` modified files | 20 | 20 | **unchanged** |
| tracked modifications | 41 | 41 | **unchanged** |

The install touched only `.venv/lib/python3.12/site-packages/`. No incidental resolver update occurred.

---

## 6. F8 Regression

The original reproduction re-run unchanged. **The discriminator flipped:**

| Measure | Before | After |
|---|---|---|
| valid call refused? | **True** | **False** |
| invalid call refused? | True | True |
| same refusal text? | **True** | **False** |
| refusal names the dependency? | **True** | **False** |

```
VALID_CALL_REFUSED_BY_MISSING_DEPENDENCY = 0
```

And the refusals are now **schema-specific** rather than a dependency error:

| Input | Result |
|---|---|
| `read_file(path="a.txt")` | `None` — **accepted** |
| `read_file(path="a.txt", offset=0, limit=10)` | `None` — **accepted** |
| `read_file()` | `'path' is a required property` + the schema excerpt |
| `read_file(path=123)` | `123 is not of type 'string'` |
| `read_file(path="a.txt", nonsense=True)` | `None` — **accepted** (see §9) |
| `not_a_registered_tool(...)` | `None` — the intentional delegation, unchanged |

---

## 7. Real Tool Execution

`.workbuddy-ai/memory/post-m13-f8-provisioning/real_tool_execution.py` — four turns through the **live
`AgentRuntime.run_turn`** with a real `ToolExecutor`, against a temporary workspace:

| Turn | Mode | Result |
|---|---|---|
| `read_file` | AUTO_EDIT | **real content returned** — `"--- FILE: input.txt \| LINES: 1 …"` |
| `write_file` | AUTO_EDIT | **real file created on disk** — `existed_before=False`, `exists_now=True`, body exact |
| `run_bash` | AUTO_EDIT | **`POLICY_DENIED`** — hard-denied by mode, *not executed* |
| `run_bash` | ASK_ALL + approval | **really executed** — stdout `"f8-restored\n"` captured |

```
REAL TOOL EXECUTION: PROVEN (6/6 checks passed)
TOOL_EXECUTION_RESTORED: YES
```

The mutation was temporary, isolated to a `tempfile.mkdtemp()` workspace, and the workspace was removed
afterwards. A separate check asserts **no stray artefacts** in the repository root.

**Both halves matter.** Turn 3 proves authorization was not softened by the repair; turn 4 proves the
approval path leads to real execution. A repair that made everything run would have been a regression, not
a fix.

---

## 8. Validation Behavior

`wisp/core/stateless.py::_validate_tool_args` — the canonical authority — now genuinely evaluates:

```
$ _validate_tool_args("read_file", {"path": "a.txt"})
None                              # valid
$ _validate_tool_args("read_file", {})
"Schema validation failed for tool 'read_file': 'path' is a required property …"
```

The `except Exception` at `stateless.py:2289` is **byte-identical** to its pre-phase state (`stateless.py`
SHA `a50deabe…` unchanged) — see §11.

---

## 9. Invalid-Argument Tests

`tests/reliability/test_f8_tool_execution_restored.py` — 11 tests, **all passing**:

| Assertion | Result |
|---|---|
| `jsonschema` is importable | pass |
| it is still a declared runtime dependency | pass |
| a valid call is not refused by a missing dependency | pass |
| an invalid call is refused for a **schema** reason, not a dependency one | pass |
| valid and invalid no longer share one message | pass |
| an unknown tool still defers to the security layer | pass |
| a real read returns the file content | pass |
| a real write reaches the disk | pass |
| authorization still hard-denies in the default mode | pass |
| an approved command really executes | pass |
| a schema-invalid call never executes | pass |

**`additionalProperties` was not tightened.** `read_file(path="a.txt", nonsense=True)` is still accepted,
because 41 of 42 schemas leave `additionalProperties` unset. That is the declared contract; §13's
"do not tighten" was honoured.

**One nuance worth recording:** `write_file` **salvages** a missing `path` to `./output.txt`
(`stateless.py:2280-2283`), so "missing required argument" is not reachable for that tool — it is a
documented repair, not a validation gap. My first version of the test asserted otherwise and was wrong.

---

## 10. Nullable-Argument Exposure

```
NULLABLE_ARGUMENT_CONTRACT: EXPOSED
```

The recon predicted this. Now that validation actually runs, the class is visible:

```
read_file(limit=None)   -> "limit: Expected type number, got NoneType"   (rejected)
read_file(offset=None)  -> rejected
```

**No schema anywhere declares `"type": "null"`** or a union containing `null`. So a model that emits
`"timeout_seconds": null` for an unset optional field is **correctly** refused by the declared schema, and
hostilely refused in practice.

**Not fixed.** This is a schema-policy question, explicitly out of scope for this phase, and it is a
contract decision rather than an F8 repair.

---

## 11. Error-Classification Status

```
SECONDARY_DEFECT_REMAINS
```

The broad handler is unchanged (`stateless.py:2289` — verified by AST, SHA-identical file):

```python
try:
    import jsonschema
    jsonschema.validate(instance=args, schema=schema)
    return None
except Exception as exc:                    # catches ModuleNotFoundError too
    return f"Schema validation failed for tool '{name}': {exc}"
```

**Determined, not changed.** §14 permits the minimal correction only if it preserves the denial taxonomy,
requires no new event class, changes no authority and changes no model-facing contract. It does not survive
that test:

| Candidate correction | Why it fails the gate |
|---|---|
| import `jsonschema` at module scope | absence would then break the whole engine at **import** time — a larger behaviour change than the defect, and beyond this phase's scope |
| report "validator unavailable" distinctly | introduces a **distinct outcome** for a failure that is not an argument problem — a new denial class, which is an ADR |

**With the dependency guaranteed present in supported installs, the path is unreachable during normal
operation.** It remains a real hazard for any incomplete install, and it is recorded as such rather than
papered over. **No production code was changed.**

---

## 12. Verification Path

```
PROVEN — and it was not provable before this phase.

valid tool proposal
  -> validation PASS            (a valid call returns None)                    §6
  -> authorization              (AUTO_EDIT hard-denies shell; ASK_ALL allows)  §7
  -> approval if required       (the handler was consulted: approvals=['run_bash'])
  -> ToolExecutor               (real, config-driven)
  -> REAL tool                  (a file was created; a command ran)
  -> tool result reaches runtime (captured from the live event stream)          §7
  -> verification observes it   (guard.note_tool_result ran — see §13 caveat)
```

---

## 13. P3 Stage-3b Measurement

`.workbuddy-ai/memory/post-m13-f8-provisioning/p3_stage3b_measurement.py`. The verdicts are the
**recorded** `acceptance_verdict` from the `GOAL_STATE` journal record — the runtime's own P3 projection,
not a re-derivation.

| # | Turn shape | tools | exec | mut | ver | P3 verdict | goal state |
|---|---|---|---|---|---|---|---|
| 1 | `no_tools` | 0 | 0 | 0 | 0 | `inconclusive` | `goal_unverified` |
| 2 | `read_only` | 1 | 1 | 0 | 0 | `inconclusive` | `goal_unverified` |
| 3 | `mutate_unverified` | 1 | 1 | 1 | 0 | `fail` | `goal_failed` |
| 4 | `mutate_verified` | 2 | 2 | 1 | 1 | `pass` | `goal_met` |
| 5 | `mutate_failed_verification` | 2 | 2 | 1 | 1 | **`pass`** ⚠ | `goal_met` |
| 6 | `schema_rejected_call` | 1 | 0 | 0 | 0 | `inconclusive` | `goal_unverified` |
| 7 | `policy_denied_call` | 1 | 0 | 0 | 0 | `inconclusive` | `goal_unverified` |
| 8 | `full_coding` | 3 | 3 | 1 | 1 | `pass` | `goal_met` |

```
population              8 evaluated turns (stated, not sampled)
P3 PASS                 3        (one of which is contaminated — see below)
P3 FAIL                 1
P3 INCONCLUSIVE         4
INCONCLUSIVE rate       4/8 = 50.0%
tool-executing turns    5/8
mutation turns          4/8
verification turns      3/8
turns with NO verdict   0        (no infrastructure failure in the population)
F8-shaped refusals      0
```

### The measurement is contaminated in one row, and that is stated

Row 5 **should be `fail`**: the turn mutated code and then ran a command that exited 3. It is recorded as
`pass`. Corrected, the population reads **2 PASS / 2 FAIL / 4 INCONCLUSIVE** — the INCONCLUSIVE rate is
unchanged at 50.0%, but the PASS/FAIL split is not trustworthy without the correction.

### The population is stated, not sampled

This is a **matrix over the reachable turn shapes**, driven through the real runtime with real execution.
It is **not** a traffic sample: producing one requires a live provider, which this environment does not
have. The INCONCLUSIVE rate here characterises the *gate's behaviour over the input space*; a production
rate would need real model traffic. Saying otherwise would be the same error as quoting the degenerate
100% from the F8 environment.

---

## 14. ADR-0016 Measurement

```
ADR-0016_MEASUREMENT: PRODUCED (with the stated contamination and the stated population)
```

The chain ADR-0035 §9 was waiting on now exists end to end:

```
F8 gone
  -> tools execute                      §7
  -> mutations land                     §7
  -> wrote_code becomes True            §13 rows 3,4,5,8
  -> verification evidence is produced  §13 rows 4,5,8
  -> P3 returns PASS / FAIL / INCONCLUSIVE on real inputs
  -> the INCONCLUSIVE rate is measurable  = 50.0% over the stated population
```

**What this measurement is.** The first P3 Stage-3b population in which `INCONCLUSIVE` is a *real*
outcome — an unverified turn — rather than the only possible one. The F8 environment could produce nothing
else; this one produces all three verdicts.

**What it is not.** Not a traffic sample, and not yet free of a known defect (§15).

```
ADR-0016_PRECONDITION: NOT YET DETERMINABLE
```

The precondition is not *unmet* — the measurement now exists and is honest. It is **not yet determinable**
as satisfied because (a) the population is a matrix rather than traffic, and (b) row 5 shows the acceptance
verdict can be wrong in the false-success direction, which is precisely the property ADR-0016 exists to
bound. Deciding the precondition is a separate judgement, and §17 of the brief forbids making it here.

**The stagnation gate was not enabled and its default was not changed.**

---

## 15. Reliability Regression

`tests/reliability/` cannot run in one process on this host (F36), so it was chunked per file and unioned.

| File | Before | After |
|---|---|---|
| `test_13h2_determinism.py` | **6 failed**, 33 passed | **39 passed** |
| `test_13j1_fanout_contract_repair.py` | **13 failed**, 35 passed | **48 passed** |
| `test_13j_fanout_contract.py` | **5 failed**, 5 passed | **10 passed** |
| `test_f8_tool_execution_restored.py` (new) | — | **11 passed** |
| the other 13 files | 0 failed | 0 failed |
| **chunk total** | **24 failed** | **0 failed · 365 passed** *(corrected 2026-09-25 from "385" — a counting error; see the note below)* |

> **Correction (2026-09-25).** This row read **385 passed**, and line 35 of this report repeats it. Both
> are wrong. The directory measures **378** today, and the F37 phase *later added* 13 tests to it
> (`test_verification_evidence_adapter.py`), so the total at the time of this phase was **365**.
> The derivation is checkable: `378 − 13 = 365`. **The 24→0 transition this phase established is
> unaffected** — that is a set comparison, not a count.

```
F8_blocked_tests_before: 24
F8_blocked_tests_after:  0
new_failures:            0
resolved_failures:       24
```

The focused stagnation / post-M13 set is unchanged at **182 passed** *(the F8-era figure; the same
8-file set measures **220** as of 2026-09-25, after the F37 and F39 phases added tests to it)*.

### Every newly-exposed failure, classified

There are **no new failures** in this chunk. The 24 resolved ones classify as:

```
F8_FIXED -> TEST_ASSUMED_BROKEN_ENVIRONMENT      (24/24)
```

They were recorded in the ledger and in `MEMORY.md` as *pre-existing* (`test_13h2_determinism.py` was
logged as F10 with "6 pre-existing failures at baseline"). **That attribution was wrong**, and this phase
is the evidence: they were F8-caused. The ledger's F10 row and the `MEMORY.md` environment table need
correcting — noted in §20.

None of them was a schema-policy failure, so §19's "do not immediately repair newly exposed schema-policy
failures" did not arise.

**The full-suite baseline (129) was measured in the F8 environment and is now stale.** Re-measuring it
requires chunking the whole suite (F36); that is a follow-up, not a claim made here.

---

## 16. Thin-Tools / ACP Preservation

```
THIN_TOOLS_BEHAVIOR:            UNCHANGED
ACP_VALIDATION_ARCHITECTURE:    UNCHANGED
```

| Claim | Evidence |
|---|---|
| `thin_tools=True` still shows only the primitives | `_get_tool_schemas()` → `['exec_sandbox', 'fs_mutate', 'git_checkpoint']` |
| the engine lookup is still a no-op there | `_validate_tool_args('exec_sandbox', {'command': …})` → `None` |
| ACP still reaches `ToolExecutor` without engine validation | `grep -c _validate_tool_args wisp/acp_session.py` → **0** |
| no second validation path was introduced | `jsonschema` appears in exactly one module (`wisp/core/stateless.py`) |

Provisioning changed what the validator *can do*, not *where* it is consulted. Both pre-existing
reachability findings are preserved, unrepaired, and now confirmed unaffected.

---

## 17. Security / Authority Verification

```
validation -> authorization -> approval -> execution      PRESERVED
```

| Check | Result |
|---|---|
| malformed calls cannot reach approval | validation runs first (`stateless.py:511`), and its refusal `continue`s before the gate (`:529`) |
| hard policy DENY cannot be overridden | `run_bash` in AUTO_EDIT → `POLICY_DENIED`, no prompt, not executed (§7 turn 3) |
| valid calls can reach execution | §7 turn 4 — a real command ran after approval |
| `ToolExecutor` remains the execution authority | all four turns route through it |
| `VerificationFloorGuard` remains the verification authority | unchanged file; `note_tool_result` is still the only writer |
| no authority migration | 0 production changes |

`UNAUTHORIZED_AUTHORITY_CHANGE: NO`.

---

## 18. Non-Vacuity

The restoration tests were checked by **removing the thing they depend on**:

```
$ python -c "sys.modules['jsonschema'] = None"   # simulate the F8 environment
  _validate_tool_args('read_file', {'path': 'a.txt'})
  -> "Schema validation failed for tool 'read_file': import of jsonschema halted; None in sys.modules"
```

A **valid** call is refused again, and the refusal blames the dependency. `test_a_valid_call_is_not_refused_
by_a_missing_dependency` fails on that input, and `test_a_real_write_reaches_the_disk` cannot pass without a
tool actually running. The suite therefore detects:

| Regression | Detected by |
|---|---|
| the dependency goes missing | the valid-call test (demonstrated above) |
| the validator is bypassed and invalid args reach execution | `test_a_schema_invalid_call_never_executes` |
| validation becomes permissive | the schema-reason test |
| tool execution is blocked again | the real-read / real-write tests |
| authorization softens | the AUTO_EDIT denial test |

---

## 19. Production Changes

```
production_files_changed:   0
production_lines_changed:   0
config_defaults_changed:    0
```

The preferred outcome was reached, so §24's escalation path never opened. `wisp/core/stateless.py` is
SHA-identical to its pre-phase state (`a50deabe…95cc`), and the secondary error-classification defect was
**determined, not repaired** (§11).

Files written by this phase:

| Path | Kind |
|---|---|
| `tests/reliability/test_f8_tool_execution_restored.py` | **new** — 11 tests |
| `PHASE_POST_M13_F8_PROVISIONING_AND_TOOL_EXECUTION_RESTORATION.md` | **new** — this report |
| `.workbuddy-ai/memory/post-m13-f8-provisioning/` | evidence: the real-tool driver, the P3 measurement, the defect proof, per-file results |
| `WISP_MIGRATION_STATUS.md`, `.workbuddy-ai/memory/MEMORY.md`, `.workbuddy-ai/memory/2026-09-24.md` | records |

Plus the environment: **5 packages installed into `.venv`**, at locked versions, with no manifest change.

---

## 20. Remaining Risks

| # | Risk | Assessment |
|---|---|---|
| 1 | **A mutation followed by a FAILING verification is recorded as P3 PASS / GOAL_MET.** `stateless.py:924` reads `result_event["result"]`, which is an *envelope*, and stringifies it — so the text handed to `note_tool_result` never begins `[exit code:` and `_verify_result_is_success` returns True for every `run_bash`. | **HIGH — a false success, in the exact class ADR-0035 exists to prevent.** Pre-existing, masked by F8, exposed by this phase. Proven in 8 checks. **Not repaired** (`VerificationFloorGuard` is out of scope). Needs its own phase. |
| 2 | The **full-suite baseline (129) is stale** — measured in the F8 environment, and at least 24 of its entries were F8-caused. | **Medium.** Requires a chunked full-suite run (F36). |
| 3 | **Attribution in the record is now wrong**: `test_13h2_determinism.py`'s 6 failures are logged as pre-existing (F10), and `MEMORY.md` lists `jsonschema` as a missing dependency that "pip cannot fix". | **Low, but it will mislead.** Corrected in `MEMORY.md` this phase; the ledger's F10 row is flagged. |
| 4 | The **nullable-argument contract** is now visible (§10). | **Medium.** A schema-policy decision, deliberately not taken. |
| 5 | The **secondary error-classification defect** remains (§11): a missing dependency is still reported as an argument verdict. Unreachable in a supported install. | **Low** now, high consequence if it recurs. |
| 6 | `.venv/bin/pip` has a **broken shebang** from the project move. `python -m pip` works; the `pip` shim does not. | **Low.** Recorded; it is why the obvious repair would have failed. |

---

## 21. Enablement Implication

**Nothing is enabled.** `stagnation_gate` remains `false`, as do `goal_state`, `recovery_ladder`,
`record_verdict` and `task_graph`; `graph_oscillation_guard` remains `true`.

What this phase changes for the enablement question is only this: **the measurement ADR-0035 §9 was waiting
on now exists.** It is honest, it is bounded, and it carries a stated contamination. The precondition is
`NOT YET DETERMINABLE` — not unmet, and not satisfied.

**One caution for whoever decides it:** risk 1 means a `GOAL_MET` can currently be produced by a turn whose
verification failed. Enabling enforcement on top of that would be measuring a gate whose downstream verdict
is not yet trustworthy in the false-success direction. That is an argument for fixing risk 1 first, not for
leaving F8 unprovisioned.

---

## 22. Exit Criterion

```
DEPENDENCY_PROVISIONED:          YES
JSONSCHEMA_IMPORTABLE:           YES
LOCKFILE_UNCHANGED:              YES
PROJECT_METADATA_UNCHANGED:      YES
F8_VALID_CALL_REFUSAL:           0
REAL_TOOL_EXECUTION:             PROVEN
INVALID_ARGUMENT_VALIDATION:     PROVEN
AUTHORIZATION_ORDER:             PRESERVED
APPROVAL_ORDER:                  PRESERVED
VERIFICATION_PATH:               PROVEN (with the §13 caveat recorded)
P3_MEASUREMENT:                  PRODUCED
ADR_0016_MEASUREMENT:            PRODUCED
STAGNATION_GATE_DEFAULT:         FALSE
UNAUTHORIZED_AUTHORITY_CHANGE:   NO
```

No hard blocker occurred: no valid call is refused, no invalid call executes, authorization and approval
ordering are unchanged, verification is not bypassed, the lockfile and project metadata are byte-identical,
no second validator authority was introduced, the stagnation gate is off, and the measurement is not
contaminated by an *infrastructure* failure — its one contaminated row is a **verification-evidence
defect**, which is stated rather than hidden.

### Exact metrics

```
jsonschema_before_importable:       NO
jsonschema_after_importable:        YES
jsonschema_version:                 4.26.0
dependencies_installed:             5        (attrs, jsonschema, jsonschema-specifications, referencing, rpds-py)
dependencies_added_to_project:      0
lockfile_changed:                   NO
valid_calls_before:                 0 accepted / 2 refused
valid_calls_after:                  2 accepted
invalid_calls_after:                3 rejected (+1 unknown tool passing by design)
real_tool_calls:                    4
real_tool_successes:                3
real_tool_failures:                 1        (POLICY_DENIED by design, not a failure of the repair)
mutations_executed:                 1
verification_events:                1
P3_total:                           8
P3_PASS:                            3        (1 contaminated — corrected: 2)
P3_FAIL:                            1        (corrected: 2)
P3_INCONCLUSIVE:                    4
P3_INCONCLUSIVE_RATE:               50.0%
F8_blocked_tests_before:            24
F8_blocked_tests_after:             0
new_failures:                       0
resolved_failures:                  24
production_files_changed:           0
production_lines_changed:           0
config_defaults_changed:            0
```

---

## 23. Final Status

```
=== STATUS: COMPLETE ===
PHASE: POST-M13-F8-PROVISIONING-AND-TOOL-EXECUTION-RESTORATION
MODE: AUTHORIZED-IMPLEMENTATION

JSONSCHEMA:
DECLARED: YES
LOCKED: YES
INSTALLED: YES
IMPORTABLE: YES
VERSION: 4.26.0

F8_VALID_CALL_REFUSAL:
0

REAL_TOOL_EXECUTION:
PROVEN

VALIDATION:
PRESERVED

AUTHORIZATION:
PRESERVED

APPROVAL:
PRESERVED

VERIFICATION:
PROVEN

P3_MEASUREMENT:
PRODUCED

P3_INCONCLUSIVE_RATE:
50.0% (4/8, over a stated matrix population; one PASS row is contaminated
by a pre-existing verification-evidence defect)

ADR_0016_MEASUREMENT:
PRODUCED

ADR_0016_PRECONDITION:
NOT_YET_DETERMINABLE

PRODUCTION_CHANGES:
0

CONFIG_DEFAULT_CHANGED:
0

STAGNATION_GATE:
FALSE

NEXT:
1. A dedicated phase for the newly-exposed verification-evidence defect: a mutation
   followed by a FAILING verification is recorded as P3 PASS / GOAL_MET. It is
   pre-existing, out of this phase's authority, and it sits directly under the
   completion contract. Fix it before any enablement decision.
2. Re-measure the full-suite failure set (the recorded 129 is stale and was measured
   in the F8 environment); correct F10's attribution in the ledger.
3. Only then return to the stagnation-gate enablement question, which still needs a
   superseding ADR (ADR-0037) in addition to ADR-0016's measurement.

EXIT:
COMPLETE
========================
```
