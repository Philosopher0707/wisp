# WISP — SUBAGENT ARCHITECTURE

**Phase 0 design document. Conceptual only — nothing here is implemented.**

> **Principle 9:** *Subagents should communicate through structured contracts rather than uncontrolled
> global mutation.*

---

## 0. The Current Position

Wisp's delegation layer is **substantially better than the brief would predict** — a typed contract
(`SubagentContract`), a typed result (`SubagentResult`), bounded fan-out, a worktree manager, a
dual-bounded telemetry ring with secret masking, and four orchestration patterns over a real DAG.

Its problems are specific, not structural:

| Problem | Severity |
|---|---|
| The task input is **free text**; only the *output* can be schema-validated | high |
| **Capability narrowing is implemented, tested, and never called in production** | **critical** |
| Failure classification is **prose-marker matching** | high |
| The subagent **circuit breaker is dead code** | medium |
| Shared-workspace effects are **not transactional** | high |
| **Two disjoint graph systems** (`multi_agent/dag.py` vs `wisp/graph/`) | medium |
| A latent defect: DAG node budgets are attached then dropped | medium |

---

## 1. Current Architecture (verified)

### 1.1 Entry points

| Path | Tool | Location |
|---|---|---|
| Blocking single | `spawn` | `tool_executor.py:1528` |
| Blocking parallel | `fanout` | `tool_executor.py:1895` |
| Blocking patterns | `orchestrate_{vote,map_reduce,chain,dag}` | `tool_executor.py:1818-1828` |
| Non-blocking | `spawn_background` | `tool_executor.py:1720` → `background.py:264` |
| Lifecycle | `subagent_{wait,list,result,send,cancel}` | `tools/subagent_tools.py:26-219` |

Execution: `orchestrator.run()` (`subagent_orchestrator.py:711`) → `SubagentRunner.run()`
(`_runner.py:152`) → `core.turn()` (`_runner.py:526`) or `AgentRuntime.run_turn()`
(`_run_via_runtime`, `:686`).

### 1.2 Contracts

`SubagentContract` (`multi_agent/task.py:57-174`):
`task: str` (free text), `role`, `system_prompt`, `tools`, `allowed_skills`, budgets
(`max_iterations`, `timeout_seconds`, `max_tokens`, `max_output_chars=8000`), `output_format`,
`output_schema`, `workspace`, `worktree_isolated`, `auto_approve`, `context_files`, depth/branch/retry
counters, `_shared_context`.

`SubagentResult` (`:213-271`):
`success`, `output` (free text), `error`, `files_changed`, `elapsed_seconds`, `tokens_used`,
`worktree_patch`, `patch_applied`, `messages`, `tool_calls`, `session_id`, `validated_output`.

**The contract layer is typed. The tool boundary is ad-hoc dicts.**

### 1.3 Context boundary

**Fresh context.** A new session is created with `messages=[{"role":"user","content":contract.task}]`
(`_runner.py:229-238`); the runtime path explicitly clears history
(`session_dict["messages"] = []`, `_runner.py:654-655`). The parent conversation is never passed.

Resume is the exception: `_resume_session_id` loads a stored session with full history
(`_runner.py:197-213`), used by `background.send()` (`background.py:500-523`).
`ContextPartitioner` filters only when messages > 10 (`_runner.py:494-510`), so it is **inert on
normal fresh spawns**.

### 1.4 Permissions and tool access

- The child **inherits the parent's `permission_mode`** — `_build_child_config` (`_runner.py:760-768`)
  does not override it.
- There is **no child approval handler**; `auto_approve=False` is forced at every construction site
  (`tool_executor.py:1597,1701,2036`).
- Tool narrowing happens in `_effective_child_tools` (`_runner.py:23-42`), intersecting
  requested/role tools with `filter_allowed_for_mode(permission_mode, …)`
  (`infra/policy_engine.py:297`). Result written to `session_dict["allowed_tools"]`
  (`_runner.py:487-490,658-661`) and enforced by the core.
- Role defaults supply `allowed_tools` (`roles.py:29,54,135,166,217`).
- **There is no read-only flag on the contract** — read-only-ness is inferred from role
  (`roles.py:152,175`) and from the tools list in `_auto_retry_safe`
  (`subagent_orchestrator.py:996-1015`).

