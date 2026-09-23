# PHASE M15 REPORT — A Subagent Authorizes as a Narrowed Child

| Field | Value |
|---|---|
| Item | **M15** — the subagent spawn site |
| Predecessor | P9 (built the plumbing; recorded this as the gap) |
| Status | **`COMPLETE`** |
| Decision | `WISP_ARCHITECTURE_DECISIONS.md` **ADR-0030** |
| Files changed | 4 production, 2 test files |
| Tests added | `tests/test_child_principal_wired.py` (**22**) |
| Rollback | none needed — `principal=None` preserves the previous behaviour exactly |

---

## 1. What was wrong

The plan calls narrowing a subagent's authority *"the single most important fix in the delegation
layer."* P9 built the plumbing — `auth.principal.child_principal()` and `ToolExecutor(principal=…)` — and
recorded the spawn site as the remaining gap, with a **tripwire test** asserting it was still unwired.

Verified: `_runner._run_agent` builds the child core with `tool_executor=self._tool_executor` — the
**parent's** executor, whose `principal` is `None`. So `ToolExecutor.execute` fell back to
`local_principal(...)`: a `HUMAN` principal with `capabilities=None`, i.e. **unbounded**.

**Every child's tool call was authorized as the local human.**

The child's *tool list* was already narrowed (`session_dict["allowed_tools"]`, enforced by the core as a
schema filter). What was missing is the **authorization identity**: L1 of `authorize()` denies a tool the
principal lacks, and the principal was always the unbounded human.

---

## 2. Why not a per-child executor

The obvious fix — build a `ToolExecutor` per child with `principal=…` — is wrong here, and reading the
constructor is what shows it. `ToolExecutor.__init__` creates **two** `ThreadPoolExecutor`s
(`_tool_pool`, `_network_pool`) whose shutdown the composition root owns:

> *"Both pools are closed by the composition root's owned-shutdown path; threads that outlive a timeout
> die with the interpreter only if shutdown never runs."*

A per-child executor would create two pools per subagent that **nothing ever closes**, and `fanout`
spawns many. So the identity travels with the **call**, not the object. That reasoning is the ADR's
substance, and `test_the_runner_does_not_construct_a_tool_executor` is the ratchet that keeps it.

---

## 3. What landed

| File | Change |
|---|---|
| `wisp/auth/principal.py` | `executor_principal()` — the precedence authority; `child_principal(…, capabilities=…)` — an explicit override that narrows only |
| `wisp/tool_executor.py` | `execute(..., principal=None)`; `_effective_principal()` resolving per-call > executor > local human |
| `wisp/core/stateless.py` | forwards `session.get("principal")` to `execute` |
| `wisp/multi_agent/_runner.py` | `_child_principal()`; stamps it into **both** child session dicts |
| `tests/test_child_principal_wired.py` | **new** — 22 tests |

### One authority for "who is the parent"

`executor_principal(tool_executor, …)` resolves the parent's identity by the **same rule the executor
uses for its own calls**. The runner uses it to derive the child, so a child's `parent_principal_id`
cannot name a principal its parent's own calls never authorize as. A second route to "who is the parent"
would be exactly the defect class this migration exists to remove.

### The `["all"]` case, resolved rather than guessed

`_effective_child_tools` turns `"all"` (and the permission mode) into a concrete list *before* the
principal is derived. `child_principal` correctly refuses to **guess** at `"all"` for an unbounded
parent — and here there is nothing to guess, so the resolved list is passed explicitly. The override
narrows only: `derive_subagent` still refuses a widening, and a test asserts it.

### Both child paths, because there are two

`_run_agent` (stateless core) and `_run_via_runtime` (`AgentRuntime`) build **separate** session dicts.
Wiring one and not the other is the half-fix this migration keeps finding, so
`test_both_child_paths_stamp_the_principal` asserts both by AST.

---

## 4. What actually changed, and the ordering fact

The child inherits the parent's **permission mode** but not the parent's **contract**, and the principal
layer is now the gate that enforces the contract:

| Gate | Question | Runs |
|---|---|---|
| policy engine | is this tool permitted **in this mode**? | first — and names no controlling layer (**F15**) |
| `authorize()` L1 | does this **principal** have this capability? | second |

This mattered for the tests. My first attempt used `run_bash` as the forbidden tool and it failed —
because the policy gate denies `run_bash` in `auto_edit` **before** `authorize()` runs. The correct
probe is a tool the mode **permits** and the contract **excludes**: `write_file`.

Verified end to end:

```
child declared ["read_file"], calls write_file in auto_edit
  -> POLICY_DENIED  "[Denied by principal layer: principal a876… lacks capability write_file]"

child declared ["read_file"], calls read_file
  -> executes normally
```

`test_the_mode_gate_denies_before_the_principal_consult` pins the ordering so a future reader does not
conclude the principal layer is redundant. It is not: the two gates answer different questions.

---

## 5. The tripwire worked

P9 shipped a tripwire asserting the spawn site was unwired, whose failure message said:

> *"the spawn site is now wired — update `PHASE_P9_REPORT.md` §7 (M15) and delete this test"*

It **fired** on the first run after the wiring, with exactly that message. That is the tripwire doing
what it was written for: the gap could not be closed silently, and the closure could not be forgotten.
It is replaced by its inverse in the new suite, with a pointer left in its place.

---

## 6. Completion criteria

- [x] The spawn site derives and passes a narrowed child principal — **both** child paths
- [x] A child is **denied at the authorization layer** for a tool its contract excludes
- [x] A child can still call what its contract allows
- [x] The parent's identity is resolved by the **shared** rule, so the parent pointer cannot disagree
- [x] `["all"]` is resolved rather than guessed, and the override narrows only
- [x] No per-child executor — the thread-pool leak is ratcheted against
- [x] The P9 tripwire fired and was replaced by its inverse
- [x] **Zero new failures** — see §7
- [ ] `ruff` / `mypy` — not installed

## 7. Regression

Verified against the **stable baseline** (`.workbuddy-ai/memory/baseline-failures-stable.txt`).

| Comparison | Result |
|---|---|
| Count | **129** |
| New vs the stable baseline | **none** (`comm -13` empty) |
| Absent vs the stable baseline | **none** (`comm -23` empty) |

This phase changed **four production files**, two of them on the live subagent path, so the comparison
carries weight. The delegation/auth scope was checked explicitly first — 27 files, 511 tests, **14
failures, every one of them verified present in the stable baseline** (11 `cryptography`/`jsonschema`
`ModuleNotFoundError`s in `test_policy_modes.py`, 2 `jsonschema` failures in `test_no_bypass.py`, 2
pre-existing `test_tool_executor_shared_state.py` identity failures). **Zero new in that scope.**

`ruff` / `mypy`: not installed.

---

## 8. Honest limits

- **The child's *tool list* was already narrowed.** `allowed_tools` filters the schemas the child is
  offered and the core rejects disallowed calls. M15 adds the **authorization** layer behind it, so a
  call that bypassed the core's filter (a bug, a prompt-injected tool name, a direct executor call) is
  now denied rather than permitted. It is defence in depth, not the first line — and it is the line that
  produces the audit record naming a `SUBAGENT` principal.
- **No subagent turn was driven end to end.** The child core is constructed inside `_run_agent`, which
  needs a provider and a workspace; the tests drive the runner's principal derivation, the executor's
  per-call consult, and the core's forwarding, but not a full child turn. `jsonschema` is absent, so a
  child's tool call is denied at the schema gate in this environment regardless.
- **The mode is still inherited, not narrowed.** A child whose contract declares `run_bash` still cannot
  use it in `auto_edit`, because the mode gate runs first. Narrowing the *mode* for a child is a separate
  change and is not attempted here.
- **`_child_principal` returns `None` when there is no executor.** A runner constructed without one has
  no authorization layer at all; the core's own no-executor fallback already denies anything but safe
  reads, so this is consistent rather than a new hole — but it is a path with no principal, and it is
  stated rather than implied.
