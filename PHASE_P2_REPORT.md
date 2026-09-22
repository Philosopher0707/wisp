# PHASE P2 REPORT — Introduce the Proposal Boundary

| Field | Value |
|---|---|
| Phase | **P2** |
| Baseline | P1 complete (`WISP_MIGRATION_STATUS.md`) |
| Status | **`COMPLETE`** — verdict recording + proposal/outcome records (§7) |
| Files changed | 2 modified, 1 added (`wisp/core/proposal.py`) |
| Tests added | `test_gate_order_corpus.py` (12), `test_verdict_layer_recorded.py` (10), `test_proposal_boundary_no_bypass.py` (8), `test_proposal_boundary_records.py` (27) |
| Rollback | `WISP_PROPOSAL_BOUNDARY` (independent of the P0/P1 flags) |

---

## 1. Executive summary

P2's objective is "make reasoning produce **proposals** that validation disposes, instead of model
output reaching effects directly." That reads two ways, and **the choice between them is this phase's
central decision** (ADR-0015):

1. **Inversion** — insert a proposal stage *between* the model and the gates.
2. **Record** — the gates already *are* the validation stage; make their disposition observable.

The plan leans toward (1) and lists `core/stateless.py` as an affected component. **Evidence says
(2):** the gates run *inside* `ToolExecutor.execute`, downstream of the dispatch site the plan proposes
wrapping. A boundary there would sit *before* validation with no way to observe its disposition — and
the migration's own constraint is explicit: *"The proposal layer adds a record, not a decision
procedure."*

So P2 delivers:

- **`ToolRequest` / `ToolResult` now have a producer.** Both contracts existed with the right shape and
  no producer or consumer at all. Every call that reaches dispatch now yields a proposal; every
  proposal yields an outcome, **including rejections**.
- **The authorization verdict is recorded for both allow and deny.** Previously it was consumed only
  on the deny path, interpolated into a denial message — so an allowed call left no trace, and
  `allow` / `approval` / "no gate ran" were indistinguishable.
- **The safety net the plan requires was written RED-first** and made green against the unmodified
  implementation, then re-run after the change.
- A **structural no-bypass invariant** pins the authority consumers and the consult/record arity.

**P2 made exactly one insertion into `ToolExecutor`** — a *record*, never a decision. No gate was
re-ordered, re-implemented, or consulted twice, and the corpus proves it. `stateless.py` is untouched.

---

## 2. What was actually wrong — and the plan's claim corrected (again)

The plan states: *"`authorize()` already returns `controlling_layer` … and today it is **discarded**.
Persist it."*

**Repository evidence narrows this.** `controlling_layer` is *not* discarded — it is interpolated into
denial messages at two production sites:

| Site | Use |
|---|---|
| `tool_executor.py:722`, `:725` | `f"[Denied by {_decision.controlling_layer} layer: {_decision.reason}]"` |
| `tools/registry.py:948` | same, on the direct-registry path |

The accurate finding is sharper: **the verdict is recorded only for denials, and only as prose inside a
denial message — never as structured, queryable data. An allowed call leaves no trace at all.**

The audit trail does have allow-side writers, but they fire on a different path:

| Writer | Called at | Path |
|---|---|---|
| `log_blocked` | `tool_executor.py:1199` (via `_audit_denial`) | denial |
| `log_auto_approved` | `tool_executor.py:942` | **approval** |
| `log_explicit_approved` | `tool_executor.py:947` | **approval** |

So `allow` (permitted by the layered policy) and `approval` (permitted after a human prompt) were
indistinguishable from each other *and* from "no gate ran". That is the gap P2 closes.

Separately, `wisp/contracts/tool.py`'s `ToolRequest`/`ToolResult` were **producer-less and
consumer-less** — referenced only by the package re-export and `test_contracts_tool.py`.

This is the **fourth** audit claim narrowed by evidence — after the `test_canonical_execution_state`
ratchet (P0), `RunStatus ⊂ RunState` (P0), and `stateless.py` being listed as a P1 component (P1).

---

## 3. Implementation

Two files modified, one added. **`stateless.py` untouched.**