### 1.5 Isolation

**Default is in-process asyncio on the parent's event loop**, sharing the parent's `ToolExecutor`
(`composition.py:172`, `_runner.py:475`), `UnifiedStore` (`_runner.py:143`), provider cache
(`_runner.py:148`), telemetry ring (`composition.py:198`), and `WorktreeManager`.

Worktree isolation is **opt-in** (`worktree_isolated=False`, `task.py:122`) and skipped for read-only
roles (`subagent_orchestrator.py:662-709`). `WorktreeManager` runs `git worktree add` under
`.wisp/worktrees` (`_worktree_manager.py:50-119`), copies the parent's uncommitted diff and untracked
files **into** the worktree (`:84-116`), and the child's patch is captured and applied back under
`_patch_lock` (`subagent_orchestrator.py:902-924`).

**`max_memory_mb` (`task.py:167`) is never enforced** — definition only. Process/memory isolation is
absent.

### 1.6 Outputs

Structured output is **opt-in** via `output_schema` → `_validate_output`
(`subagent_orchestrator.py:1458`; `schema_validator.py`). Output truncated to
`max_output_chars=8000` (`_runner.py:301-305`); tool envelopes cap at 2000
(`tools/orchestration.py:32`). Persisted to JSONL `.wisp/subagent_results.jsonl`
(`subagent_orchestrator.py:394`, preview `output[:500]`), plus the child session via
`store.save_session` (`_runner.py:266`).

### 1.7 Failure

`run()` never raises; failures become `SubagentResult(success=False)`
(`subagent_orchestrator.py:715-718`). Retries: `_run_with_retry` (`:1538`),
`run_parallel._guarded` transient loop (`:1068-1114`), timeout ×1.5 (`:858-876`), schema repair
(`:1487-1497`). Gated by a shared logical budget `max_retries ≤ 5` (`task.py:44`) and
`_auto_retry_safe`.

**Classification is prose-marker based:** `_TRANSIENT_MARKERS` (`:933`) and
`"timeout" in last_error` (`:1577`).

### 1.8 Cancellation and timeout

`asyncio.timeout(contract.timeout_seconds)` (`_runner.py:251`) plus per-iteration
`asyncio.timeout(remaining)` (`:522,685`); `FirstTokenTimeout` (`:94,531-536`).
Cancel: `request_cancel_live`/`cancel_live_tasks` (`subagent_orchestrator.py:438-475`) and
`_cancel_and_reap` with a 2 s join (`:46-84`); background `cancel` (`background.py:549-561`);
TUI `c` calls it (`tui/screens/subagents.py:131`).

`wait` clamps to the parent turn deadline via `get_turn_deadline` (`tools/subagent_tools.py:68-79`;
ContextVar `tools/context.py:70-75`), floor 1 s, cap 3600 s.

**Ambiguity:** cancellation is cooperative; a shared-workspace child may already have written files
with **no rollback**. Retry of shared-workspace work is refused only heuristically
(`_auto_retry_safe:996-1015`); a cancelled child's partial mutations are never reconciled.

### 1.9 Fan-out and budgets

| Bound | Value | Location |
|---|---|---|
| Max running agents | 8 | `background.py:29` |
| Subagent branch | 3 | `tool_executor.py:1948-1960` |
| Subagent depth | 2 | `tool_executor.py:1962-1973` |
| Pool size | 4 | `:412-414` |
| DAG parallelism | ≤ 8 | `tools/orchestration.py:210` |
| Token budget | 2 000 000 | `config.py:653-655`; `composition.py:181-183` |
| Telemetry rings | 500 events / 256 KB | `telemetry.py:54-56,154-156` |
| Finished retention | 50 | `background.py:30,584-594` |
| Vote voters | 2–6 | `orchestration.py:62` |
| Map items | ≤ 20 | `:102` |
| Chain steps | ≤ 6 | `:143` |

Background admission `_admit` (`background.py:244-262`) → `Scheduler.admit` (`runs/scheduler.py:32-40`).

### 1.10 Telemetry

