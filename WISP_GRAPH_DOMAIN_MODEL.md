# WISP — GRAPH DOMAIN MODEL

**Phase 0 design document. Conceptual only — nothing here is implemented.**

Defines the domain vocabulary for the target Persistent Graph Loop, decides which concepts are
**first-class**, and justifies each decision against what the repository already has.

---

## 0. The Decision Rule

A concept earns first-class status when **all three** hold:

1. **It is written and read by more than one component** (otherwise it is a local variable).
2. **It must survive a process boundary** (otherwise it belongs in memory).
3. **It carries authority or provenance** (otherwise it is a string).

Anything failing this test stays a field, an annotation, or prose — and this document says so
explicitly, because the failure mode of this codebase is *building types nobody reaches*.

---

## 1. The Concept Register

| Concept | Status | Rationale | Existing artifact to build on |
|---|---|---|---|
| **Goal** | **FIRST-CLASS** | read by planner, verifier, context, recovery, termination; must persist; carries acceptance criteria | `graph/planner.py:54` `objective` (string only); `coding.py:40` `TaskContext` |
| **AcceptanceCriteria** | **FIRST-CLASS** | the verifier's input; the termination oracle; must persist | `benchmark/tasks.py:29` `verify` callable (nearest analogue, benchmark-only) |
| **Task** | **FIRST-CLASS** | the unit of durable work; already exists in two shapes | `graph/types.py:115` `GraphNode`; `multi_agent/dag.py:20` `TaskNode` |
| **Action** | **FIRST-CLASS** | must be validated before it happens; must be journaled | `contracts/tool.py:15-80` `ToolRequest` (**exists, unwired**) |
| **Observation** | **FIRST-CLASS** | the executor's only output; distinct from evidence | *nothing* — `core/stateless.py:1914` produces a raw string |
| **Evidence** | **FIRST-CLASS** | carries provenance; invalidatable; the verifier's raw material | `graph/types.py:305` `GraphArtifact`; `VerificationResult.evidence: list[str]` |
| **Verification** | **FIRST-CLASS** | a stage, a request, a result, a policy | `graph/types.py:285` `VerificationResult`; `core/verification.py:119` |
| **Failure** | **FIRST-CLASS** | drives recovery; needs a closed taxonomy | `graph/types.py:273` `NodeFailure` (`failure_code` is a free string) |
| **Decision** | **FIRST-CLASS** | the recovery/router output; must be replayable | `graph/verifier.py:19` `VALID_DECISIONS` (a tuple) |
| **Artifact** | **FIRST-CLASS** | content-addressed output with provenance | `graph/types.py:305`, `graph/artifacts.py:56-92` (**already excellent**) |
| **Delegation** | **FIRST-CLASS** | crosses a trust boundary; must be a contract | `multi_agent/task.py:57` `SubagentContract` |
| **Human Intervention** | **FIRST-CLASS** | a durable, resumable state — not a blocking call | `core/approval_gate.py:54`; `graph/types.py:41` `AWAITING_APPROVAL` |
| **Hypothesis** | **NOT first-class (yet)** | no component reads it; would be speculation until an experiment loop exists | — |
| **Budget** | **FIELD on Goal/Task** | real and necessary, but it is a parameter, not an entity | `multi_agent/resource_budget.py` `ResourceBudget` |
| **Plan** | **DERIVED** | a plan *is* the graph proposal; a separate Plan type would duplicate it | `graph/planner.py:331` proposal statuses |
| **Intent** | **NOT first-class** | subsumed by Goal + AcceptanceCriteria | — |
| **Belief** | **NOT first-class** | no mechanism consumes it | — |

**Tally: 12 first-class, 2 field/derived, 3 deliberately excluded.**

---

## 2. First-Class Concepts in Detail

### 2.1 Goal

