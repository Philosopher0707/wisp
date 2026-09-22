# WISP — RECOVERY ARCHITECTURE

**Phase 0 design document. Conceptual only — nothing here is implemented.**

> **Principle 6:** *Recovery is part of the architecture, not an afterthought.*
> **Principle 10:** *Long-running execution requires explicit handling of retry, recovery, budgets, stagnation.*

---

## 0. The Current Position

Wisp already has **substantial** recovery machinery. What it lacks is (a) a **failure taxonomy** to
drive the choice, (b) **replanning** at any level, and (c) a **ladder** — the existing mechanisms are
independent, not ordered.

### 0.1 What exists (verified)

| Mechanism | Trigger | Budget | Location |
|---|---|---|---|
| Provider stream retry | transient 429/5xx/socket, empty stream, stall | `max_attempts` + jittered backoff | `core/provider_stream.py:75-102,285-355` |
| Turn-level transient retry | exception in the provider stream | `iteration < 2`, backoff 1 s/2 s | `core/stateless.py:650-702` |
| Graph node retry | FAILED node, idempotent | `retry_policy.max_attempts` (default 1) | `graph/executor.py:611-641` |
| Correction-edge replay | conditional label on a cycle member | `cycle.max_iterations` ≤ 5 | `graph/executor.py:692-727` |
| Verifier RETRY routing | verifier verdict | routed by `_label_for` | `graph/executor.py:908-926` |
| File rollback / rewind | agent calls `tool_rewind` | 20/file, 10 MB/workspace | `tools/checkpoints.py:141-223` |
| Graph resume | process restart | graph-hash pin | `graph/executor.py:149-231` |
| Turn crash-replay | last event ≠ DONE | — | `core/runtime.py:363-373` |
| Subagent timeout retry | timeout | 1 extra round ×1.5 | `subagent_orchestrator.py:852-880` |
| Subagent transient retry | transient, not denial | `max_retries` ≤ 5, backoff ≤ 6 s | `subagent_orchestrator.py:996-1113` |
| Subagent schema repair | invalid schema | shared budget | `subagent_orchestrator.py:1464-1500` |
| Provider circuit breaker | consecutive failures | threshold 5, recovery 30 s | `infra/circuit_breaker.py:24-38` |
| Oscillation revert | 1-cycle / 2-cycle diff hash | `max_iterations=25` | `core/graph/loop.py:112-196` — **UNWIRED** |
| Subagent circuit breaker | 3 failures / 120 s | manual reset | `multi_agent/_circuit_breaker.py:15-38` — **UNWIRED** |

### 0.2 What is absent

- **Local replan** — absent everywhere.
- **Global replan** — absent everywhere.
- **A failure taxonomy** — `NodeFailure.failure_code` is a **free string** (`graph/types.py:273-283`).
- **Recovery budgets with precedence** — each mechanism has its own budget; nothing coordinates them.
- **A durable rollback path** — `CheckpointStore` is in-memory and session-scoped
  (`tools/checkpoints.py:6-10`), so process death loses every checkpoint.
- **Human escalation as a state** — approval is a *blocking call* (`core/approval_gate.py:54`;
  `tool_executor.py:806`), not a durable request.

---

## 1. Failure Taxonomy

Recovery cannot be *chosen* unless failures can be *distinguished*. The target vocabulary is closed —
ten classes, each with a defined detection point and a legal rung set.

| Class | Detected by | Existing detection code | Legal rungs |
|---|---|---|---|
| `TRANSIENT` | provider/network layer | `core/transport.py:443-570` `is_transient_error`/`is_transient_status`; fallback `stateless.py:655-668` | Retry (with backoff) |
| `TOOL` | tool result outcome | `core/events.py:366-372` `classify_result` → `ERROR` | Repair → Retry → Local Replan |
| `IMPLEMENTATION` | verification | `VerificationResult.verdict == FAIL` | Repair → Local Replan → Global Replan |
| `VERIFICATION` | verifier unavailable / inconclusive | `INCONCLUSIVE` verdict | Diagnostic → Local Replan → Escalate |
| `DEPENDENCY` | predecessor failed / blocked | `blocked_by_failure` (`graph/scheduler.py:47-73`) | Global Replan |
| `ENVIRONMENT` | sandbox / toolchain / host | (new — detect from tool output + sandbox router) | Diagnostic → Escalate |
| `INVALID_ASSUMPTION` | plan contradicts observed reality | (new — a criterion that cannot hold) | Local Replan → Global Replan |
| `REPEATED` | same failure N times | `VerificationFloorGuard.repeat_count` (`core/verification.py:135-136`) | Escalate (stop retrying) |
| `STAGNATION` | no progress signal movement | `OscillationTrap` (`core/graph/loop.py:112-125`) — **unwired** | Global Replan → Escalate |
| `SECURITY` | policy/authorization denial | `OutcomeClass.POLICY_DENIAL` (`core/events.py:286`) | **No retry** — Escalate |