`SubagentTelemetryBuffer` (`telemetry.py:90`): per-agent `deque[TeleEvent]`, bounded in **both**
events (500) and UTF-8 bytes (256 KB), oldest-first eviction (`:154-156`), per-event cap 4000 chars
(`:56,126-127`). Kinds `started|thinking|tool_call|tool_result|progress|settled` (`:34`).
`mask_text()` (`:38-52`) calls `wisp.auth.secrets.redact` before an event enters any ring.

**Fail-open:** if the import or `redact` raises, it returns the **raw text** (`:48,52`). This is a
deliberate availability choice but is worth revisiting — telemetry is exactly where a secret leak is
least noticeable.

### 1.11 Orchestration patterns

| Pattern | Location | Behaviour |
|---|---|---|
| `vote` | `_patterns.py:136` | same task → N contracts via `run_parallel`; normalized-similarity grouping; majority vs threshold; tie-breaker subagent (`:211-236`) |
| `map_reduce` | `:18` | mapper contract per item → `run_parallel`; retry failed; reducer synthesizes (`:121-130`) |
| `chain` | `:274` | sequential; last-3 outputs passed forward (`:315-321`); optional shared chain worktree (`:298-313`) |
| `dag` | `dag.py:37` `TaskDAG`, `:146` `DAGScheduler` | **a real graph** — Kahn cycle validation (`:80-107`), topological levels (`:109-131`), dependency-output injection (`:186-197`), failed-node descendant blocking (`:219-244`) |

**`dag` does not interact with `wisp/graph/`** — no `wisp.graph` import exists in `multi_agent/`.
Meanwhile `graph/runner.py:124` independently constructs its own `SubagentOrchestrator`. **Two
disjoint graph systems, two orchestration paths, no bridge.**

### 1.12 Dead code

`wisp/core/subagent/` is an **empty namespace package** — only stale `.pyc` files for
`coordinator`/`pool`/`protocol`; **zero `.py` sources**; nothing imports it. Dead bytecode.

`multi_agent/_circuit_breaker.py:28` is **imported nowhere**. The live breaker is
`infra/circuit_breaker.py`, used only for provider streaming (`core/stateless.py:1061-1085`).

---

## 2. The Silent Global Mutation Inventory

Twelve paths, from the audit (§17.3):

| # | Path | Mediated? |
|---|---|---|
| 1 | Parent files (shared workspace default) | permission mode only |
| 2 | Shared `ToolExecutor` instance (root + all children) | per-call ContextVars |
| 3 | Shared SQLite store (children save sessions in the parent DB) | no |
| 4 | `SharedContext` — an **untyped** key/value dict visible to all siblings (`shared_context.py:69`) | no |
| 5 | Provider cache shared per orchestrator (`_runner.py:148,442-446`) | no |
| 6 | Global telemetry ring (`background.py:113`; `composition.py:198`) | bounded, but global |
| 7 | Append-only global JSONL (`subagent_orchestrator.py:173-192`) | no |
| 8 | Git operations: `git add -A`, `git apply`, `git checkout -f`, branch delete (`_worktree_manager.py:225-230,267-298`) | conflict-only revert |
| 9 | Contract mutation: `_cache_context` stamped; `progress_callback` overwritten; `_shared_context` assigned into caller contracts (`subagent_orchestrator.py:781,1052-1055`; `background.py:356`) | no |
| 10 | DAG node metadata mutated in place (`dag.py:197`) | no |
| 11 | Result objects rewritten post-hoc (`subagent_orchestrator.py:914,922,928`; `_runner.py:295-305`) | no |
| 12 | Partial patch application — **non-transactional** (`subagent_orchestrator.py:919-924`; `_worktree_manager.py:144-201`) | conflict-only revert |

**The most consequential is #12.** A patch that applies cleanly to some files and conflicts on others
leaves the parent workspace in a state that corresponds to no completed subagent result.

---

## 3. The Critical Finding: Capability Narrowing Is Inert

`derive_subagent(parent, capabilities)` (`auth/principal.py:62`) implements principal narrowing — the
mechanism by which a child receives **fewer** capabilities than its parent.

**It is called only in tests** (`tests/test_auth_principal.py:25-39`, `tests/test_auth_decision.py:37-38`).
Verified by grep across the whole repository: **zero production call sites.**

Consequently the executor authorizes every tool call — parent and child alike — with
`local_principal(workspace=…, profile=…)` (`tool_executor.py:708`), which has
`capabilities=None`, i.e. **unbounded**.

