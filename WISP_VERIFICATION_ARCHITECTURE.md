# WISP — VERIFICATION ARCHITECTURE

**Phase 0 design document. Conceptual only — nothing here is implemented.**

> **Principle 3:** *Execution produces observations. Observations are not automatically proof of success.*
> **Principle 4:** *Verification determines whether acceptance criteria are actually satisfied.*

---

## 0. The Current Position, Stated Plainly

| Property | Today |
|---|---|
| Who decides success | the same model invocation that planned and acted (`core/stateless.py:744,771`) |
| What it checks | that a shell command exited 0 after the last edit, or that a test summary parses green (`core/verification.py:75,83-88`) |
| Against what criteria | **nothing** — there are no acceptance criteria |
| What it permits | surrender, explicitly, with the word `UNVERIFIED` (`core/verification.py:36-41,56-57`) |
| Is the verdict durable | **no** — it is a boolean in memory |
| Is there a third outcome | **no** — pass or fail only |

**Verified:** `tests/test_verification_loop.py:110-123` (`test_edit_then_finish_is_nudged_not_done`)
**pins the same-invocation loop** as intended behavior. This is not an oversight; it is the current
design.

**What already works well:** `core/verification.py:149` invalidates prior verification evidence on
any later mutation, and `tests/test_verification_contract.py:72-78` +
`tests/test_verification_loop.py:139-164` pin it. That is a correct and important property, and it is
the seed of the target design.

---

## 1. Verification Model

```
              ┌──────────────────────────────────────────┐
              │  AcceptanceCriteria  (authored up front) │
              └────────────────────┬─────────────────────┘
                                   │
  Action ──► Observation(s) ──► VerificationRequest ──► Verifier ──► VerificationResult
                                   │                                    │
                                   │                              ┌─────┴─────┐
                                   │                              ▼           ▼
                                   │                            PASS        FAIL / INCONCLUSIVE
                                   │                              │           │
                                   └── Evidence ◄─────────────────┘           │
                                              │                               │
                                              ▼                               ▼
                                     Task → SUCCEEDED                 Recovery (see
                                     (only if all required            WISP_RECOVERY_ARCHITECTURE.md)
                                      criteria are satisfied
                                      by non-invalidated evidence)
```

**The load-bearing sentence:** a task reaches `SUCCEEDED` **only** when non-invalidated evidence
satisfies every *required* criterion. There is no other path to `SUCCEEDED`. This is invariant R4
from `WISP_GRAPH_DOMAIN_MODEL.md`.

---

## 2. Acceptance Criteria

### 2.1 Kinds

| Kind | Evaluated by | Examples | Trust |
|---|---|---|---|
| `DETERMINISTIC` | code, no model | exit code, hash match, schema validation, test result, lint, type check, diff scope | **highest** — not a matter of opinion |
| `ARTIFACT` | code | artifact exists, content hash matches, artifact parses | high |
| `SEMANTIC` | a model | "the change addresses the stated goal", "the API is used correctly" | lowest — advisory unless corroborated |

### 2.2 Shape

```
AcceptanceCriteria
  criteria_id   : identity
  kind          : DETERMINISTIC | ARTIFACT | SEMANTIC
  statement     : what must be true, in the operator's words
  check         : the executable form (command + expected result, hash, schema, predicate)
  required      : bool
  evidence_kind : what evidence satisfies it
  authored_by   : operator | goal interpreter | planner
```

### 2.3 Evaluation order

**Deterministic and artifact criteria run first, and a deterministic failure short-circuits.**
Rationale: a failing test is not open to interpretation, and letting a model adjudicate a
deterministic failure is how false successes are manufactured.

### 2.4 When criteria are absent

If a goal carries no acceptance criteria, the honest verdict is `INCONCLUSIVE` — **never**
`SUCCEEDED`. This is the single most important semantic change in this document: it makes "we do not
know whether this worked" a representable outcome, which today it is not.

---

