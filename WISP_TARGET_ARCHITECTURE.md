# WISP — TARGET ARCHITECTURE

**Phase 0 design document. Conceptual only — nothing here is implemented.**

Companion to `WISP_PERSISTENT_GRAPH_LOOP_ALIGNMENT_AUDIT.md`. This document defines the architecture
Wisp should evolve *toward*, using the existing repository as the substrate. It is explicitly **not** a
rewrite proposal: every component below either (a) already exists and is retained, or (b) is the
smallest correct addition that closes an audited gap.

---

## 0. Design Commitments

| # | Commitment | Consequence |
|---|---|---|
| C1 | **Evolve, do not replace.** | `wisp/graph/`, `ToolExecutor`, `authorize()`, `RepoMap` are foundations. |
| C2 | **One authority per concept.** | No new state vocabulary where one exists; duplicates are canonicalized before wiring. |
| C3 | **Every state transition is durable and validated.** | A transition that cannot be replayed is a bug. |
| C4 | **Reasoning proposes; validation disposes.** | Model output never reaches an effect or a state write directly. |
| C5 | **Execution produces observations; verification produces evidence.** | These are separate stages with separate records. |
| C6 | **The graph is the state; the transcript is a view.** | The message list becomes derived, not authoritative. |
| C7 | **Additive and reversible.** | Every phase is behind a flag; disabling it restores today's behavior byte-for-byte. |

---

## 1. Layer Model

```
┌────────────────────────────────────────────────────────────────────────────┐
│ L5  HUMAN INTERVENTION                                                     │
│     ApprovalGate · WebSocket approval · APPROVAL nodes · escalation        │
├────────────────────────────────────────────────────────────────────────────┤
│ L4  GOVERNANCE            (existing, to be completed)                      │
│     authorize() L0-L5 · SecurityPolicy · pathsec · PolicyBundle (wire L0)  │
├────────────────────────────────────────────────────────────────────────────┤
│ L3  CONTROL LOOP          (NEW — thin, explicit)                           │
│     Goal · Planner · Proposal Validator · Graph Controller · Recovery      │
├────────────────────────────────────────────────────────────────────────────┤
│ L2  STATE                 (existing substrate, extended)                   │
│     Graph (wisp/graph) · RunStore · Event Journal · Evidence/Artifact      │
├────────────────────────────────────────────────────────────────────────────┤
│ L1  EFFECT                (existing)                                       │
│     ToolExecutor 19 gates · 42 tools · sandbox · MCP · subagents           │
├────────────────────────────────────────────────────────────────────────────┤
│ L0  SUBSTRATE             (existing)                                       │
│     providers · SQLite store · filesystem · git · LSP · repo intelligence  │
└────────────────────────────────────────────────────────────────────────────┘
```

The control loop (L3) is **new but thin**. Its entire job is to turn durable state into validated
proposals and to apply validated proposals as state transitions. It contains no I/O and no model calls.

---

## 2. Component Boundaries and Responsibilities

| Component | Status | Responsibility | Owns | May NOT |
|---|---|---|---|---|
| **Goal** | NEW (type) | the objective + acceptance criteria | criteria, scope | execute |
| **GoalInterpreter** | NEW | prompt → `Goal` (possibly multi-node) | interpretation only | plan topology |
| **Planner** | EXISTS (`graph/planner.py`) | `Goal` → `GraphProposal` (IR → compiled graph) | topology proposals | execute, mutate state |
| **ProposalValidator** | EXISTS in parts (`graph/validator.py`, `auth/decision.py`) | decide legality of every proposal | verdicts | mutate state |
| **GraphController** | NEW (thin) | apply validated proposals as transitions | state transitions | decide legality, execute |
| **GraphStore** | EXISTS (`graph/store.py`) | durable materialized state | persistence | semantics |
| **EventJournal** | EXISTS partially (`session_repo.py` + `graph_events`) | append-only transition history | history | semantics |
| **GraphExecutor** | EXISTS (`graph/executor.py`) | drive ready nodes within budgets | execution order, concurrency | topology, legality |
| **ToolExecutor** | EXISTS (`tool_executor.py:653`) | perform effects through 19 gates | effect execution | state, topology |
| **ObservationRecorder** | NEW (thin) | turn a raw tool result into a typed `Observation` | observations | interpret success |
| **Verifier** | EXISTS in part (`graph/verifier.py`, `core/verification.py`) | decide whether criteria are met, emit `Evidence` | verdicts, evidence | mutate state, execute |
| **RecoveryController** | NEW | classify failure, choose escalation | recovery decisions | bypass transitions |
| **ContextBuilder** | EXISTS (`context_assembler.py`) | decide what the model sees | context selection | mutate state |
| **RepoIntelligence** | EXISTS (`repo_map.py` et al.) | derived knowledge, advisory | index | authority |
| **DelegationBroker** | EXISTS (`SubagentOrchestrator`) | structured subagent delegation | child lifecycle | uncontrolled mutation |
| **BudgetGovernor** | NEW (unifying) | one place that answers "how much is left" | budgets | decisions |