So a subagent inherits **full root authority**, mitigated only by:
- `auto_approve=False` on child contracts (`tool_executor.py:1597,1701,2036`);
- depth and branch caps (`:1946-1973`);
- `filter_allowed_for_mode` (mode-filtered tool *advertisement*).

**Why this matters for the target architecture:** `DelegationRequest` (`WISP_PROPOSAL_PROTOCOL.md`
§3.8) requires `capabilities` to be a subset of the parent's. That requirement is unenforceable today
because the narrowing function is never invoked. **Wiring `derive_subagent` is a prerequisite for
structured delegation**, and it is a small change to an already-tested function.

---

## 4. Target Architecture

### 4.1 Structured delegation contract

```
DelegationRequest
  parent_task_ref : the delegating node
  child_goal      : a structured goal (not free text) — statement + acceptance criteria
  inputs          : explicit, typed inputs (files, artifact refs, prior observations)
  required_outputs: what the child must produce
  result_schema   : MANDATORY (today: opt-in)
  role            : a named role (retained)
  capabilities    : an explicit subset of the parent's   <-- APPLIED via derive_subagent
  isolation       : WORKTREE | SHARED                    <-- explicit, not defaulted
  budget          : tokens / wall / tool calls / iterations
  deadline        : wall clock
  provenance      : who delegated and why
```

**Changes and their justification:**

| Change | Today | Why |
|---|---|---|
| Structured `child_goal` | `task: str` (`task.py:57`) | a free-text task cannot be validated, budgeted, or verified against criteria |
| Mandatory `result_schema` | opt-in (`output_schema`) | a delegation whose result cannot be validated cannot participate in verification |
| Applied `capabilities` | never invoked | the single most important fix in this document |
| Explicit `isolation` | defaulted to `SHARED` (`task.py:122`) | shared-by-default is the wrong default for a system that now has durable state |
| Typed `Failure` | prose markers (`subagent_orchestrator.py:933,1577`) | recovery cannot choose a rung from prose |

### 4.2 Context boundary — declared, not implicit

The child receives a **fresh context** (today's behaviour, correctly) plus exactly the declared
`inputs`. `ContextPartitioner` (`_runner.py:494-510`) is currently inert on normal spawns because it
only fires above 10 messages; in the target the boundary is **declared in the contract** and enforced
regardless of size.

**Rule:** the parent's transcript is never implicitly available. `resume` is an explicit, recorded
choice, not a code path.

### 4.3 Result contract

```
DelegationResult
  child_task_ref  : the child's task in its own scope
  success         : bool
  output          : validated against result_schema (admission gate)
  artifacts       : evidence refs flowing back to the parent
  evidence        : evidence refs the parent may cite
  failure         : a typed Failure, or null
  files_changed   : as today
  tokens_used     : as today
  patch           : worktree patch + whether it was applied
  provenance      : chain
```

**Admission rule:** a child result that fails `result_schema` is **not admissible** — it is a
`VERIFICATION` failure of the delegation, not a successful delegation with a messy output.

### 4.4 Transactional effects

| Isolation | Guarantee |
|---|---|
| `WORKTREE` | the child works in an isolated worktree; the patch is applied atomically or not at all (all-or-nothing, with conflict → reject) |
| `SHARED` | the child's file mutations are recorded as `EditRecord`s (`runs/compensation.py`) so they can be rolled back as a unit on failure or cancellation |

**This closes the audit's #12.** `runs/compensation.py` already defines `EditRecord` (path, unified
diff, pre-image hash, reversible, note, version) and a `_REVERSIBILITY` tool map (`:40-54`) — its
docstring states *"No tool wiring"*. It is the durable record this guarantee needs.

### 4.5 One graph, not two

**`multi_agent/dag.py` should be retired into `wisp/graph/`.**

| Property | `multi_agent/dag.py` | `wisp/graph/` |
|---|---|---|
| Cycle validation | Kahn (`:80-107`) | `validator.py:106-155` |
| Topological levels | yes (`:109-131`) | `scheduler.py:25-44` |
| Dependency output injection | yes (`:186-197`) | `metadata["_dep_results"]` |
| Failed-descendant blocking | yes (`:219-244`) | `dag.py::_block_descendants` |
| Durability | **none** | SQLite (`store.py:132-284`) |
| Audit / provenance | none | hash chain (`audit.py:72-121`) |
| Artifacts | none | content-addressed (`artifacts.py`) |
| Validation | cycle + structure | 6 categories (`validator.py`) |