**Six of these ten exist nowhere today** (implementation, verification, dependency, environment,
invalid assumption, stagnation). The other four map onto existing artifacts, listed above.

**Critical rule carried over:** denials must never auto-retry. `graph/subagent_orchestrator.py:933`
comments this and `_DENIAL_MARKERS` was removed in Phase 10 because all five canonical denial
statuses matched nothing (`CONTEXT.md:456`). The taxonomy makes the rule enforceable by class rather
than by prose matching.

---

## 2. The Recovery Ladder

```
  Retry ──► Repair ──► Rollback ──► Local Replan ──► Global Replan ──► Diagnostic Task ──► Human Escalation
    │         │            │              │                 │                  │                  │
  cheap    prompt/      restore a     fix/extend      rebuild the        gather info        ask the
  & fast   arg fix      checkpoint    the subgraph    whole graph        to classify        operator
    │         │            │              │                 │                  │                  │
  rung 1    rung 2       rung 3         rung 4            rung 5             rung 6            rung 7
```

**Design rules:**

- **R1 — Escalate, do not loop.** A rung is exhausted by its budget; exhaustion moves *down* the
  ladder, never back up to the same rung.
- **R2 — Each rung is cheaper than the next.** The order is by cost and blast radius.
- **R3 — Terminal honesty.** If rung 7 is exhausted, the outcome is `ESCALATED_TO_HUMAN` — a
  **terminal state**, not a hang and not a false success.
- **R4 — Every rung is a `RecoveryDecision` proposal** (`WISP_PROPOSAL_PROTOCOL.md` §3.9): validated,
  recorded, idempotent.
- **R5 — A rung that would repeat an already-failed rung for the same failure is illegal.** This is
  the structural anti-stagnation rule.

---

## 3. Rung Definitions

### Rung 1 — Retry

| Aspect | Definition |
|---|---|
| **Applies to** | `TRANSIENT`, and `TOOL` failures that are provably idempotent |
| **Detection** | `is_transient_error` / `is_transient_status` (`core/transport.py:443-570`); `OutcomeClass` |
| **Decision maker** | deterministic rule |
| **Budget** | existing: `max_attempts` (stream), `iteration < 2` (turn), `retry_policy.max_attempts` (graph node) |
| **State transition** | `FAILED → PENDING` (graph, `executor.py:636`) |
| **Termination** | attempts exhausted → escalate to rung 2 |
| **Status** | **EXISTS — retain unchanged** |

**Constraint:** retry requires an idempotency key. The audit found **no durable idempotency**
(`idempotency` table empty; the `repeat_guard` is in-memory and per-turn). Retry is therefore
currently *unsafe for side-effecting tools*. Adding the `Action.idempotency` key
(`WISP_PROPOSAL_PROTOCOL.md` §3.5) is a prerequisite for widening rung 1.

### Rung 2 — Repair

| Aspect | Definition |
|---|---|
| **Applies to** | `TOOL`, `IMPLEMENTATION`, `VERIFICATION` |
| **Detection** | a verification `FAIL`, or a tool error with an actionable message |
| **Decision maker** | model, with the failure and the criteria in context |
| **Budget** | bounded; existing analogue is `max_nudges=2` (`core/verification.py:124`) |
| **State transition** | task stays `RUNNING`; a new `Action` is proposed |
| **Termination** | budget exhausted, or the same failure class repeats → rung 3/4 |
| **Status** | **PARTIAL — exists as the nudge loop** (`core/stateless.py:744-768`), not as an explicit rung |

**Change:** promote the nudge to a named rung with a typed failure input and a recorded decision. The
mechanism is unchanged; what is added is that the *repair attempt* becomes an observable object.

### Rung 3 — Rollback

| Aspect | Definition |
|---|---|
| **Applies to** | any failure where a mutation may have made things worse |
| **Detection** | a failure after a mutation, where the pre-mutation state is known |
| **Decision maker** | deterministic rule (rollback is a tool, not a judgment) |
| **Budget** | existing: 20 snapshots/file, 10 MB/workspace |
| **State transition** | affected tasks → `INVALIDATED`; graph → `PENDING` for the invalidated subgraph |
| **Termination** | no checkpoint available → rung 4 |
| **Status** | **PARTIAL — the mechanism exists, the durability does not** |