```
Goal
  goal_id            : stable identity, minted once
  statement          : the objective, in the operator's words
  acceptance         : AcceptanceCriteria[]        (may be empty -> INCONCLUSIVE, not SUCCEEDED)
  constraints        : scope, forbidden paths, budgets
  provenance         : who/what produced it (operator | goal interpreter | parent goal)
  parent_goal_id     : optional, for decomposed goals
  created_at         : timestamp
  state              : GOAL_OPEN | PLANNING | EXECUTING | RECOVERING | GOAL_MET | GOAL_FAILED |
                       BUDGET_EXHAUSTED | CANCELLED | ESCALATED_TO_HUMAN
```

**Why first-class.** Today the goal is `prompt: str` appended at `core/stateless.py:266` and never
seen again. Nothing can ask "was the goal met?" because the goal is not an object. Every downstream
gap — verification, termination, recovery, stagnation — traces back to this.

**Design constraint.** `acceptance` may be empty. When it is, a node can only reach `INCONCLUSIVE`,
never `SUCCEEDED`. This makes the honest case explicit rather than letting "no criteria" mean "passed".

### 2.2 AcceptanceCriteria

```
AcceptanceCriteria
  criteria_id   : identity
  kind          : DETERMINISTIC | SEMANTIC | ARTIFACT
  check         : what must be observed (command + expected exit, hash match, schema, assertion)
  required      : bool   (required criteria must pass for SUCCEEDED)
  evidence_kind : what evidence satisfies it
  provenance    : who authored it
```

**Why first-class.** The nearest analogue today is `BenchmarkTask.verify`, a Python callable that
exists only in the benchmark harness (`benchmark/tasks.py:29`). `VerificationFloorGuard` has no
criteria at all — it checks only that a shell command exited 0 after the last edit
(`core/verification.py:97-116`). A verifier without criteria cannot be independent, because there is
nothing to verify *against*.

**Kind ordering matters:** `DETERMINISTIC` criteria (exit codes, hashes, schema, test results) are
evaluated before `SEMANTIC` ones, because a deterministic failure is not a matter of opinion.

### 2.3 Task (node)

**Retained from `graph/types.py:115-132`**, with additions:

```
Task (= GraphNode)
  task_id        : the existing `id` (identity, immutable)
  type           : AGENT | FUNCTION | JOIN | ROUTER | VERIFIER | GATE | APPROVAL   (unchanged)
  contract       : inputs/outputs                                                 (unchanged)
  state          : the target NodeStatus vocabulary (see §4)
  goal_id        : which goal this serves                                          (NEW)
  criteria_refs  : which AcceptanceCriteria this task is accountable for           (NEW)
  evidence_refs  : evidence this task produced                                     (NEW)
  invalidation   : what invalidates this task's evidence                           (NEW)
  superseded_by  : replacement task_id after a replan, or null                     (NEW)
  attempt        : retry counter                                                   (exists)
  lease          : owner + expiry                                                  (NEW)
  provenance     : producer, parent transition, timestamp                           (NEW)
```

**Identity.** `task_id` remains immutable, as today. A task is never mutated into a different task —
a replan produces a **new** task and marks the old one `SUPERSEDED` with a pointer. This preserves the
existing frozen-dataclass design (`graph/types.py:115`) while adding dynamism, and it keeps history
intact, which replay requires.

### 2.4 Action

**The existing `ToolRequest` (`contracts/tool.py:15-80`) is already the right shape** — statuses and
block-reasons vocabularies included. It is simply unreached.

```
Action (= ToolRequest, extended)
  action_id     : identity
  task_id       : which task requested it
  tool          : the tool name
  args          : validated arguments
  intent        : the proposer's stated reason (from the model, advisory)
  validation    : the verdict (from authorize() + SecurityPolicy + budget)
  provenance    : proposer, model, prompt hash
  idempotency   : a key derived from (task_id, tool, canonical(args))   (NEW — closes P1-1)
```

**Why first-class.** Without it, model output reaches `ToolExecutor.execute` directly
(`core/stateless.py:812`) and there is no object to validate, journal, or replay.

**Idempotency key** is the smallest addition that closes the duplicate-execution risk documented in
the audit (§20/§26).

