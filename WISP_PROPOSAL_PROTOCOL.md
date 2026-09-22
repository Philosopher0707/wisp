# WISP — PROPOSAL PROTOCOL

**Phase 0 design document. Conceptual only — nothing here is implemented.**

Defines the boundary between **reasoning** (which may be a model, and may be wrong) and **state**
(which must be durable, validated, and replayable).

> **Principle 2 (target):** *Reasoning should produce proposals. Reasoning should not automatically
> have unrestricted authority over state.*

---

## 0. Why a Proposal Layer

The audit found that in the live loop, model output reaches effects **directly**:
`core/stateless.py:812` calls `ToolExecutor.execute` with raw arguments, and the only object between
the model and the host is the argument dict.

That is not unsafe — the 19-gate pipeline (`tool_executor.py:653`) is genuinely strong. But it means
three things cannot be stated, and therefore cannot be enforced:

1. **What was proposed** — there is no record of intent, only of arguments.
2. **Why it was legal** — the verdict is computed and discarded; `authorize()` returns
   `controlling_layer` (`auth/decision.py:23-30`) and nothing persists it.
3. **What would make it illegal next time** — no re-validation is possible without replaying the call.

The proposal layer fixes all three by making the *proposed transition* a durable, typed, inert object.

---

## 1. Protocol Shape

Every proposal is a value. Nothing happens until a validator rules and the controller applies.

```
propose ──► Proposal ──► validate ──┬─► LEGAL   ──► apply ──► transition recorded ──► effect/state
   ▲                                └─► ILLEGAL ──► rejection recorded ──► no effect
   │
 (model | deterministic rule | recovery controller | operator)
```

**Invariants:**
- P1 — A proposal never mutates anything. It is data.
- P2 — Validation is total: every proposal receives a verdict, including refusals.
- P3 — The verdict is recorded with the proposal, even when the proposal is rejected.
- P4 — Application is idempotent under the proposal's idempotency key.
- P5 — Rejection is never silent. A rejected proposal is observable and, where legal, retryable.

---

## 2. Common Envelope

All proposal types share:

```
ProposalEnvelope
  proposal_id    : UUID, minted at creation
  type           : NodeCreate | NodeTransition | GraphExpand | GraphInvalidate | ToolCall |
                   EvidenceRecord | VerificationRequest | DelegationRequest | RecoveryDecision
  task_ref       : the task/goal this serves (may be null for goal-level proposals)
  proposer       : who proposed it  (MODEL | RULE:<name> | RECOVERY | OPERATOR)
  model          : model id + prompt hash, when proposer == MODEL
  payload        : the type-specific body
  created_at     : timestamp
  idempotency    : key = hash(type, task_ref, canonical(payload))       # dedupe + replay safety
  preconditions  : assertions that must hold before application
  parent         : the transition that caused this proposal (provenance chain)
```