---

## 3. Authority Model

```
                    ┌───────────────────────────┐
   model output ───►│  PROPOSAL  (typed, inert) │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │  VALIDATION               │  ← the only place legality is decided
                    │  schema · policy · authz  │
                    │  resources · preconditions│
                    └─────────────┬─────────────┘
                        legal ────┴──── illegal ──► REJECTED (recorded, no effect)
                                  │
                    ┌─────────────▼─────────────┐
                    │  GRAPH CONTROLLER         │  ← the only writer of state
                    │  applies the transition   │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │  EXECUTOR                 │  ← produces observations only
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │  VERIFIER                 │  ← produces evidence only
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │  RECOVERY CONTROLLER      │  ← proposes the next transition
                    └───────────────────────────┘
```

**The invariants:**

- I1 — Only the GraphController writes graph state. No tool, no subagent, no verifier, no recovery
  routine writes state directly.
- I2 — Every write is a transition of a typed proposal, recorded in the journal before it is applied.
- I3 — Reasoning components (Planner, RecoveryController, ContextBuilder) are **pure** with respect to
  state: they return proposals, never mutations.
- I4 — Validation is the single legality gate; it composes the existing `authorize()`,
  `SecurityPolicy`, `validate_graph` and budget checks rather than replacing them.
- I5 — An observation is never treated as evidence of success.
- I6 — Every state write carries provenance: `(proposal_id, actor, parent_transition, timestamp)`.

---

## 4. Data Flow

```
prompt ─► GoalInterpreter ─► Goal(acceptance_criteria)
                                 │
                                 ▼
                            Planner ─► GraphProposal ─► Validator ─► GraphController
                                                                            │
                                                     ┌──────────────────────┘
                                                     ▼
                                          Graph (materialized, durable)
                                                     │
                              ┌──────────────────────┴───────────────────┐
                              ▼                                          ▼
                    ready_nodes() ─► CLAIM ─► execute             ContextBuilder ─► model
                              │                    │
                              │                    ▼
                              │              Observation(s)  ─► journal
                              │                    │
                              │                    ▼
                              │              Verifier ─► VerificationResult + Evidence
                              │                    │
                              │        ┌───────────┴───────────┐
                              │        ▼                       ▼
                              │   SATISFIED                UNSATISFIED
                              │        │                       │
                              └────────┴───────────┬───────────┘
                                                   ▼
                                        RecoveryController ─► RecoveryProposal
                                                   │
                                                   ▼
                                            Validator ─► GraphController ─► graph mutated
```

**Key property:** the model appears exactly once in this diagram — behind `ContextBuilder` and in the
Planner. Every other arrow is deterministic code operating on durable state.

---

## 5. Control Flow and Graph Lifecycle

### 5.1 States

```
GOAL_OPEN ─► PLANNING ─► GRAPH_READY ─► EXECUTING ─┬─► VERIFYING ─┬─► GOAL_MET
                                                    │              └─► RECOVERING ─┐
                                                    │                              │
                                                    └─► STAGNATED ─► ESCALATING    │
                                                                                   │
                        ◄──────────────────────────────────────────────────────────┘
                                          (replan produces a new subgraph)

terminal: GOAL_MET · GOAL_FAILED · BUDGET_EXHAUSTED · CANCELLED · ESCALATED_TO_HUMAN
```