### 2.5 Observation

```
Observation
  observation_id : identity
  task_id        : producing task
  action_id      : producing action
  kind           : TOOL_RESULT | FILE_STATE | EXIT_STATUS | STDOUT | ARTIFACT_WRITTEN | DIFF
  content_ref    : pointer to the durable payload (not the payload inline)
  summary        : bounded, model-facing text
  observed_at    : timestamp
  outcome        : the EXISTING OutcomeClass (SUCCESS|ERROR|DENIAL|POLICY_DENIAL|TIMEOUT|
                   CANCELLATION|INVALID|UNKNOWN)          <-- reuse, do not reinvent
  provenance     : producer, node_run, sequence
```

**Why first-class.** Today a tool result string is simultaneously the observation, the evidence, and
the verification input (`core/stateless.py:1914`). Splitting them is the single change that makes
Principle 3 ("observations are not automatically proof of success") structurally true rather than
aspirational.

**Reuse mandate.** `outcome` **must** be `OutcomeClass` (`core/events.py:280-290`). That taxonomy is
the one concept in this codebase that is already a canonical authority, AST-enforced by
`tests/test_outcome_classification_authority.py`. A second classifier would repeat the exact defect
Phase 10 spent its budget fixing.

### 2.6 Evidence

```
Evidence
  evidence_id    : identity
  claim_ref      : what it supports (task / criterion)
  source         : which observation(s) it derives from
  kind           : EXIT_CODE | TEST_RESULT | HASH | SCHEMA | DIFF | ARTIFACT | LINT | TYPE_CHECK
  strength       : DETERMINISTIC | SEMANTIC
  content_ref    : durable pointer (artifact or journal record)
  observed_at    : timestamp
  producer       : which component produced it
  invalidated_by : the mutation that made it stale, or null
  provenance     : full chain
```

**Why first-class.** `GraphArtifact` already proves the pattern works — content-addressed,
hash-verified, run-scoped, with `producer` and `node_run_id` (`graph/types.py:305-316`,
`graph/artifacts.py:56-92`). The gap is that this exists **only** in the graph layer and never in the
turn layer. Evidence generalizes what the artifact layer already does correctly.

**Invalidation is the key property.** `core/verification.py:149` already nulls prior evidence on a
later mutation, pinned by `test_verification_contract.py:72-78`. Promoting that to an explicit
`invalidated_by` field makes it inspectable and replayable.

### 2.7 Verification

```
VerificationRequest
  request_id  : identity
  task_id     : what is being verified
  criteria    : AcceptanceCriteria[] (the required subset)
  evidence    : evidence refs available to the verifier
  independence: REQUIRED | PREFERRED | NOT_REQUIRED
  deadline    : budget

VerificationResult
  result_id   : identity
  request_id  : what it answers
  verdict     : PASS | FAIL | INCONCLUSIVE
  criteria_results : per-criterion outcome
  evidence    : evidence refs the verdict rests on
  verifier    : which component/model, and whether it differs from the actor
  reason      : human-readable
  provenance  : chain
```

**Why first-class.** `VerificationResult` already exists (`graph/types.py:285-294`) — but with
`decision` restricted to `("ALLOW","REJECT","RETRY","ESCALATE")` (`graph/verifier.py:19`), which is a
*router's* vocabulary, not a verifier's. It cannot express "I could not tell", which is why the
current design has no honest third outcome. Adding `INCONCLUSIVE` is a small but load-bearing change.

### 2.8 Failure

```
Failure
  failure_id   : identity
  task_id      : where it happened
  class        : TRANSIENT | TOOL | IMPLEMENTATION | VERIFICATION | DEPENDENCY | ENVIRONMENT |
                 INVALID_ASSUMPTION | REPEATED | STAGNATION | SECURITY
  code         : a closed, enumerated code within the class
  evidence     : evidence refs
  detected_by  : which component
  detected_at  : timestamp
  recoverable  : bool + which rungs are legal
  provenance   : chain
```