## 3. Verification Requests and Results

### 3.1 Request

```
VerificationRequest
  request_id   : identity
  task_ref     : the task under verification
  criteria     : the required subset of criteria
  evidence     : evidence available to the verifier
  independence : REQUIRED | PREFERRED | NOT_REQUIRED
  deadline     : budget
  provenance   : who requested it and why
```

### 3.2 Result

```
VerificationResult
  result_id        : identity
  request_id       : what it answers
  verdict          : PASS | FAIL | INCONCLUSIVE
  criteria_results : per-criterion outcome + the evidence that decided it
  evidence         : evidence refs the verdict rests on
  verifier         : component + model id + whether it differs from the actor
  independence_met : bool
  reason           : human-readable
  provenance       : chain
```

### 3.3 The three verdicts and what each means

| Verdict | Meaning | Consequence |
|---|---|---|
| `PASS` | every *required* criterion is satisfied by non-invalidated evidence | task → `SUCCEEDED` |
| `FAIL` | at least one required criterion is provably unsatisfied | task → `FAILED`; recovery chooses a rung |
| `INCONCLUSIVE` | the criteria could not be evaluated (no criteria, verifier unavailable, evidence insufficient) | task → `INCONCLUSIVE`; recovery may request more evidence, replan, or escalate |

**`INCONCLUSIVE` is the addition that makes false success structurally impossible to hide.** Today
the only alternatives are "done" and "nudge", so an unverifiable task becomes "done".

### 3.4 Relationship to the existing vocabulary

`graph/verifier.py:19` defines `VALID_DECISIONS = ("ALLOW","REJECT","RETRY","ESCALATE")`. That is a
**router's** vocabulary — it says what to do next, not what is true. The target keeps it as the
*routing* decision derived from a verdict, and adds the verdict as a separate concept:

```
VerificationResult.verdict  ──►  routing decision  ──►  next edge
      PASS                  ──►  ALLOW             ──►  dependents READY
      FAIL                  ──►  RETRY | REPAIR | ESCALATE
      INCONCLUSIVE          ──►  (more evidence) | RETRY | ESCALATE
```

This is a small change with a large effect: the current design conflates "what is true" with "what to
do", which is why it cannot express uncertainty.

---

## 4. Evidence

### 4.1 What already exists and must be retained

`GraphArtifact` (`graph/types.py:305-316`) is the correct model, already implemented:
`artifact_id, run_id, node_run_id, type, content_hash, uri, producer, created_at`, persisted
content-addressed under `.wisp/artifacts/<type>-<hash>.json` (`graph/artifacts.py:56-92`) and read
through a **hash-verifying** accessor (`:94-110`).

**Provenance is real** — producer + node_run + run + timestamp — and the graph writes
`graph.artifact_created` events (`:89-91`). This satisfies Principle 5 as written.

### 4.2 What is missing

Evidence does not exist **outside** the graph layer. For a successful *turn*, the only "evidence" is
the tool-result text in session history (`core/stateless.py:823-837`) — no artifact, no hash, no
producer record. `ChangeTracker` records file mutations with timestamp/agent_id/sizes
(`change_tracker.py:19-37,49-106`) but is **not wired to verification**.

### 4.3 The target Evidence type

```
Evidence
  evidence_id    : identity
  claim_ref      : the criterion or task it supports
  derived_from   : observation refs  (>= 1, enforced)
  kind           : EXIT_CODE | TEST_RESULT | HASH | SCHEMA | DIFF | ARTIFACT | LINT | TYPE_CHECK
  strength       : DETERMINISTIC | SEMANTIC
  content_ref    : durable pointer (artifact or journal record)
  observed_at    : timestamp
  producer       : which component produced it
  invalidated_by : the mutation that made it stale, or null
  provenance     : full chain
```

### 4.4 Staleness and invalidation

**Already correct in spirit, and to be promoted to explicit state:**