> **Annotated 2026-09-25 (ADR-0060).** This is the **target**, and its arrows are directional:
> `GRAPH_READY ─► EXECUTING` reads as *the graph drives execution*. **The implemented model is
> Position A** — the turn loop (`WispAgentCore.turn` / `AgentRuntime.run_turn`) is the driver and
> the graph is a durable record. ADR-0060 R2 records the graph-driven reading as **rejected**, with
> the reversal condition under which it would return. Read the diagram as the target, not as the
> system; nothing above is rewritten.

### 5.2 Node states

```
PENDING ─► READY ─► CLAIMED ─► RUNNING ─┬─► OBSERVED ─► VERIFYING ─┬─► SUCCEEDED
                                        │                          ├─► FAILED
                                        │                          └─► INCONCLUSIVE
                                        ├─► WAITING   (dependency · approval · human)
                                        ├─► BLOCKED   (predecessor failed)
                                        └─► CANCELLED
   any ─► INVALIDATED   (a later mutation invalidated its evidence)
   any ─► SUPERSEDED    (a replan produced a replacement node)
```

Differences from today (`graph/types.py:27-34`), and why each matters:

| Target state | Today | Why it must exist |
|---|---|---|
| `READY` | computed, not stored | a materialized readiness set is what makes the graph *persistent* state rather than a recomputation |
| `CLAIMED` | absent | needed for leases/idempotency across restarts |
| `OBSERVED` | absent | separates "the action happened" from "it worked" |
| `VERIFYING` | absent | makes verification an explicit, inspectable stage |
| `INCONCLUSIVE` | absent | honest third outcome; today everything is success or failure |
| `WAITING` | absent | distinguishes "not yet" from "blocked" |
| `BLOCKED` | approximated by `SKIPPED` | `SKIPPED` conflates "predecessor failed" with "dead branch" |
| `INVALIDATED` | absent | required for evidence staleness to be a state, not a flag |
| `SUPERSEDED` | absent | required for replanning to be visible |

### 5.3 Transition table (target)

| From | To | Trigger | Validator | Journal record | Reversible |
|---|---|---|---|---|---|
| PENDING | READY | all predecessors settled-ok | readiness predicate | `node_ready` | yes |
| READY | CLAIMED | executor lease | budget + concurrency | `node_claimed` | yes (lease expiry) |
| CLAIMED | RUNNING | worker starts | — | `node_started` | yes |
| RUNNING | OBSERVED | observation recorded | observation schema | `observation` | no |
| OBSERVED | VERIFYING | verification requested | criteria present | `verification_requested` | yes |
| VERIFYING | SUCCEEDED | verdict PASS | verdict schema + independence policy | `verification_result` | no |
| VERIFYING | FAILED | verdict FAIL | as above | `verification_result` | no |
| VERIFYING | INCONCLUSIVE | no criteria / verifier unavailable | policy | `verification_result` | no |
| FAILED | PENDING | retry within budget | retry policy | `node_retry` | yes |
| any | INVALIDATED | evidence invalidated by a mutation | invalidation rule | `node_invalidated` | no |
| any | SUPERSEDED | replan replacement | replan legality | `node_superseded` | no |
| GOAL_OPEN | PLANNING | goal accepted | goal schema | `goal_accepted` | yes |
| EXECUTING | RECOVERING | failure/stagnation classified | recovery policy | `recovery_started` | yes |
| RECOVERING | GRAPH_READY | replan validated | validator | `graph_extended` | yes |

---

## 6. Planning

**Retained:** `wisp/graph/planner.py` — model → IR → `narrow_ir` → `validate_graph` → compiled `Graph`,
with the `approve is True` gate (`planner.py:299`). This is a genuinely good design and becomes the
sole topology producer.

**Changes (conceptual):**
1. Input becomes a `Goal` (with acceptance criteria), not a free prompt.
2. Output is a `GraphProposal` carrying provenance (model, prompt hash, seed) rather than a bare graph.
3. Planning is **incremental**: a proposal may extend an existing graph rather than replace it.
4. `coding_graphs.py` templates remain as a deterministic fallback when the planner is unavailable.