**Provenance is mandatory** — this is the mechanism that makes Principle 5 ("Evidence should have
provenance") true for *decisions*, not just artifacts.

---

## 3. The Nine Proposal Types

### 3.1 NodeCreate

| Aspect | Definition |
|---|---|
| **Purpose** | Add a task to the graph — the *only* way topology grows. |
| **Inputs** | `goal_ref`, `task_spec` (type, contract, function/tool refs), `depends_on[]`, `criteria_refs[]`, `budget`, `invalidation_rule` |
| **Outputs** | a `Task` in `PENDING`, plus edges from `depends_on` |
| **Preconditions** | the referenced goal exists; every `depends_on` resolves to a real task; `task_spec.type` is a known `NodeType` |
| **Validation** | schema; acyclicity; `validate_graph` (`graph/validator.py`); resource profile (`optimizer_passes._profile_ok`); policy for the capability the task will use |
| **Provenance** | proposer + model + parent transition |
| **Idempotency** | by `(type, task_ref, canonical(spec))` — a duplicate create is a no-op returning the existing id |
| **Failure behavior** | ILLEGAL → recorded, no topology change; the proposer is told why |
| **State impact** | new node; dependents may become READY |
| **Required evidence** | none at creation; the node must later produce evidence to reach `SUCCEEDED` |

**Today:** node creation exists **only at compile time** (`graph/optimizer_passes.py:346-367`).
Runtime creation is structurally absent (audit §7.1, §22).

### 3.2 NodeTransition

| Aspect | Definition |
|---|---|
| **Purpose** | The **only** way a task changes state. |
| **Inputs** | `task_ref`, `from_state`, `to_state`, `reason`, `evidence_refs[]` |
| **Preconditions** | `(from_state, to_state)` is a legal edge (§5.3 of the audit's target table); the caller is the GraphController |
| **Validation** | transition table; **`SUCCEEDED` additionally requires non-invalidated evidence satisfying every required criterion** (invariant R4) |
| **Provenance** | the decision that produced it |
| **Idempotency** | keyed on `(task_ref, to_state, attempt)`; re-application is a no-op |
| **Failure behavior** | ILLEGAL → recorded; state unchanged |
| **State impact** | the node's state; possibly dependents' readiness; possibly the goal's state |
| **Required evidence** | for `SUCCEEDED`: evidence refs. For `FAILED`: a `Failure` ref. For `INCONCLUSIVE`: a reason. |

**This is the single most important type in the protocol.** It is the mechanism that replaces the
audit's finding of "~30 direct mutation sites, 11 of them unpersisted, with no transition API"
(§7.1) with one validated, journaled, idempotent write path.

### 3.3 GraphExpand

| Aspect | Definition |
|---|---|
| **Purpose** | Grow the graph in response to new information, without replacing it. |
| **Inputs** | `parent_task_ref`, `expansion_kind` (DECOMPOSE \| REPAIR \| DISCOVER \| DIAGNOSE), `new_tasks[]`, `edges[]`, `justification` |
| **Preconditions** | the parent task exists and is not terminal; the expansion preserves acyclicity |
| **Validation** | full `validate_graph` on the **resulting** graph; budget headroom (graph growth is a budget, audit §24); depth bound |
| **Provenance** | proposer + the observation that motivated it |
| **Idempotency** | keyed on `(parent_task_ref, expansion_kind, canonical(new_tasks))` |
| **Failure behavior** | ILLEGAL → the graph is unchanged; the proposer may propose a different expansion |
| **State impact** | new nodes; parent may move to `WAITING`; dependents recomputed |
| **Required evidence** | the observation(s) that justify the expansion |

**Distinction from NodeCreate:** `NodeCreate` adds an independent task; `GraphExpand` adds a
*sub-structure* attached to an existing task, and is the mechanism by which local replanning happens.

### 3.4 GraphInvalidate

| Aspect | Definition |
|---|---|
| **Purpose** | Mark state or evidence as no longer trustworthy. |
| **Inputs** | `target_refs[]` (tasks, evidence, or criteria), `cause` (MUTATION \| STALENESS \| SUPERSESSION \| EXTERNAL_CHANGE), `scope` (LOCAL \| CASCADE), `justification` |
| **Preconditions** | the target exists; the cause is a known cause |
| **Validation** | cascade closure must terminate; a task may not be invalidated while `CLAIMED` without cancelling it first |
| **Provenance** | the mutation or observation that caused invalidation |
| **Idempotency** | keyed on `(target_refs, cause)` |
| **Failure behavior** | ILLEGAL → recorded; nothing invalidated |
| **State impact** | tasks → `INVALIDATED`; their `SUCCEEDED` dependents → `INVALIDATED` (cascade) or `READY` (re-verify) |
| **Required evidence** | the change that caused it (e.g. a file mutation with its hash) |

**Today:** evidence invalidation exists as a *flag* — `core/verification.py:149` nulls prior
verification on a later mutation, pinned by `test_verification_contract.py:72-78`. Cascading
invalidation and `INVALIDATED` as a state do not exist (audit §22, §23).

### 3.5 ToolCall

| Aspect | Definition |
|---|---|
| **Purpose** | Request an effect. The bridge from reasoning to `ToolExecutor`. |
| **Inputs** | `task_ref`, `tool`, `args`, `intent` |
| **Preconditions** | the tool is advertised for the current mode; the task is `RUNNING` |
| **Validation** | **composes the existing pipeline, unchanged:** `check_dangerous_command` (`tools/_utils.py:76`), `policy_hard_deny` (`infra/security.py:66-79`), `authorize()` L0–L5 (`auth/decision.py:42`), plan-mode, permission mode, approval (`core/approval_gate.py:54`), budget |
| **Provenance** | proposer + model + prompt hash |
| **Idempotency** | `hash(task_ref, tool, canonical(args))` — **closes the duplicate-execution risk** (audit §20) |
| **Failure behavior** | DENIED → the verdict is returned to the proposer as a structured refusal (not an exception); the loop may propose a different action |
| **State impact** | none directly; produces `Observation`s |
| **Required evidence** | none to call; the observation is the result |

**Design constraint:** this type must **not** re-implement authorization. It carries the request; the
validator calls the existing gates and records `controlling_layer` (which `authorize()` already
returns and today discards).

### 3.6 EvidenceRecord

| Aspect | Definition |
|---|---|
| **Purpose** | Attach provenance-bearing evidence to a claim. |
| **Inputs** | `claim_ref`, `kind`, `strength`, `content_ref`, `derived_from[]` (observations), `observed_at` |
| **Preconditions** | every `derived_from` observation exists |
| **Validation** | schema; **every evidence must cite at least one observation** (invariant R2); content hash must verify for artifact-backed evidence (`graph/artifacts.py:107` already does this) |
| **Provenance** | full chain to the producing action and task |
| **Idempotency** | keyed on `(claim_ref, kind, content_hash)` |
| **Failure behavior** | ILLEGAL → evidence not recorded; the claim cannot then reach `SUCCEEDED` |
| **State impact** | none directly; enables `NodeTransition` to `SUCCEEDED` |
| **Required evidence** | itself |

### 3.7 VerificationRequest

| Aspect | Definition |
|---|---|
| **Purpose** | Ask for a verdict against criteria. |
| **Inputs** | `task_ref`, `criteria_refs[]`, `evidence_refs[]`, `independence` (REQUIRED \| PREFERRED \| NOT_REQUIRED), `deadline` |
| **Preconditions** | criteria exist and are resolvable; the task is `OBSERVED` |
| **Validation** | the criteria set is non-empty **or** the request is explicitly marked as having none (→ the verdict can only be `INCONCLUSIVE`) |
| **Provenance** | who requested it and why |
| **Idempotency** | keyed on `(task_ref, criteria_refs, evidence_refs)` |
| **Failure behavior** | if the verifier is unavailable, the result is `INCONCLUSIVE`, never a silent `PASS` |
| **State impact** | task → `VERIFYING` |
| **Required evidence** | the evidence the verdict will rest on |

**Independence:** when `independence == REQUIRED`, the validator must confirm that the verifier is not
the acting invocation. In a single-model deployment this is satisfied structurally — separate context,
separate prompt, criteria the actor never saw (see `WISP_VERIFICATION_ARCHITECTURE.md` §5).

### 3.8 DelegationRequest

| Aspect | Definition |
|---|---|
| **Purpose** | Delegate work to a subagent through a typed contract. |
| **Inputs** | `parent_task_ref`, `structured_task` (goal ref + inputs + required outputs), `role`, `capabilities[]`, `isolation` (WORKTREE \| SHARED), `result_schema` (**mandatory**), `budget`, `deadline` |
| **Preconditions** | depth and branching budgets permit it; `capabilities` is a subset of the parent's |
| **Validation** | **`derive_subagent(parent, capabilities)` must be applied** (`auth/principal.py:62` — implemented, tested, and today never called in production); depth ≤ `max_subagent_depth`; branch ≤ `max_subagent_branching`; result schema present |
| **Provenance** | the parent task and the reason for delegation |
| **Idempotency** | keyed on `(parent_task_ref, canonical(structured_task), capabilities)` |
| **Failure behavior** | a child failure produces a typed `Failure`, never a prose marker (`subagent_orchestrator.py:933,1577` today) |
| **State impact** | a child scope; on completion, evidence + artifacts flow back to the parent |
| **Required evidence** | the child's result must satisfy `result_schema` to be admissible |

**Closes three audited gaps:** unbounded child principal (audit §8.5 item 2), free-text delegation
inputs (audit §17.2), and non-transactional shared-workspace effects (audit §17.3 item 11).

### 3.9 RecoveryDecision

| Aspect | Definition |
|---|---|
| **Purpose** | Choose the next move after a failure or stagnation. |
| **Inputs** | `subject_ref` (failure or stagnation signal), `failure_class`, `candidates[]` (legal rungs), `chosen`, `reason`, `budget_cost` |
| **Preconditions** | the failure is classified; the chosen rung has budget remaining |
| **Validation** | the rung must be legal for the failure class (the ladder's transition rules, `WISP_RECOVERY_ARCHITECTURE.md` §3); budgets must permit |
| **Provenance** | the detection point and the classifier |
| **Idempotency** | keyed on `(subject_ref, chosen, attempt)` |
| **Failure behavior** | no legal rung → `ESCALATED_TO_HUMAN`, which is a **terminal, honest** outcome |
| **State impact** | the affected tasks (retry → `PENDING`; replan → `GraphExpand`; escalate → `WAITING`) |
| **Required evidence** | the failure record and its evidence |

---

## 4. Validation as a Single Composable Gate

The validator is the **only** place legality is decided. It must **compose** the existing
authorities, never replace them:

```
validate(proposal) -> Verdict
  1. schema            — is the proposal well-formed?
  2. preconditions     — do the referenced objects exist and hold?
  3. structural        — graph/validator.py: acyclicity, contracts, governance, resources, security
  4. policy            — SecurityPolicy.check  (infra/security.py:190)
  5. authorization     — authorize() L0-L5     (auth/decision.py:42)   [records controlling_layer]
  6. budget            — BudgetGovernor
  7. transition rules  — the legal state edges
  8. evidence rules    — R2 (evidence cites observations); R4 (SUCCEEDED requires evidence)
```

**Ordering rationale:** cheap structural checks first; policy and authorization before anything that
could touch the host; evidence rules last because they are the strongest.

**The existing gates keep their exact semantics and their order within `ToolExecutor`.** This layer
adds a *record* of the verdict, not a new decision procedure.

---

## 5. Authority and Idempotency Summary

| Proposal | Who may propose | Validator | Who applies | Idempotency key |
|---|---|---|---|---|
| NodeCreate | MODEL, RULE, OPERATOR | structural + policy | GraphController | (type, task_ref, spec) |
| NodeTransition | GraphController, RECOVERY | transition table + evidence | GraphController | (task_ref, to_state, attempt) |
| GraphExpand | MODEL, RECOVERY | structural + budget | GraphController | (parent, kind, tasks) |
| GraphInvalidate | RULE, RECOVERY, OPERATOR | cascade closure | GraphController | (targets, cause) |
| ToolCall | MODEL | full tool gate chain | ToolExecutor | (task_ref, tool, args) |
| EvidenceRecord | verifier, executor | schema + R2 + hash | EvidenceStore | (claim, kind, hash) |
| VerificationRequest | GraphController, RULE | criteria resolvable | Verifier | (task, criteria, evidence) |
| DelegationRequest | MODEL, GraphController | `derive_subagent` + budgets | DelegationBroker | (parent, task, caps) |
| RecoveryDecision | RECOVERY | ladder rules + budget | GraphController | (subject, rung, attempt) |

**Rule:** a model may propose `NodeCreate`, `GraphExpand`, `ToolCall`, `DelegationRequest`.
A model may **never** propose `NodeTransition`, `GraphInvalidate`, or `RecoveryDecision` — those are
consequences of validation and evidence, not of assertion. This is the concrete expression of
Principle 2.

---

## 6. Failure Behavior — Uniform Contract

Every proposal, legal or not, produces a `ProposalOutcome`:

```
ProposalOutcome
  proposal_id  : the proposal
  verdict      : LEGAL | ILLEGAL
  layer        : which validation layer decided (mirrors authorize().controlling_layer)
  reason       : human-readable
  applied      : bool
  transition   : the recorded transition id, if applied
  retryable    : bool
  recorded_at  : timestamp
```

**A rejection is a first-class, observable event.** The audit found that today the agent path
computes `controlling_layer` and discards it (`auth/decision.py:23-30`); the proposal layer makes the
refusal's *reason* durable, which is what makes "the agent kept trying something it was not allowed
to do" diagnosable rather than mysterious.

---

## 7. What This Protocol Must Not Become

| Anti-pattern | Why it must be avoided |
|---|---|
| A second authorization system | `authorize()` + `SecurityPolicy` are composed, not duplicated. The audit found the *cost* of two decision models (G1, `PHASE_10_AUTHORIZATION_PARITY.md`). |
| A second outcome classifier | `OutcomeClass` is canonical and AST-enforced (`test_outcome_classification_authority.py`). |
| A second graph | `wisp/graph/` is the graph. `multi_agent/dag.py` should be retired into it. |
| A proposal type with no consumer | This is the repository's dominant defect (eight instances found). Every type above has a named consumer. |
| Proposals that carry effects | A proposal is data. If it holds a live handle, a callback, or a session object, it is not a proposal. |
| Renaming for resemblance to the research report | Brief §34: names follow the repository, not the report. `NodeCreate` etc. are conceptual labels, not prescribed class names. |