`core/verification.py:149` nulls prior verification evidence whenever a mutation occurs after the
verification. Pinned by `test_verification_contract.py:72-78` and `test_verification_loop.py:139-164`.

The target makes this inspectable rather than implicit: an evidence record carries `invalidated_by`
pointing at the mutation that killed it. An invalidated evidence record is **never deleted** — it is
retained with its invalidation reason, which is what makes replay possible.

**Cascading rule:** invalidating evidence on a task invalidates the `SUCCEEDED` state of any task
whose required criteria depended on it, transitively. This is `GraphInvalidate` with `scope=CASCADE`
(`WISP_PROPOSAL_PROTOCOL.md` §3.4).

### 4.5 Provenance chain

```
Goal → Task → Action → Observation → Evidence → VerificationResult → Task.SUCCEEDED
```

Every arrow is a recorded reference. The chain is what allows the question *"why does the system
believe this succeeded?"* to be answered from durable state, with no model involvement.

---

## 5. Independence

### 5.1 The problem

A single model invocation currently plans, acts, and judges (`core/stateless.py:744,771`). The graph
layer has a separate `VERIFIER` node type (`graph/types.py:22`) and the optimizer mints one
(`graph/optimizer_passes.py:221-270`), and `graph/verifier.py:1-10` states the requirement — *"must be
… independent from generation (different context, ideally different model)"*.

**But** the verifier's judgment is produced by the same injected `runner` used for agent nodes
(`graph/executor.py:577,585-591`), and `_try_insert` may reuse the graph's model/provider
(`optimizer_passes.py:353-354`). Nothing enforces a different model at runtime.

### 5.2 Three levels of independence

| Level | Requirement | Achievable with one model? |
|---|---|---|
| **L1 — Structural** | verifier is a different node with a different context and a prompt the actor never saw | **YES** |
| **L2 — Informational** | the verifier receives criteria and evidence the actor did not have while acting | **YES** |
| **L3 — Model** | a different model performs verification | requires a second model |

**The target mandates L1 + L2 and prefers L3.** This is a deliberate design choice: independence that
requires a second model would make the architecture unimplementable in the single-model deployment
Wisp actually targets, so the design must be independent *structurally* first.

### 5.3 How L1/L2 are enforced

1. **Criteria authored before acting** — the actor never sees the verification prompt.
2. **Separate context** — the verifier receives `criteria + evidence + task`, not the actor's
   transcript.
3. **Evidence-only judgment** — the verifier may not accept the actor's claim as evidence.
4. **Deterministic-first** — the checks that need no judgment are evaluated by code.
5. **Recorded** — `VerificationResult.verifier.independence_met` is a durable field, so a
   non-independent verification is *visible* rather than assumed.

### 5.4 What the existing floor guard becomes

`VerificationFloorGuard` (`core/verification.py:119-206`) is **retained** and **demoted**: it becomes
one deterministic check among several — specifically, a *cheap* check that a verification action
occurred at all after the last mutation. Its honest-surrender behavior (`UNVERIFIED`) maps exactly
onto the new `INCONCLUSIVE` verdict, which is a natural fit rather than a rewrite.

**Its invalidate-on-mutation property is retained unchanged** — it is already correct.

---

## 6. Verification Policy

Which checks are required depends on what changed. This is a policy table, not a hard-coded sequence:

| Change | Required deterministic checks | Suggested semantic check |
|---|---|---|
| Source edit | compile / type check / lint; existing test suite for the touched module | does the change address the goal? |
| Test-only edit | the test suite runs and the new test fails before / passes after | — |
| Config / dependency change | the system boots; the affected subsystem's tests pass | — |
| Documentation | none (deterministic) | accuracy against the code |
| Security-sensitive path | policy tests; protected-path tests | — |