**Why first-class.** Today `NodeFailure.failure_code` is a **free string**
(`graph/types.py:273-283`), and the only real classifier is `_is_transient`
(`core/transport.py:443-570`) which covers exactly one of the ten classes. Recovery cannot be
*chosen* if failures cannot be *distinguished*.

**Closed vocabulary is mandatory.** Six of the ten classes exist nowhere in the codebase today
(implementation, verification, dependency, environment, invalid assumption, stagnation). The other
four map to existing artifacts: `TRANSIENT` → `is_transient_error`; `TOOL` → `OutcomeClass.ERROR`;
`SECURITY` → `OutcomeClass.POLICY_DENIAL`; `REPEATED` → `VerificationFloorGuard.repeat_count`
(`core/verification.py:135-136`).

### 2.9 Decision

```
Decision
  decision_id : identity
  subject     : what is being decided (a task, a failure, a router choice)
  options     : the legal alternatives
  chosen      : the selected option
  reason      : why
  decided_by  : component + model (if any)
  budget_cost : what it consumed
  provenance  : chain
```

**Why first-class.** Routing already produces decisions that are invisible: `_label_for`
(`graph/executor.py:908-926`) selects among pre-declared edges from model output, and
`graph/verifier.py:19` produces `ALLOW|REJECT|RETRY|ESCALATE`. Making the decision a record is what
allows the "why did it do that?" question to be answered from durable state rather than re-inference.

### 2.10 Artifact

**Retained unchanged in concept.** `GraphArtifact` (`graph/types.py:305-316`) with
`artifact_id, run_id, node_run_id, type, content_hash, uri, producer, created_at`, persisted
content-addressed under `.wisp/artifacts/` and verified on read (`graph/artifacts.py:94-110`).

**Change:** artifacts become reachable from the turn layer, not only the graph layer. No schema change.

### 2.11 Delegation

**Retained:** `SubagentContract` / `SubagentResult` (`multi_agent/task.py:57,213`).

**Changes:**
- `task: str` → a structured task (goal ref + explicit inputs + required outputs).
- `output_schema` becomes **mandatory** for delegated work (today it is opt-in).
- `capabilities` becomes an explicit field, **and is actually applied** (today `derive_subagent` at
  `auth/principal.py:62` is never called in production).
- `isolation` becomes explicit (`WORKTREE | SHARED`) rather than defaulted to shared
  (`task.py:122`).
- Failure becomes a typed `Failure`, not a prose marker (`subagent_orchestrator.py:933,1577`).

### 2.12 Human Intervention

```
HumanIntervention
  request_id   : identity
  task_id      : what is blocked
  kind         : APPROVAL | CLARIFICATION | ESCALATION | DECISION
  question     : what is being asked
  options      : the legal choices (if enumerable)
  requested_at : timestamp
  deadline     : budget
  response     : the answer, or null
  responded_by : identity
  state        : PENDING | ANSWERED | EXPIRED | CANCELLED
```

**Why first-class.** Approval is currently a **blocking call** inside the tool pipeline
(`core/approval_gate.py:54`; `tool_executor.py:806`). A durable request/response record makes
intervention *resumable* — which is what allows a process to restart while waiting for a human, and
what closes the "REST has no approval channel" gap (`PHASE_10_AUTHORIZATION_PARITY.md`).

---

## 3. Concept Relationships

```
Goal 1──* AcceptanceCriteria
Goal 1──* Task                      (a goal decomposes into tasks)
Task 1──* Action                    (a task requests actions)
Action 1──* Observation             (an action produces observations)
Observation *──* Evidence           (evidence derives from observations)
AcceptanceCriteria 1──* VerificationRequest
VerificationRequest 1──1 VerificationResult
VerificationResult 1──* Evidence
Task 1──* Failure
Failure 1──1 Decision               (recovery chooses)
Decision 1──1 RecoveryProposal
Task 1──* Artifact
Task 1──* Delegation (Task in another scope)
Task 1──? HumanIntervention
```