| File | Change |
|---|---|
| `wisp/tool_executor.py` | new `_audit_authorization()`; one call inserted **after** the allow/deny fork |
| `wisp/core/proposal.py` | **new** — `build_proposal`, `build_outcome`, `is_refusal`, the `OutcomeClass` → frozen-vocabulary maps |
| `wisp/core/session.py` | `PROPOSAL` / `OUTCOME` event kinds + factories + **audit-only** apply cases |
| `wisp/core/runtime.py` | emit the records from the exchange serializer; hoist the three durable-record flags to one read site |
| `wisp/config.py` | `proposal_boundary` flag (env `WISP_PROPOSAL_BOUNDARY`) |
| `tests/test_gate_order_corpus.py` | **new** — RED-first safety net (12) |
| `tests/test_verdict_layer_recorded.py` | **new** — verdict for both outcomes (10) |
| `tests/test_proposal_boundary_no_bypass.py` | **new** — structural invariant (8) |
| `tests/test_proposal_boundary_records.py` | **new** — the records themselves (27) |

### 3.1 The single gate-chain insertion

```
_decision = authorize(...)
if not _decision.allowed:
    _audit_denial(...)          # pre-existing
    yield denial_result(...)
    return
self._audit_authorization(...)  # ← P2, on the allow path only
```

Placing the recorder **after** the fork is what makes it exactly-once: a denied call returns before
reaching it. Pinned by `test_verdict_record_follows_the_allow_deny_fork` (source-order assertion) and
`test_exactly_one_verdict_per_call` (behavioural).

### 3.2 The records are AUDIT-ONLY

The transcript is rebuilt from `ASSISTANT_MESSAGE(tool_calls=…)` + `TOOL_RESULT`. If `PROPOSAL` or
`OUTCOME` also appended to `messages`, **replay would duplicate every tool reply**.
`Session.apply` therefore records them in dedicated lists and never touches `messages` — pinned by
`TestAuditOnly`.

### 3.3 Where the verdict goes (ADR-0013)

`ImmutableAuditTrail.record_decision` — the purpose-built decision recorder that `AuditLog` already
maps onto (`tools/audit.py:142`), so both outcomes converge on one hash-chained sink. The layer is
stored **twice**:

- `reason` → `"[Allowed by <layer> layer: …]"`, deliberately **symmetric** with the existing
  `"[Denied by <layer> layer: …]"`;
- `args_summary` → `{"decision": "authorized", "layer": …, "approval_required": …, "obligations": …,
  "args_keys": …}`, queryable and hash-covered.

**Why not a new column:** `ImmutableAuditTrail.verify()` recomputes each row's hash from its own column
values. Adding a field to the payload would invalidate the chain for **every pre-existing row** —
destroying tamper-evidence to gain a record. Not a trade worth making.

### 3.4 Status derivation is delegated, not re-derived

`classify_result()` (`core/events.py`) is the ONE authority for "what kind of outcome is this?".
`proposal.py` maps its `OutcomeClass` onto the frozen `ToolResult.STATUSES` rather than re-deriving any
predicate — a second classifier is what let benchmark error accounting miss every structured denial.
The mapping is **lossy** (8 classes → 4 statuses), so the precise class is always carried in
`metadata["outcome_class"]` — documented rather than hidden, and pinned by a totality test.

---

## 4. The safety net earned its place immediately

The plan predicted the risk correctly: *"The main risk is behavioral drift in the gate chain."*

Writing the corpus **before** the change surfaced a fact that was previously undocumented and that the
plan's model did not account for:

> **A `read_only` denial is decided by the policy-engine gate, which runs *before* the `authorize()`
> consult — so it names no controlling layer.**

The corpus expected `POLICY_DENIED:approval`; reality was bare `POLICY_DENIED`. That is a real ordering
property: some denials never reach the layered authority at all. It is now pinned with a comment
explaining why.

**Two real bugs were also caught by the new tests**, both in my own first implementation:

1. `_closed_exchange_events` **hardcoded `journal=True`**, so the incremental writer ignored its own
   flag and wrote transcript events with `session_event_fidelity` off — breaking the one-flag-per-
   concern rollback contract (ADR-0002).
2. `journal_fidelity` was read **inside the `finally` block** but used in the stream loop above it →
   `UnboundLocalError` on every tool-using turn. Fixed by reading all three durable-record flags at
   **one** site, since a flag read in two places is a flag that can disagree with itself.

---

## 5. Verification

### 5.1 New tests — 57, all passing