**Not changed:** the compiler's determinism, `narrow_ir`'s narrowing guarantees, the optimizer pass
ordering, or the monotonicity gate (`optimizer.py:179-201`).

---

## 7. Execution

**Retained:** `GraphExecutor._drive` (`graph/executor.py:264-550`) — single-threaded async drive loop,
`asyncio.Task` per node, global + per-provider semaphores, `settle_one` with `FIRST_COMPLETED`, per-node
timeouts, bounded retry, bounded cycle correction, idempotency keys, checkpoints, resume.

**Changes (conceptual):**
1. Node state writes route through the GraphController instead of direct dict writes.
2. `ready_nodes` results are **materialized** into the READY state rather than recomputed each pass.
3. The executor gains one new capability: it can be handed a **newly inserted node** mid-run (this is
   the only genuinely new executor behavior).
4. Every node writes an `Observation` before a verdict is computed.

**Not changed:** determinism (sorted-id selection, `scheduler.py:33`), the join policies, the
concurrency bounds, or the retry semantics.

---

## 8. Verification

See `WISP_VERIFICATION_ARCHITECTURE.md` for the full design. Summary:

- A node produces `Observation`s, never a success claim.
- A `VerificationRequest` names the criteria and the evidence required.
- A verifier produces a `VerificationResult` with `evidence` entries carrying provenance.
- **Independence policy:** the verifier must not be the invocation that produced the observation. In
  the single-model case this is enforced structurally — a separate context, a separate prompt, and
  criteria the acting invocation never saw.
- Deterministic gates (tests, exit codes, hashes, schema) are preferred over semantic judgment and run
  first. `graph/verifier.py:39-72` already implements four such gates.

---

## 9. Recovery

See `WISP_RECOVERY_ARCHITECTURE.md`. Summary ladder:

```
Retry ─► Repair ─► Rollback ─► Local Replan ─► Global Replan ─► Diagnostic Task ─► Human Escalation
```

Each rung is a `RecoveryProposal`, validated and applied like any other transition. Rungs are
budgeted; exhausting a rung escalates rather than looping. The existing retry machinery
(`provider_stream.py`, `stateless.py:650-702`, `graph/executor.py:611-641`) becomes rung 1 unchanged.

---

## 10. Context

See `WISP_CONTEXT_ARCHITECTURE.md`. Summary:

- `ContextBuilder` becomes a named, testable subsystem with an explicit `ContextRequest` → `Context`
  contract.
- Every context item carries a **trust tag** (`SYSTEM` | `OPERATOR` | `REPOSITORY` | `TOOL_OUTPUT` |
  `EXTERNAL`) and a provenance reference.
- Repository and tool content is **delimited and labelled**, never concatenated into instruction prose.
- Graph context (current node, its criteria, dependency outputs, prior failures) is an explicit,
  budgeted section.
- Selection becomes deterministic given a `ContextRequest` (the LRU cache is keyed on the request).

---

## 11. Repository Intelligence

**Retained:** `RepoMap` + PageRank, `code_index`, `tree_sitter_index`, `import_graph`,
`semantic_index` (with its correct staleness refusal, `semantic_index.py:451-508`).

**Changes (conceptual):**
1. The turn path stops forcing `fast_mode=True` (`stateless.py:1469`), which today guarantees only a
   skeleton reaches the model (`repo_map.py:218-245`).
2. The map becomes an **input to proposal** (which files does this node touch?) as well as a context
   section.
3. Staleness becomes a first-class property reported to the loop, not just an internal cache decision.

**Not changed:** it remains **advisory**. It never grants authority and never writes state.

---

## 12. Delegation

See `WISP_SUBAGENT_ARCHITECTURE.md`. Summary:

- `SubagentContract` gains a **structured task** and a **mandatory result schema**.
- `derive_subagent` (`auth/principal.py:62`) is wired so capabilities actually narrow.
- `multi_agent/_circuit_breaker.py` is wired.
- Child effects in a shared workspace become transactional (worktree or patch-with-rollback).
- `multi_agent/dag.py` is **retired in favor of** `wisp/graph/` — one graph, not two.