**Invariants:**
- R1 — Every `Observation` belongs to exactly one `Action`, which belongs to exactly one `Task`.
- R2 — Every `Evidence` cites at least one `Observation`. Evidence with no source is invalid.
- R3 — Every `VerificationResult` cites the `AcceptanceCriteria` it evaluated.
- R4 — Every `Task` in state `SUCCEEDED` has at least one non-invalidated `Evidence` satisfying every
  `required` criterion. **This is the structural definition of "not a false success".**
- R5 — Every state change has a `Decision` (even if the decision is "the deterministic rule fired").
- R6 — `SUPERSEDED` and `INVALIDATED` tasks are never deleted. History is append-only.

---

## 4. The State Vocabulary (single, canonical)

```
NodeState:  PENDING | READY | CLAIMED | RUNNING | OBSERVED | VERIFYING | SUCCEEDED | FAILED |
            INCONCLUSIVE | WAITING | BLOCKED | CANCELLED | INVALIDATED | SUPERSEDED     (14)

GoalState:  GOAL_OPEN | PLANNING | EXECUTING | RECOVERING | GOAL_MET | GOAL_FAILED |
            BUDGET_EXHAUSTED | CANCELLED | ESCALATED_TO_HUMAN                          (9)
```

**Canonicalization requirement.** The audit found **four** state vocabularies in the repository:
`graph/types.NodeStatus` (7), `graph/types.RunStatus` (7), `runs/record.RunState` (8),
`graph/planner` proposal statuses (9), plus `core/graph/phases.Phase` (9).
`tests/test_canonical_execution_state.py` already exists to police one of them.

**Before any of this is implemented, `RunStatus` and `RunState` must be reconciled.** The audit notes
they are currently divergent but harmlessly so, *because the durable layer is unwired*. Wiring them
without canonicalizing would create a live divergence — which is exactly the class of defect Phase 10
spent its budget on.

---

## 5. Mapping: Current → Target

| Target concept | Today | Smallest correct change |
|---|---|---|
| Goal | `prompt: str` (`stateless.py:266`) | introduce the type; attach criteria; persist per run |
| AcceptanceCriteria | `benchmark/tasks.py:29` only | generalize; make required-criteria authoritative |
| Task | `GraphNode` (frozen) | add `goal_id`, `criteria_refs`, `evidence_refs`, `lease`, `superseded_by` |
| Action | `ToolRequest` (unwired) | wire it; add `idempotency` |
| Observation | raw string (`stateless.py:1914`) | introduce the type; reuse `OutcomeClass` |
| Evidence | `GraphArtifact` (graph only) | generalize to the turn layer; add `invalidated_by` |
| Verification | `VerificationResult` + floor guard | add `INCONCLUSIVE`; add criteria refs; split request/result |
| Failure | free-string `failure_code` | close the vocabulary (10 classes) |
| Decision | implicit | record it |
| Artifact | `GraphArtifact` | make reachable from the turn layer |
| Delegation | `SubagentContract` (free-text task) | structured task; mandatory result schema; wire narrowing |
| Human Intervention | blocking call | durable request/response; resumable |
| Hypothesis | — | **do not add** until an experiment loop exists |
| Plan | proposal statuses | **do not add** a separate type |
| Budget | `ResourceBudget` | keep as a field; unify the reporting |

---

## 6. What This Model Deliberately Excludes

| Excluded | Why |
|---|---|
| Hypothesis nodes | No component consumes them. Adding them would create a type nobody reads — the exact pattern the audit found eight times. |
| A separate Plan type | A plan *is* a graph proposal. Two representations would drift. |
| A `Belief` type | No consumer; would be speculation. |
| A second outcome classifier | `OutcomeClass` is the canonical authority. A second one is a known defect class. |
| A second graph | `multi_agent/dag.py` should be retired into `wisp/graph/`, not duplicated alongside it. |
| A distributed scheduler | The audit found no requirement for one; `SchedulerState` is explicitly in-process by design. |
