# PHASE P9 REPORT — Structured Delegation

| Field | Value |
|---|---|
| Phase | **P9** — the final phase in the plan |
| Baseline | P8 complete (`WISP_MIGRATION_STATUS.md`) |
| Status | **`PARTIAL`** — the two wiring fixes landed; five structural items deferred (§7) |
| Files changed | 3 modified (`auth/principal.py`, `auth/__init__.py`, `tool_executor.py`, `multi_agent/task.py`) |
| Tests added | `tests/test_structured_delegation.py` (25) |
| Rollback | additive — `ToolExecutor.principal` defaults to `None`, preserving today's behaviour exactly |

---

## 1. Executive summary

P9's plan names its own most important item: *"Wire `derive_subagent` … This is the single most
important fix in the delegation layer."* **It is now wired**, and a second latent defect — a live
`AttributeError` the plan described more mildly — is fixed.

| # | Plan item | Status |
|---|---|---|
| 1 | **Wire `derive_subagent`** | ✅ plumbed: `child_principal()` + `ToolExecutor.principal`; **the spawn site itself is deferred** (§7.1) |
| 7 | **Fix the latent budget defect** | ✅ `SubagentContract.metadata` added — it was worse than described (§2) |
| 6 | Wire or delete `_circuit_breaker.py` | ⚠️ **documented, not deleted** — it is the user's untracked WIP (§7.2) |
| 2 | Structured `child_goal` replacing `task: str` | ❌ deferred |
| 3 | Mandatory `result_schema` | ❌ deferred |
| 4 | Transactional effects | ❌ deferred |
| 5 | Typed failure replacing prose markers | ❌ deferred |
| 8 | Retire `multi_agent/dag.py` into `wisp/graph/` | ❌ deferred (already item M8) |

---

## 2. What was actually wrong — and item 7 was worse than the plan says

The plan says: *"`subagent_orchestrator.py:1316` writes `task.metadata`, but `SubagentContract` has no
`metadata` field, so `_runner._budget_from_contract` never sees the DAG node budget."*

**Verified, and sharper.** `SubagentContract` is a plain (non-frozen, non-slotted) dataclass with **no
`metadata` field**. So:

| Operation | Before |
|---|---|
| `contract.metadata` (**read**) | `AttributeError` |
| `contract.metadata = {}` (assign) | succeeds — a *dynamic* attribute |

Line 1316 is `if not task.metadata:` — a **read**. So the failure is not a silently-dropped budget: the
first time a DAG node declares a budget, the orchestrator **raises `AttributeError`** before it can
attach anything. A latent crash, not a silent omission.

Fixed by adding a documented `metadata: dict[str, Any] = field(default_factory=dict)` field. That makes
the read valid, the write typed rather than dynamic, and `_runner._budget_from_contract`'s
`contract.metadata["_budget"]` readable.

`test_the_orchestrators_write_now_succeeds` reproduces the exact two lines.

---

## 3. Implementation

### 3.1 Item 1 — the delegation layer's most serious defect

**The finding, verified.** `derive_subagent` is defined at `auth/principal.py`, re-exported from
`wisp.auth`, and called **only by two test files** — zero production callers. Meanwhile
`tool_executor.py` hardcodes:

```python
_decision = authorize(
    local_principal(workspace=workspace, profile=str(_profile)), …
```

`local_principal` returns a **`HUMAN`** principal with `capabilities=None` — **unbounded**. So every
subagent's tool call is authorized as the local human, with the parent's full authority *regardless of
what the child was asked to do*.

**The fix, in two parts:**

1. **`child_principal(parent, contract)`** — wraps `derive_subagent` with the one thing a caller should
   not re-derive: which tools the contract declared. It **delegates** rather than constructing a
   `Principal` itself, and an AST test pins that (a second narrowing implementation would be a second
   authority for what a child may do).
2. **`ToolExecutor(principal=…)`** — an additive keyword, defaulting to `None`, that the authorize
   consult prefers when set and otherwise falls back to `local_principal` exactly as before.

**The `["all"]` question, decided rather than guessed.** `tools == ["all"]` means "inherit the parent's
full toolset". That is answerable when the parent is **bounded** — the child inherits that exact set
(equal is not wider). It is **not** answerable when the parent is **unbounded**: there is no universe to
take a subset of, and both guesses are wrong — leave the child unbounded (the defect) or hand it an
empty set (a child that can call nothing). So it is **refused**, with a message naming the fix.

---

## 4. Verification

### 4.1 New tests — 25, all passing

| Plan requirement | Class | Proves |
|---|---|---|
| `test_subagent_capabilities_narrowed` | `TestSubagentCapabilitiesNarrowed` (11) | a child gets exactly the declared tools, is a `SUBAGENT` (not `HUMAN`), records its parent, inherits workspace/profile; **widening is refused**; narrowing twice stays narrow; `child_principal` delegates to `derive_subagent` (AST) |
| — | `TestExecutorAuthorizesAsAPrincipal` (4) | `ToolExecutor` accepts a principal, defaults to `None`, prefers it at the consult, and a narrowed child reaches `authorize()` |
| `test_dag_node_budget_applied` | `TestDagNodeBudgetApplied` (7) | the `metadata` field exists, defaults per-instance, makes the orchestrator's read/write pair work, and is documented as to *why* |
| — | `TestReachability` (3) | `child_principal` is exported; the circuit-breaker duplicate is **not** deleted; **and the spawn site is still unwired** — asserted so the gap cannot look closed |