**The gap is durability, not capability.** `CheckpointStore` is in-memory and session-scoped
(`tools/checkpoints.py:6-10`), dual-bounded (20/file, 10 MB). `tool_rewind` is rewindable — it
snapshots pre-restore state (`:201-206`). **A durable checkpoint store is the single change that makes
rollback a real recovery rung** rather than a within-session convenience.

**Also existing and under-used:** `wisp/runs/compensation.py` defines `EditRecord` (path, unified
diff, pre-image hash, reversible, note, version) and `rollback_preview`, with a `_REVERSIBILITY` tool
map (`:40-54`). Its module docstring states *"No tool wiring"*, and it is referenced only by
`runs/__init__.py:11`. **This is the durable rollback record the ladder needs, already designed.**

### Rung 4 — Local Replan

| Aspect | Definition |
|---|---|
| **Applies to** | `IMPLEMENTATION`, `INVALID_ASSUMPTION`, `TOOL` after repair fails |
| **Detection** | verification `FAIL` that repair did not resolve |
| **Decision maker** | model, constrained to the affected subgraph |
| **Budget** | new: bounded replan count per goal; graph growth is a budget (audit §24) |
| **State transition** | `GraphExpand` on the failing task (`WISP_PROPOSAL_PROTOCOL.md` §3.3); the failed task → `SUPERSEDED` |
| **Termination** | replan budget exhausted → rung 5 |
| **Status** | **ABSENT** |

**Why it needs `GraphExpand` and not just "try again":** local replan means *changing the plan for a
subgoal while keeping the rest of the graph*. That requires the graph to accept new nodes mid-run —
which the audit found is **structurally absent** (the `Graph` is a frozen value,
`graph/types.py:194-201`). **Rung 4 is therefore blocked on the runtime graph mutation capability.**

### Rung 5 — Global Replan

| Aspect | Definition |
|---|---|
| **Applies to** | `DEPENDENCY`, `STAGNATION`, `INVALID_ASSUMPTION` that invalidates the whole plan |
| **Detection** | stagnation signal, or a failed dependency with no local alternative |
| **Decision maker** | model, with the full goal + evidence + failure history |
| **Budget** | new: bounded; each global replan is expensive |
| **State transition** | all tasks → `SUPERSEDED`; a new graph is proposed for the same goal |
| **Termination** | budget exhausted → rung 6 |
| **Status** | **ABSENT** (today, a "replan" is a fresh `propose` → new run, with no relationship to the old one) |

**Design note:** the existing `graph/planner.py` already supports this mechanically — `propose` →
`compile_ir` → `validate_graph` produces a fresh graph. What is missing is the **linkage** (the new
graph supersedes the old, and the old is retained for replay) and the **budget**.

### Rung 6 — Diagnostic Task

| Aspect | Definition |
|---|---|
| **Applies to** | failures that cannot be classified |
| **Detection** | the classifier returns "unknown" or the failure class has no legal rung |
| **Decision maker** | deterministic — spawn a diagnostic task |
| **Budget** | one diagnostic task per failure |
| **State transition** | a `NodeCreate` with type `AGENT` scoped to diagnosis |
| **Termination** | the diagnostic produces a classification → return to the appropriate rung |
| **Status** | **PARTIAL** — `tools/diagnose.py:13-21` and `error_diagnosis.py:219-290` exist, model-invoked |

**Change:** make diagnosis a *recovery-initiated* step rather than a model whim. Today the model must
decide to diagnose; in the target, an unclassifiable failure *triggers* diagnosis.

### Rung 7 — Human Escalation

| Aspect | Definition |
|---|---|
| **Applies to** | `REPEATED`, `STAGNATION`, `SECURITY`, exhausted budgets, unclassifiable failure after diagnosis |
| **Detection** | ladder exhaustion |
| **Decision maker** | deterministic |
| **Budget** | deadline (existing approval timeout is 60 s, `transport/websocket.py:30`) |
| **State transition** | task → `WAITING`; goal → `ESCALATED_TO_HUMAN` (terminal) |
| **Termination** | the operator answers, or the deadline expires → terminal |
| **Status** | **PARTIAL — exists as a blocking approval call, not as a durable state** |

**The gap:** approval today is synchronous (`tool_executor.py:806` awaits the handler). A process
cannot restart while waiting for a human, and the REST surface has **no approval channel at all**
(`PHASE_10_AUTHORIZATION_PARITY.md`: the agent requires approval for `hooks.create` /
`mcp.add_server` / `plugins.install` in the default `auto_edit` mode; REST does not).