The `dag` orchestration pattern is a **strictly weaker** version of the graph engine. Retiring it
removes a whole class of divergence — the audit's §27 risk "two-graph divergence" — and gives the
`dag` tool durability, audit and artifacts for free.

### 4.6 Wired failure containment

`multi_agent/_circuit_breaker.py` (3 failures / 120 s, manual reset) exists and is imported nowhere.
Wire it, or delete it — but do not leave a containment control that appears to exist and does not.
The audit's §27 risk register calls this pattern out explicitly, and `CONTEXT.md:216-286` documents
it as the repository's dominant pathology.

### 4.7 The latent budget defect

`subagent_orchestrator.py:1316` writes `task.metadata`, but `SubagentContract` has **no `metadata`
field**. `_runner._budget_from_contract` reads `getattr(contract, "metadata", None)` (`_runner.py:78`),
so it never sees the DAG node's declared budget — **contradicting the comment at `_runner.py:54-67`
which claims the fix landed.**

This is Phase 10's F1 fix (`CONTEXT.md:255-275`, "the DAG budget, now honored") reappearing in a
second location that the fix did not reach. It is recorded here rather than fixed, per the audit-only
constraint.

---

## 5. Failure, Cancellation and Timeout — Target

| Concern | Today | Target |
|---|---|---|
| Failure detection | `success=False`; prose markers | typed `Failure` with a class from the closed taxonomy |
| Transient retry | prose-marker gated | `TRANSIENT` class gated |
| Circuit breaker | dead code | wired, or removed |
| Timeout | `asyncio.timeout` + ×1.5 retry | retained; timeout becomes a `Failure` class |
| Cancellation | cooperative, 2 s reap, **no rollback** | cooperative + transactional effects (§4.4) |
| Cancelled partial mutations | never reconciled | rolled back as a unit, or recorded as retained-and-declared |
| Result after cancel | rewritten post-hoc | frozen; a cancelled child produces a `CANCELLED` result |
| Deadline clamp | `wait` clamps to the parent deadline (`subagent_tools.py:68-79`) | retained — correct |

**The cancellation rule:** a cancelled child must leave the parent workspace in exactly one of two
states — *unchanged*, or *a recorded, declared set of changes*. "Partially changed, unrecorded" is the
state the target eliminates.

---

## 6. What Must Not Change

| Component | Why retain |
|---|---|
| `SubagentContract` / `SubagentResult` | correct typed envelopes; extend, do not replace |
| Fresh-context default (`_runner.py:229-238`) | correct isolation of reasoning |
| `_effective_child_tools` ∩ `filter_allowed_for_mode` | correct narrowing mechanism |
| `auto_approve=False` on children | correct conservatism |
| Depth/branch/pool bounds (3 / 2 / 4 / 8) | correct and tested |
| `WorktreeManager` (patch capture, conflict revert, copy-in of uncommitted state) | genuinely good engineering |
| `SubagentTelemetryBuffer` (dual-bounded + masking) | correct bounded-state design |
| The four orchestration patterns | each is genuinely useful; `vote`'s tie-breaker is a nice touch |
| `wait` deadline clamping | correct |
| `mask_text` before ring insertion | correct producer-boundary masking |

---

## 7. Open Questions

1. **Should `SHARED` isolation survive at all?** It is faster and avoids git worktree overhead, but it
   is the source of every mutation hazard in §2. Recommendation: retain, but require an explicit
   opt-in and transactional effect recording.
2. **What is the cost of mandatory `result_schema`?** Some delegations are genuinely exploratory. The
   target could allow `schema: NONE` explicitly — but then the result cannot feed verification.
3. **How does a delegated goal interact with the parent's acceptance criteria?** A child's criteria
   must be a *decomposition* of the parent's, or the parent's verification has no basis. This is the
   same open question as `WISP_VERIFICATION_ARCHITECTURE.md` §9.1.
4. **Should the `dag` orchestration pattern be deprecated in the same release as the retirement, or
   after a deprecation window?** It has live tests (`test_dag_cancel.py`).