The last one is deliberate: `test_derive_subagent_still_has_no_production_caller` is a **tripwire**. It
fails the moment someone wires the spawn site, with a message telling them to update §7 and delete the
test. A gap that is documented and asserted is a gap someone will close; a gap in a comment is not.

### 4.2 Regression

This phase modified **real production files** (`tool_executor.py`, `multi_agent/task.py`), unlike P7 and
P8 which only added unreferenced modules — so the comparison matters more here.

Verified against the **stable baseline** (`.workbuddy-ai/memory/baseline-failures-stable.txt`).

| Comparison | Result |
|---|---|
| Count | **129** |
| New failures vs the stable baseline | **none** (`comm -13` empty) |
| Absent failures vs the stable baseline | **none** (`comm -23` empty) |

**This is the strongest regression result of the migration**, because it is the only one where the
change touched files the rest of the suite exercises:

- `ToolExecutor.principal` is an **additive keyword defaulting to `None`**, so the authorize consult
  falls back to `local_principal` exactly as before. The byte-identical set is the evidence that the
  fallback is genuinely behaviour-preserving — not the claim.
- `SubagentContract.metadata` is a **new field with a `default_factory`**, so every existing
  construction is unaffected.

`ruff` / `mypy`: not installed.

---

## 5. Honest limits

- **The spawn site is not wired.** `child_principal` and `ToolExecutor.principal` exist and are tested,
  but nothing in the subagent spawn path calls them. The mechanism is complete; the integration is
  **M15**. This is the fifth consecutive phase whose remainder is integration (M11–M15).
- **Capability narrowing is therefore not enforced in production.** A child still runs unbounded today.
  The plan's own mitigation applies: *"it must be measured and staged"* — and measuring needs a working
  tool path (`jsonschema` is absent).
- **Items 2–5 and 8 are deferred** (§7).
- **`ruff`/`mypy` not installed.**

---

## 6. Completion criteria

| Criterion | Status |
|---|---|
| Capability narrowing is applied and tested | ⚠️ **tested**; not yet *applied* at the spawn site (M15) |
| A schema-violating result is rejected | ❌ not attempted (item 3) |
| Shared-workspace failure rolls back transactionally | ❌ not attempted (item 4) |
| One graph system | ❌ not attempted (item 8 — already M8) |
| No regression | ✅ see §4.2 |
| `ruff` / `mypy` | ❌ not installed |

---

## 7. Deviations from the plan

### 7.1 The spawn site is not wired (M15)

`derive_subagent`'s *reachability* is addressed: there is now a caller-shaped helper
(`child_principal`) and a place to pass the result (`ToolExecutor.principal`), both tested end to end
through `authorize()`.

What remains is the subagent spawn path constructing an executor with the child principal. That path is
the live subagent execution route, whose own regression suite
(`test_subagent_enterprise.py`, `test_13j1_fanout_contract_repair.py`) is **already red for
environmental reasons** — the same condition that deferred `dag.py`'s retirement (M8). Making an
authority change there now would make a regression the migration caused indistinguishable from one
already present.

**Recorded as M15, with a tripwire test rather than a comment.**

### 7.2 The circuit breaker was documented, not deleted

The plan says *"Wire or delete `multi_agent/_circuit_breaker.py` — it is imported nowhere."*

**Verified and refined.** It is imported by exactly one file: `tests/test_subagent_enterprise.py` —
which is a **foreign-session WIP file that does not even collect** (it is in the failure set as
`ERROR`). And a **second, wired** circuit breaker exists at `wisp/infra/circuit_breaker.py`, used by
`core/stateless.py:1083-1107` and `core/doctor.py`, with its own config keys and two passing test files.

So the accurate finding is a **duplicate authority**: one wired, one orphaned, near-identical APIs.

**Not deleted**, because `CONTEXT.md` §8 records this file as the **user's untracked WIP**. Deleting
someone's untracked file is not a decision a migration should make silently. Recommendation recorded;
the decision is theirs.

---

## 8. The migration's shape, at the end of the plan

Nine phases, and the pattern is consistent: **the mechanisms mostly existed**. What was missing was
callers.

| Category | Count |
|---|---|
| Phases that found the plan's claim needed narrowing | **6** (P0 ×2, P1, P2, P7, P8) |
| Phases that found the plan's *target component* was wrong | **2** (P2, P5) |
| Phases whose remainder is integration rather than construction | **5** (M11, M12, M13, M14, M15) |

Those five integration items are **one coherent piece of work**, and they share one prerequisite:
**M9** (the message list as a projection of the graph) plus **M2** (journal-first reconstruction). That
is the natural next phase, and it is bigger than any single item above.

The honest summary of the migration as delivered: **eight mechanisms built, tested, and reachable from
their packages — and not yet driven by the live turn loop.** That is a real, substantial body of work,
and it is not the same thing as a working Persistent Graph Loop.