**The fix is the `HumanIntervention` type** (`WISP_GRAPH_DOMAIN_MODEL.md` §2.12) — a durable
request/response, which makes escalation resumable and gives REST a channel through the existing
`WebSocketTransport` approval flow (`transport/websocket.py`).

---

## 4. Transition Rules (failure class → legal rungs)

| Failure class | R1 Retry | R2 Repair | R3 Rollback | R4 Local Replan | R5 Global Replan | R6 Diagnostic | R7 Escalate |
|---|---|---|---|---|---|---|---|
| `TRANSIENT` | ✅ | — | — | — | — | — | after budget |
| `TOOL` | ✅ (if idempotent) | ✅ | ✅ | ✅ | — | — | after budget |
| `IMPLEMENTATION` | — | ✅ | ✅ | ✅ | ✅ | — | after budget |
| `VERIFICATION` | — | ✅ | — | ✅ | — | ✅ | ✅ |
| `DEPENDENCY` | — | — | — | — | ✅ | — | ✅ |
| `ENVIRONMENT` | — | — | — | — | — | ✅ | ✅ |
| `INVALID_ASSUMPTION` | — | — | — | ✅ | ✅ | — | after budget |
| `REPEATED` | ❌ | ❌ | — | — | — | ✅ | ✅ |
| `STAGNATION` | ❌ | ❌ | — | — | ✅ | ✅ | ✅ |
| `SECURITY` | ❌ | ❌ | — | — | — | — | ✅ |

**`❌` means structurally forbidden, not merely discouraged.** `REPEATED` forbidding retry is the rule
that prevents infinite retry loops; `SECURITY` forbidding retry is the rule that prevents an agent
from hammering a denial.

---

## 5. Budgets

| Budget | Scope | Existing value | Status |
|---|---|---|---|
| Stream attempts | per provider round | 3 (`stateless.py:2075`) | exists |
| Turn transient retry | per turn | `< 2` (`stateless.py:669`) | exists |
| Graph node retry | per node | `retry_policy.max_attempts` (default 1) | exists |
| Repair nudges | per turn | `max_nudges=2` (`verification.py:124`) | exists |
| Verification grind floor | per turn | `min_turns=5` (`verification.py:123`) | exists |
| Cycle iterations | per cycle | ≤ 5 (`graph/types.py:140-145`) | exists |
| **Local replans** | **per goal** | **—** | **NEW** |
| **Global replans** | **per goal** | **—** | **NEW** |
| **Diagnostic tasks** | **per failure** | **—** | **NEW** |
| **Graph growth (nodes)** | **per goal** | **—** | **NEW** |
| Wall clock | per goal | `turn_timeout` 1800 s (max 7200) | exists (per turn, not per goal) |
| Tokens | per goal | subagent ceiling 2 000 000 | exists (partial) |

**Design requirement:** all of these must be readable from **one** place — the `BudgetGovernor`
(`WISP_TARGET_ARCHITECTURE.md` §2). Today the audit found five unordered termination modes and no
single object that answers "how much is left".

**Precedence rule:** recovery budget exhaustion escalates; it does **not** terminate the goal
silently. Only `BUDGET_EXHAUSTED` at the *goal* level terminates.

---

## 6. Evidence in Recovery

Every recovery decision must cite evidence. This is what separates recovery from thrashing.

| Rung | Evidence required |
|---|---|
| Retry | the transient classification and its source (status code, socket error) |
| Repair | the failure, the criteria that were not met, and the evidence that shows it |
| Rollback | the mutation being undone and its pre-image hash |
| Local Replan | the failed task, its evidence, and the observation that invalidated the assumption |
| Global Replan | the full failure history and the stagnation signal, if any |
| Diagnostic | the unclassifiable failure and what was attempted |
| Escalate | the complete ladder history — which rungs were tried, with what evidence |

**The escalation payload is the audit trail.** When the system asks a human for help, it should be
able to say exactly what it tried and why, from durable records. Today, a `WAITING` state has no such
record because the approval is a transient call.

---

## 7. Stagnation Interaction

Stagnation is the failure mode where **the system is working but not progressing**. It is the most
dangerous failure class because every individual step looks successful.

### 7.1 The detector already exists and is orphaned

`OscillationTrap` (`core/graph/loop.py:112-125`) detects exact 1-cycle repeats and 2-cycle
oscillations of **diff hashes** — a genuine progress signal. `ExecutionGraph.run` reverts files and
enters `RECOVER` (`:180-196`). It is exported (`core/graph/__init__.py:15`).