---

## 13. Memory

| Class | Target home | Change |
|---|---|---|
| Working | graph node context (derived) | becomes derived from state, not the source of truth |
| Task | graph + goal rows | newly durable |
| Execution history | event journal | gains tool/observation events |
| Repository knowledge | RepoMap / semantic index | unchanged, advisory |
| Decision | audit chain + proposal records | gains proposal provenance |
| Verified knowledge | evidence records + `skill_capture` | gains provenance |
| Failure | failure records with a closed taxonomy | taxonomy becomes typed |

No new memory *systems* are introduced. The change is that existing stores gain provenance and become
reachable.

---

## 14. Persistence and Replay

**Target model:**

```
Immutable Event Journal  ──materialize──►  Graph State  ──project──►  Execution View
   (append-only)                            (durable rows)              (prompt / UI)
```

- The journal is the source of truth for *what happened*.
- Graph rows are the materialized state for *what is true now*.
- The message list and UI are **views** projected from both.

> **Annotated 2026-09-25 (ADR-0029, ADR-0060).** The third bullet is the one the migration corrected:
> the message list is a view projected from the **journal**, not from the graph — the graph carries
> **no transcript payload** (`TaskNode` has no payload field, and `PAYLOAD` is a declared kind with no
> member; ADR-0029). The graph contributes status and structure, not content. And the second bullet's
> `materialize` step runs **after** the turn, over the work the turn observably did — the graph is a
> **record**, not the driver (ADR-0060 R1/R2). Nothing above is rewritten.

**Concretely:** extend the already-wired `SessionRepository` (`core/session_repo.py`) and the existing
`graph_events` table; make `Session.apply` (`core/session.py:84-117`) handle `TOOL_CALL`; and give
normal turns a `RunRecord` by passing the existing `SQLiteRunStore` at the two construction sites
(`composition.py:192`, `tool_executor.py:1666`).

**Replay guarantee to aim for:** given the journal, the system can reconstruct the state as of any
recorded transition, and re-executing from that point is idempotent.

---

## 15. Observability

| Signal | Source | Status |
|---|---|---|
| Tool decisions + reasons | `audit_log`, `.wisp/audit.jsonl` | wired |
| Graph events | `graph_events` | wired (off-path) |
| Hash-chained provenance | `graph/audit.py:72-121` | wired (off-path) |
| Subagent telemetry rings | `SubagentTelemetryBuffer` | wired |
| Turn events | `session_events` | wired (3 kinds only) |
| Spans | `trace_spans` | **unwired** — wire it |
| Transition history | `run_transitions` | **unwired** — wire it |

Observability is **derived from the journal**, not a parallel system. This is the single most
cost-effective wiring change available.

---

## 16. Termination

Termination becomes **explicit and ordered**, replacing today's five unordered modes:

```
1. GOAL_MET           — all acceptance criteria verified        (success-based)
2. GOAL_FAILED        — criteria proven unsatisfiable           (failure-based)
3. ESCALATED_TO_HUMAN — recovery ladder exhausted               (escalation)
4. BUDGET_EXHAUSTED   — the BudgetGovernor reports zero         (budget-based)
5. CANCELLED          — operator action                         (cancellation)
6. TIMED_OUT          — wall-clock deadline                     (timeout)
```

Precedence is explicit: `GOAL_MET` beats `BUDGET_EXHAUSTED`; `CANCELLED` beats everything except an
already-recorded terminal state. Every termination writes a durable, reason-bearing transition.

---

## 17. What This Architecture Deliberately Does Not Do

- It does not introduce a second graph engine. `wisp/graph/` is the graph.
- It does not introduce a new authorization model. `authorize()` + `SecurityPolicy` are composed, not
  replaced.
- It does not require a distributed scheduler, a message bus, or a database migration to a new engine.
- It does not make the model responsible for state. The model reasons; code transitions.
- It does not rename existing modules to match the research report's vocabulary. Names follow the
  repository, not the report (`AGENTS.md` principle; brief §34).