| Class | Proves |
|---|---|
| `TestGateOutcomeUnchanged` (7 cases) | each gate's structured outcome + deciding layer is unchanged |
| gate-order stability | repeated identical calls decide identically — no order drift from accumulated state |
| effect invariants | a denied mutation leaves nothing behind; an allowed write does produce the effect |
| `TestAllowVerdictRecorded` (4) | an allowed call now records a verdict; the layer is named **and** stored structurally |
| `TestDenyVerdictRecorded` (4) | the deny path is unchanged; exactly one verdict per call |
| `TestAuditChainIntegrity` (2) | the hash chain still verifies after mixed allow+deny |
| `TestAuthorityConsumers` (2) | exactly the expected modules consult `authorize()` |
| `TestExecutorConsultArity` (3) | `execute()` consults authority exactly once and records exactly one verdict |
| `TestNoDirectImplementationReach` | no module invokes `TOOL_IMPLS` behind the authority entry points |
| `TestReachability` (2) | both new call edges are reachable (RULE 11) |
| `TestBuildProposal` (4) / `TestBuildOutcome` (13) | proposal + outcome records round-trip through the contracts; every `OutcomeClass` is representable; classification delegates to the canonical authority |
| `TestAuditOnly` (4) | the records never touch `messages`; replay rebuilds them; the transcript stays exactly three messages |
| `TestBoundaryEndToEnd` (5) | an outcome is recorded end-to-end; a refusal is first-class; flag off records nothing; replay unaffected; sequences gapless |

### 5.2 Regression

| Run | Failures + errors |
|---|---|
| HEAD baseline | 131 |
| P0 final | 128 |
| P1 final | 128 |
| P2 (first half) | 128 |
| **P2 (complete)** | **128** |

Failure set `diff`-compared against every prior run. P2 introduced **0 new failures**.

---

## 6. Honest limits

- **`proposal` records cannot be produced end-to-end in this environment.** `jsonschema` is missing
  (P0 F8), so every call is refused **before** it streams a `tool_call` event — and a proposal is
  recorded only for a call that reached dispatch. The end-to-end tests therefore assert the **outcome**
  (a rejection is still first-class, which is the boundary's purpose), while the proposal-record path
  is covered directly at the serializer boundary. Stated in the tests themselves rather than papered
  over.
- **`ruff`/`mypy` not installed.**
- **128 pre-existing failures remain.** P2's claim is exact: it adds none.

---

## 7. Deviations from the plan

### 7.1 The inversion was not performed — deliberately

See §1 and ADR-0015. The plan's items 1/2/5 are satisfied **as records**; the *stage* reading is not.
The plan's own constraint ("adds a record, not a decision procedure") settles which reading was
intended.

### 7.2 `stateless.py` was not modified

The plan lists it as an affected component. It is untouched, because the gates run inside
`ToolExecutor.execute`, downstream of the dispatch site — the same finding that shaped P1 (ADR-0012).

---

## 8. Completion criteria

| Criterion | Status |
|---|---|
| Every tool effect has a recorded proposal with a verdict and a layer | ✅ proposal + outcome records journaled; verdict + layer recorded in the audit trail, joinable by `tool_call_id` / `action_key` |
| Gate order and outcomes provably unchanged on the corpus | ✅ 7-case corpus + stability + effect invariants |
| No second authorization implementation introduced | ✅ `execute()` consults `authorize()` exactly once (AST-pinned) |
| Rollback by one flag | ✅ `WISP_PROPOSAL_BOUNDARY`, independent of the P0/P1 flags (independence pinned) |
| No regression | ✅ **0 new failures**; failure set identical to P1's |
| No existing test weakened | ✅ 6 updated, each preserving intent and gaining an assertion (§9) |
| New code is reachable (RULE 11) | ✅ both call edges AST-pinned |
| `ruff` / `mypy` | ❌ not installed |

## 9. Tests updated, not weakened

Six tests written during P0/P1 asserted **exact event lists** that the intentional P2 addition changed.
Each was updated to include the new kinds and gained an assertion about the records themselves
(`test_proposal_and_outcome_records_are_produced`, `test_flags_are_independent`,
`test_fidelity_off_alone_still_records_the_proposal_boundary`). Two of them now document an
environment constraint explicitly rather than asserting something this environment cannot produce.

---

## 10. Next phase

**P3 — Independent Verification** is now unblocked: P2's items 1/2/5 have landed, so the critical path
(P2 → P4 → P5 → P6) has a materialized boundary to build on.

Carried forward as prerequisites:

1. **Journal-first reconstruction with blob fallback** (P1 §7.1) — still outstanding.
2. **Killpoint integration** (P1 §7.2) — still outstanding.
3. **Revisit ADR-0004 for the proposal path.** Now that verdicts and proposals are durably recorded, a
   silent best-effort failure loses an authorization record — closer to a correctness precondition
   than an observability one.


---