**Policy is data.** `wisp/policy/` already provides a signed, narrow-only bundle format
(`wisp/policy/`, Ed25519, `narrow_only` merge). Verification policy is a natural second consumer of
that machinery — and the audit notes that layer is **never loaded at runtime**
(`PHASE_10_M4_GOVERNANCE_UNWIRED.md`; verified: `composition.py:134` passes no `policy=`).
Wiring it is a prerequisite for policy-driven verification, and is listed as P1-2 in the audit.

---

## 7. False-Success Prevention

The audit's Q7 asks what is missing for independent verification. This section states the specific
mechanisms that make false success structurally hard rather than merely discouraged.

| Mechanism | What it prevents |
|---|---|
| `SUCCEEDED` requires non-invalidated evidence for every required criterion (R4) | "it finished, therefore it worked" |
| `INCONCLUSIVE` exists as a verdict | unverifiable work being reported as success |
| Evidence must cite observations (R2) | claims with no basis |
| Evidence carries provenance | fabricated or orphaned evidence |
| Invalidation is explicit and cascading | stale evidence surviving a later mutation |
| Deterministic checks run first and short-circuit | a model overriding a failing test |
| The verifier is structurally separate (L1/L2) | self-assessment |
| `independence_met` is recorded | silent loss of independence |
| Verification results are durable | a verdict that cannot be audited |
| A rejection of a proposal is recorded | silent refusals |

**The strongest of these is the first.** Today, the *only* gate to completion is
`guard.resolved()` = `wrote_code and verify_ok_after_edit is True`
(`core/verification.py:193-196`) — a boolean with no criteria behind it. Replacing that boolean with
a criteria-satisfaction requirement is the single highest-leverage change in the verification
architecture.

---

## 8. Relationship to Existing Components

| Component | Disposition |
|---|---|
| `core/verification.py` `VerificationFloorGuard` | **RETAIN**, demote to one deterministic check; map `UNVERIFIED` → `INCONCLUSIVE` |
| `graph/verifier.py` deterministic gates (`gate_tests_green`, `gate_no_scope_creep`, `gate_schema_valid`, `gate_policy_allowed`) | **RETAIN** — these are exactly the `DETERMINISTIC` criteria the target needs |
| `graph/verifier.py` `combine_verdicts` (any REJECT wins) | **RETAIN** — correct conservative semantics |
| `graph/verifier.py:19` `VALID_DECISIONS` | **RETAIN as routing**, add a separate verdict |
| `graph/types.py:285` `VerificationResult` | **EXTEND** — add `INCONCLUSIVE`, criteria refs, `independence_met` |
| `graph/artifacts.py` `ArtifactStore` | **RETAIN unchanged** — the correct evidence substrate |
| `core/events.py` `OutcomeClass` | **RETAIN as the observation classifier** — never duplicate |
| `core/lsp/gate.py` `CompilerGate` | **RETAIN** — a `DETERMINISTIC` check (note: currently fail-open, `:95-145`) |
| `test_runner.py`, `tools/tests.py` | **RETAIN** — the test-execution substrate |
| `benchmark/tasks.py:29` `verify` callable | **GENERALIZE** into `AcceptanceCriteria` |
| `change_tracker.py` | **WIRE** into evidence (it already records mutations with timestamps) |

---

## 9. Open Questions for the Implementation Phase

1. **Where do criteria come from for a free-form operator prompt?** The goal interpreter must author
   them, and an operator must be able to inspect and amend them before execution. If the interpreter
   cannot produce criteria, the goal is `INCONCLUSIVE` by construction — which is honest but may be
   surprising.
2. **What is the cost of L1/L2 independence in tokens?** A separate verification context doubles the
   model calls per verified task. The floor guard's cheapness is part of why it exists.
3. **Should `INCONCLUSIVE` block dependents?** Recommended: no — dependents may proceed if their own
   criteria are satisfied, but the goal cannot reach `GOAL_MET`.
4. **How does verification interact with the existing approval gate?** An approval is a *human*
   decision, not evidence. They must not be conflated (the audit notes REST currently has no
   approval channel at all — `PHASE_10_AUTHORIZATION_PARITY.md`).