**Verified unwired:** the only importer is `tests/test_architectural_upgrade.py:81-90`.
`config.graph_oscillation_guard` is defined (`config.py:256,544,799-800`) and **never read**.

### 7.2 Required progress signal

A progress signal needs four inputs. **All four already exist in the codebase, unconnected:**

| Input | Existing artifact |
|---|---|
| per-turn action digest trail | `VerificationFloorGuard.steps` (`core/verification.py:131`) |
| verification result **history** | currently **overwritten** (`core/verification.py:151`) — must become a list |
| artifact/content hashes per turn | `GraphArtifact.content_hash` (`graph/types.py:305-316`) — not fed to the turn loop |
| a monotonic progress metric | **absent** — must be defined |

### 7.3 Proposed progress metric

A task is **progressing** if any of the following changed since the last evaluation:

1. a required criterion moved from unsatisfied to satisfied;
2. a new artifact with a previously-unseen content hash was produced;
3. the set of failing criteria strictly shrank;
4. the graph's completed-node count strictly increased.

If **none** holds across N consecutive evaluations, the failure class is `STAGNATION`.

**Why this metric:** it is deterministic, it requires no model judgment, and every input is already
recorded. It also composes with the existing `OscillationTrap`, which catches the specific case of
*A → B → A* cycles that a coarser metric might miss.

### 7.4 Interaction with the ladder

```
STAGNATION detected
   └─► R5 Global Replan (the plan itself is the problem, not the execution)
          └─► if stagnation recurs after a replan ──► R6 Diagnostic ──► R7 Escalate
```

**A stagnated goal must never reach `GOAL_MET`.** That is the whole point: the detector exists to
prevent a system from reporting success on the basis of activity.

---

## 8. Durable Rollback — the Missing Prerequisite

Rollback is the one rung whose *mechanism* is complete and whose *durability* is absent.

| Component | State | Change needed |
|---|---|---|
| `tools/checkpoints.py` `CheckpointStore` | in-memory, session-scoped, dual-bounded, rewindable | persist snapshots (or their diffs) durably |
| `runs/compensation.py` `EditRecord` | designed, unwired, docstring says "No tool wiring" | **wire it** — it is the durable rollback record |
| `runs/compensation.py` `rollback_preview` | exists | surface it in the escalation payload |
| `runs/compensation.py` `reversibility()` | `_REVERSIBILITY` tool map (`:40-54`) | use it to decide whether rollback is legal |
| `change_tracker.py` | records mutations with timestamps | feed it into `EditRecord` |

**This is a wiring task, not a design task.** The record type, the reversibility map, and the preview
renderer all exist. The audit's `CONTEXT.md:222-241` notes this pattern explicitly: the prior audit's
12 unwired controls were re-verified as *7 wired · 1 deleted · 3 unwired · 1 unidentified* — and this
is a further instance.

---

## 9. What Recovery Must Not Do

| Anti-pattern | Why |
|---|---|
| Retry a denial | `SECURITY` failures are terminal for the rung-1 path; Phase 10 already removed a broken `_DENIAL_MARKERS` that failed to enforce this (`CONTEXT.md:456`) |
| Retry without idempotency | the audit found no durable idempotency; retrying side-effecting tools duplicates effects |
| Loop a rung | rule R1: escalation, not repetition |
| Replan without invalidating | a replan that leaves old `SUCCEEDED` nodes is a false-success generator |
| Escalate without evidence | the escalation payload must be the audit trail |
| Treat an approval as evidence | human approval is a decision, not verification |
| Recover silently | every recovery step is a recorded `RecoveryDecision` |
| Add a second failure classifier | the taxonomy is closed and single; `OutcomeClass` remains the observation authority |

---

## 10. Implementation Ordering for Recovery

Recovery depends on the rest of the architecture:

```
durable turn state (P0-1)
   └─► failure taxonomy (P2-3)                     ── needed to CHOOSE a rung
          └─► durable rollback (P1-4)              ── rung 3 becomes real
                 └─► runtime graph mutation (P0-2) ── rungs 4 and 5 become possible
                        └─► stagnation detector (P2) ── feeds rung 5
                               └─► ladder + budgets  ── the full recovery architecture
```

**Rungs 1, 2 and 7 can be improved immediately** (they are wiring, not new capability): widen retry
once idempotency exists, name the repair rung, and make escalation durable. **Rungs 4 and 5 are
blocked on runtime graph mutation** and must not be attempted before it.
