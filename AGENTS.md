# AGENTS.md

Guidance for AI coding agents working in the Wisp codebase.

## How to approach tasks

1. **Read the architecture layers** — know which layer your change belongs in before coding
2. **Follow existing patterns** — new transports extend `Transport` ABC, new tools add schemas to `registry.py`, CLI rendering uses pure functions from `renderer.py`
3. **Test first** — all new code needs tests. Transport tests use a locally-defined `_MockRuntime` plus `StringIO`-based stdin/stdout. Core tests use `MockProvider` (the canonical test double) with a real `WispAgentCore`
4. **Mode-aware output** — anything rendered to terminal must handle all 4 output modes (unicode, ascii, accessible, minimal). Use `BoxChars`, `OutputMode`, and `display_width()`
5. **Stateless core** — `WispAgentCore` has no mutable state. Session state lives in `AgentRuntime`. Tools are pure functions

## Module map

| Module | Purpose | Key exports |
|--------|---------|-------------|
| `wisp/core/stateless.py` | Stateless turn engine | `WispAgentCore.turn(session, prompt, approval_handler)` → `AsyncIterator[dict]`; env-tuned stream knobs (`FIRST_TOKEN_DEADLINE_S`, `CHUNK_DEADLINE_S`) live here |
| `wisp/core/provider_stream.py` | Provider stream guard | `guarded_provider_stream()`: first-token + mid-chunk stall deadlines, transient-error/empty-stream retry with backoff, honest truncation notice; all deps injected (stream opener, normalizer, deadlines) so it is testable without a core. Owns **recovery**, not canonicalization (ADR-0039 R5), and decides meaningfulness from an event's **payload** for every type (ADR-0043) |
| `wisp/core/convergence.py` | The objective-level loop (**ADR-0045**) | `ConvergenceController` (derive acceptance → run a turn → measure with a harness probe → `acceptance.evaluate` → `goal.derive_goal_state` → `recovery.classify_failure*` → `RecoveryLadder.decide` → repeat; bounded, journalled, resumable), `Objective`, `CommandSpec`/`SymbolSpec`, `CommandProbe`, `Measurement`, `derive_acceptance` / `explain_acceptance` (**ADR-0048**), `WITNESS_FIELDS` / `witness_digest`. Consumes every existing authority and re-implements none |
| `wisp/core/progress.py` | Objective-relative progress (**ADR-0046**) | `evaluate_progress(before, after, criteria)` → `NO_PROGRESS` / `MEANINGFUL_PROGRESS` / `PROGRESS_UNDETERMINABLE`; `PROGRESS_CONTINUATION_RUNGS` (total; `ENVIRONMENT` gains exactly `REPAIR`; empty for `SECURITY`/`REPEATED`/`STAGNATION`). Host-owned, deterministic, reads no model text. A moved `inputs_digest` disqualifies the criterion **first** |
| `wisp/autonomous.py` | The convergence wiring (**ADR-0045**) | `converge_on_objective`, `observe_turn` (delegates the turn predicate to `terminal_outcome_from_evidence` — never re-derives it), `compose_attempt_prompt`, `workspace_fingerprint`, `changed_files`. Reads `WISP_CRITERIA_STRICT_DERIVATION` at this composition point (ADR-0048 R5) |
| `wisp/core/engine.py` | Back-compat shim | Re-exports `WispAgentCore` from `stateless.py` |
| `wisp/core/events.py` | Event system | `AgentEvent`, 12 factory functions (`thinking()`, `tool_call()`, etc.), `EventType` enum |
| `wisp/core/runtime.py` | Session management | `AgentRuntime`: session CRUD, per-session locks; `_get_core(session_id)` caches one `WispAgentCore` per (session, fingerprint), FIFO-bounded (`MAX_SESSION_CORES`); `invalidate_core_cache()` on config change |
| `wisp/transport/base.py` | Transport ABC | `Transport`: `send()`, `recv()`, `approve()`, `start()`, `stop()` |
| `wisp/transport/cli.py` | CLI transport | `CLITransport`: REPL loop, event rendering, thinking/content buffering; `_write_bg_line()` pause/write/resume protocol for bg notices; `open_subagent_monitor()` suspend/park/drain bridge |
| `wisp/cli/dispatcher.py` | Slash-command router | `Dispatcher.register()` decorator + `ReplContext` (runtime, transport, session, config); builtins: help/doctor/provider/model/expand/subagents/rewind/hooks; unknown names fall back to legacy registry, never reach the LLM |
| `wisp/transport/renderer.py` | Terminal rendering | Pure functions: `render_tool_call()`, `_box()`, `_rule()`, `render_phase_bar()`, `render_turn_stats()` |
| `wisp/transport/progress.py` | Progress tracking | `ProgressTracker`, `TurnProgress` — phase detection, tool counting, file tracking |
| `wisp/transport/spinner.py` | Terminal spinner | `Spinner` — inline `\r`-based spinner with mode-aware frames |
| `wisp/transport/websocket.py` | Live WebSocket transport | `WebSocketTransport`: connection ↔ session routing, event streaming, approval. **The approval round-trip is COMPLETE (ADR-0061).** The *answer* direction works and all three clients send `tool_approval`/`{id, approved}`; the *question* direction now emits the frame they read — `tool_approval_request` / `{call_id, name, arguments, reason}`. `call_id` **is** the `_approvals` key, which is what the clients echo back as `id` (ADR-0061 R2). The bound is **60 s**, unchanged (R3). **No client ⇒ deny** with a named, distinguishable reason (`NO_CLIENT_REASON`); `WISP_WS_AUTO_APPROVE=true` is the one explicit opt-in. Before ADR-0061 the frame was `approval_request`/`{approval_id, tool_call}`, which **no** client read, so the prompt never rendered and every request timed out to deny (W1). Wired through `wisp/server/routes/agents.py` |
| `wisp/server/approval_bridge.py` | REST-originated approvals (ADR-0057) | `ApprovalBridge` — a registry of connected channels plus the round-trip, owning its **own** correlation map so `WebSocketTransport`, `approve()` and the agent path are untouched. Speaks the vocabulary **both clients already read** (`REST_APPROVAL_FRAME = "tool_approval_request"`, correlated on `call_id`), so the client change is **zero**. `REST_APPROVAL_ACTIONS` / `REST_APPROVAL_MODES` / `action_requires_rest_approval()` state the trigger; `REST_APPROVAL_TIMEOUT_S` bounds it; **no client ⇒ deny** |
| `wisp/server/deps.py` | FastAPI dependencies | `verify_api_key`, `SQLiteRateLimiter`, `request_policy`, `require_tool_allowed` (the REST policy gate), **`require_rest_approval`** — its **async** companion (ADR-0057), called *after* the policy gate so a denying mode never prompts |
| `wisp/server/routes/hooks.py` | Hook registration | `POST /api/hooks` **does not content-validate `command`, and that is the decision** (ADR-0061 R6 / G3): **the gate restricts WHO may register a hook, it does not restrict WHAT the hook runs.** A shell command's target is not determinable from its text (G2), so a runnable check, a metacharacter blocklist and an allow-list are each rejected with reasons. `name` **is** validated (a path-traversal allowlist); the asymmetry is the decision. The controls that work are authorization: the API key, `require_tool_allowed`, and — with `WISP_REST_APPROVAL` on — a human |
| `wisp/transport/headless.py` | Headless transport | `HeadlessTransport`: collects events into result dict, no I/O |
| `wisp/tools/registry.py` | Tool definitions | `TOOL_SCHEMAS` (list), `TOOL_IMPLS` (dict), `execute_tool()`, `ToolRegistry` |
| `wisp/tool_executor.py` | Tool call lifecycle | `ToolExecutor`: approval gating, pre/post hooks, dangerous-command blocking, metrics; named tools dispatch via `_SPECIAL_TOOL_ROUTES` table (uniform `(executor, func_args, workspace)` adapters), then MCP / run_bash / generic-pool branches |
| `wisp/tools/orchestration.py` | Orchestration pattern tools | `vote`, `map_reduce`, `chain`, `dag` behind `OrchestrationDeps(orchestrator, build_contract, tool_error)` — free functions, executor methods are one-line delegates |
| `wisp/tools/subagent_tools.py` | Background-subagent lifecycle tools | `wait`/`list_agents`/`result`/`send`/`cancel` behind `SubagentDeps(resolve_manager, tool_error)`; wait clamps to the parent turn deadline |
| `wisp/multi_agent/` | Subagent system | `SubagentOrchestrator`, `SubagentRunner`, `WorktreeManager`, `BackgroundAgentManager`, `SubagentTelemetryBuffer` |
| `wisp/multi_agent/background.py` | Background agent registry | `BackgroundAgentManager`: launch/send/cancel, lifecycle pub-sub (`agent_started/progress/settled`); publishes all lifecycle + chained TASK_* events into `telemetry` rings; `prune()` drops rings |
| `wisp/multi_agent/telemetry.py` | Per-agent telemetry rings | `SubagentTelemetryBuffer`: dual-bounded (events + bytes) per-worker rings, replay (`transcript`) + cursor poll, settle-status mapping; `mask_text()` producer-boundary secret masking |
| `wisp/multi_agent/dag.py` | **Legacy DAG entry point (M8) — `wisp/graph/` is the graph engine** | `TaskNode` / `TaskDAG` / `DAGScheduler` / `DAGResult`. Kept for the two live callers (`orchestrate_dag`, `SubagentOrchestrator.run_dag`); **not** the canonical graph implementation. **ADR-0060 re-scopes its divergence from a blocker to a boundary**: `wisp/graph/` requires a single reachable entrypoint and `TaskDAG` is a general partial order, so the two answer different questions and a re-point would reject inputs `orchestrate_dag` accepts today. The removal is **not owed**; choosing which definition of a valid DAG wins is a change to a live model-callable tool. Guards: `tests/reliability/test_dag_retirement_contract.py`, `tests/reliability/test_layer_b_boundary.py` |
| `wisp/tools/checkpoints.py` | File checkpoints | `CheckpointStore` (bounded per-workspace snapshots), `snapshot_before_mutation()`, `tool_rewind` (list/restore, rewindable rewind); auto-hooked in write/edit/edit_multi with drop-on-failed-mutation |
| `wisp/tui/screens/subagents.py` | Worker monitor screen | `SubagentMonitorScreen` (roster + live transcript, `]`/`[` cycle — never Tab — `c` cancel, `q`/`Ctrl+O`/`Esc` exit) + `SubagentMonitorApp` standalone host for the REPL bridge |
| `wisp/sandbox/` | Command confinement | Package (`__init__` = providers); `router.py`: `SandboxRouter` (Docker → `PtySandbox` → `NoopSandbox`, TTL-cached decision, silent failover) + `get_router()`; legacy `get_sandbox()` unchanged |
| `wisp/tools/primitives.py` | Thin harness surface | `exec_sandbox` / `fs_mutate` / `git_checkpoint` (pydantic args, delegate to bash/filesystem/checkpoints); `PRIMITIVE_SCHEMAS`; core opts in via `thin_tools` config (schemas + prompt menu + dispatcher) |
| `wisp/core/verification.py` | Completion gate | `VerificationFloorGuard`: blocks finish until exit-0 postdates last mutation or grind floor (`min_turns` + nudges) exhausts; `HARNESS_REJECTION` text; `resolved()` triggers auto-capture. **Also projects itself onto the acceptance model** via `floor_guard_criteria/evidence/verdict()` — read-only; the guard's own behaviour is unchanged |
| `wisp/core/acceptance.py` | Acceptance verdicts (P3, stage 3a) | `Verdict` (`PASS`/`FAIL`/`INCONCLUSIVE`), `CriterionKind`, `AcceptanceCriteria`, `Evidence` (content-addressed, with `producer` + `observations`), `CompletionVerdict`, `evaluate()`, `invalidate()`, `route_for()`. **Records; does not gate** — routing is *derived* from the verdict so the graph vocabulary stays an output, not a competitor |
| `wisp/core/turn_criteria.py` | The turn path's criteria set (**ADR-0053**) | `turn_criteria(guard, prompt, workspace, enabled=)` — `floor_guard_criteria(guard)` **unioned with** the objective's declared criteria (`convergence.explain_acceptance(..., use_declaration=True)`) and the declaration's `CommandProbe` evidence; `turn_acceptance_verdict()`; `verdict_keys_on_declared()` — **the gate's condition**: a `FAIL` whose every named criterion is non-floor. Re-implements nothing: the floor producer, the derivation, the probe and `acceptance.evaluate` are all consumed unchanged. With `enabled=False` it returns exactly `floor_only(guard)` |
| `wisp/core/goal.py` | Goal-state arbiter (**ADR-0035**, POST-M13) | `TerminalOutcome` (3), `GoalState` (6), `PRECEDENCE` (ADR-0035's ordered table **as data**, so the contract can be compared against the code without reverse-engineering branches), `derive_goal_state()` — **pure and total** (the final `return` is reachable, so no input combination raises or falls through), `already_recorded_from()`, `goal_state_from_record()` (fails **loud** on absence: "missing" and "unreadable" are different facts). **`GOAL_MET` is reachable only through row 6**, which requires `turn_succeeded` **and** an acceptance `PASS` — so a terminal `done` alone can never become goal success, and an absent verdict yields `GOAL_UNVERIFIED` rather than an optimistic pass. Completion lives here; recovery lives in `core/recovery.py`, and **completion is evaluated first** |
| `wisp/auth/principal.py` | Principals (P9) | `local_principal()`, `derive_subagent()` (narrows; raises on widening), and **`child_principal(parent, contract)`** — the caller-shaped wrapper that reads the contract's declared tools. `["all"]` against a **bounded** parent inherits that set; against an **unbounded** parent it is **refused** (there is no universe to subset, and both guesses are wrong). `ToolExecutor(principal=…)` authorizes as it; **the spawn site does not pass one yet** (M15) |
| `wisp/core/context_trust.py` | Context trust boundary (P8) | `TrustTag` (SYSTEM/OPERATOR/REPOSITORY/TOOL_OUTPUT/EXTERNAL), `Influence`, `may_influence()` (**the single authority** for T3), `ContextItem` (tag + **required** `Provenance`), `assemble()` → `Context` with a **structured** `dropped` list. T1–T4 enforced structurally; untrusted content is always fenced as `<<UNTRUSTED:TAG source=…>>`. **Defaults to tagging-only**; nothing on the live path produces tagged items yet (M14) |
| `wisp/core/oscillation.py` | Oscillation detection (Layer A, **relocated by ADR-0060 R5**) | `diff_hash()` — a stable, content-addressed diff identity; `OscillationTrap` — detects 1-cycle repeats and 2-cycle oscillations. **Both moved here from `wisp/core/graph/loop.py`** (Layer C), because this is where the *live* consumers are (`core/stagnation.py`, `core/runtime.py`) and ADR-0001 had named Layer C *disowned* — a disowned layer that the live path imported from. `loop.py` re-exports both, so the public surface is unchanged. **The rule is a direction:** a disowned layer may import from Layer A; the live path may never import from a disowned layer. The trap is **monotonic** (no reset — that is *how* ADR-0037's latch is achieved) |
| `wisp/core/stagnation.py` | Stagnation detection (P7, wired by M13) | `ProgressSignal` (criteria satisfied / failing set / artifact hashes / completed nodes / **action identities**), `StagnationDetector` (**reuses** `core/oscillation.py::OscillationTrap` — AST-pinned that it does not define its own), `route_to_recovery()`, `may_report_goal_met()`. Reads `config.graph_oscillation_guard`, which was **never read before P7**. **M13 constructs one per turn on the live path** and journals a `STAGNATION` record when the verdict is reached — it **records; it does not act** (ADR-0034). An empty observation is not evidence of stagnation (F32) |
| `wisp/core/recovery.py` | Recovery ladder (P6) | `FailureClass` (closed, 10), `RecoveryRung` (7), `LEGAL_RUNGS` / total `FORBIDDEN_RUNGS`, `classify_failure()` (delegates to `classify_result`), `BudgetGovernor`, `RecoveryLadder`, `HumanIntervention` (durable, resumable), `plan_rollback()` — the first caller of `runs/compensation.py`'s declarations. **Denials never retry, by class** (ADR-0024). **The turn loop does not consult it yet** (M12) |
| `wisp/core/task_graph.py` | Materialized task graph (P4) + runtime mutation (P5) | `TaskNode` / `TaskGraph` (with **stored** `ready`), `TaskNodeState` (14 states — a **superset** of `NodeStatus`), `NodeTransition`, `LEGAL_NODE_TRANSITIONS`, `build_turn_graph()`, `materialize()`, `apply_transition()`, `replay_transitions()`, `divergences()`. P5 adds `create_node()` / `expand()` / `invalidate()` (transitive cascade) / `supersede()` + an enforced `GraphGrowthBudget`. **Every mutation is a pure function** — a mutated-in-place graph cannot be replayed. Journals through `UnifiedStore`, so the graph is a **projection of the log** (ADRs 0019–0023) |
| `wisp/core/action_key.py` | Durable idempotency (P1) | `action_key(tool, args)` — sha256 over canonical sorted JSON; JSON-string and dict args hash alike, unserializable args degrade to `repr` rather than raising. Stamped on `TOOL_CALL` (intent) and its `TOOL_RESULT` (resolution) so "dispatched but never resolved" is queryable via `Session.unresolved_actions()` |
| `wisp/core/proposal.py` | Proposal boundary (P2) | `build_proposal()` → `contracts.tool.ToolRequest`, `build_outcome()` → `ToolResult`, `is_refusal()`. **Records, never decides** — status derivation delegates to `core.events.classify_result()`. Produces a proposal per dispatched call and an outcome per proposal, rejections included; journaled as audit-only `PROPOSAL`/`OUTCOME` events |
| `wisp/benchmark/` | Benchmark + predictions | `run_task` (isolated ws, git-baseline/diff patch capture), `BenchResult.model_patch`, `run_bench --predictions PATH` (SWE-bench `{instance_id,model_patch,model_name}` JSONL), injectable core factory |
| `wisp/skill_capture.py` | Workflow capture | `SkillCapture`: record tool sequences, detect repeats, render Warp-compatible SKILL.md with merge-on-recapture; `capture_resolved_skill()` persists verified turns to `.wisp/skills/auto/` |
| `wisp/config.py` | Configuration | `WispConfig` dataclass |
| `wisp/colors.py` | Terminal colors | `success()`, `error()`, `warning()`, `dim()`, `info()`, `accent()`, `bold()` |
| `wisp/terminal_width.py` | Display width | `display_width()`, `BoxChars`, `OutputMode`, `is_accessible()` |
| `wisp/tui/task_owner.py` | TUI fire-and-forget ownership | `OwnedTasks`: named spawn, exception logging via done-callbacks, `cancel_all()` on unmount — no bare `create_task` in screens (structural pin enforces) |
| `wisp/contracts/` | Versioned wire envelopes (M1) | `CanonicalEvent`, `ToolRequest`/`ToolResult` (**now produced** by `core/proposal.py`), `PolicyDecisionEnvelope` (**⚠️ still unwired**), `RunStatus`/`Transition`, manifest schemas, flat↔nested `adapters` |
| `wisp/auth/` | Local authority layer (M2) | `Principal` + narrowing derivation, layered `authorize()`, `WorkspaceTrust`, secret `redact()`/`scan_for_secrets()`, extension consent/quarantine. Verdicts are recorded for **allow and deny** via `ToolExecutor._audit_authorization` (P2) — the layer lands in `reason` and in a structured `args_summary` envelope |
| `wisp/runs/` | Durable runtime (M3) | `RunRecord` state machine, `RunStore` ABC + `SQLiteRunStore`, `Scheduler` (admission/leases/idempotency), compensation records, `ReproManifest`. **Now wired** at the composition root (`CompositionRoot.run_store`) into both `BackgroundAgentManager` sites (P0); foreground-turn lifecycle still deferred to P1 follow-up |
| `wisp/policy/` | Governance bundles (M4) | Ed25519 `PolicyBundle`, narrow-only precedence merge, managed/disconnected loader, `explain`/`dry_run`, admin CLI, control-plane routes. **⚠️ Not wired to the runtime** — no entry point loads a bundle, so `ToolExecutor.policy` is always `None`; see `PHASE_10_M4_GOVERNANCE_UNWIRED.md` |
| `wisp/trace/` + `wisp/eval/` | Evidence + evaluation (M5) | `Span` store (redaction at append), evidence export, replay plans, tier-gated OTLP, eval scenarios + safety/latency/cost metrics. **Now wired** — `CompositionRoot.trace_store` emits a `turn` span per turn (P0) |
| `wisp/task/` | CLI workflow (M6) | `TaskManager` lifecycle, plan review render + scope approval, 5 profiles, task CLI with `--json` contract |
| `wisp/release/` | Supply chain + support (M7) | Dep lock verify, CycloneDX SBOM, license audit, health checks, redacted diagnostics, release CLI |

## Durable-record flags

The durable record (run registry, span sink, session journal, proposal boundary) is gated by independent
flags — one per concern (ADR-0002), so each is independently rollable. Each is read with
`getattr(config, name, True)` — the `thin_tools` convention — so `SimpleNamespace` test doubles keep
working. **The table below is the list; do not quote its size in prose**, because a count in a sentence
goes stale on the next flag while the table does not.

| Flag | Env var | Gates |
|---|---|---|
| `durable_runs` | `WISP_DURABLE_RUNS` | `SQLiteRunStore` construction + `BackgroundAgentManager` persistence |
| `session_event_fidelity` | `WISP_SESSION_EVENT_FIDELITY` | transcript journal (`assistant_message` / `tool_call` / `tool_result`) |
| `turn_journal` | `WISP_TURN_JOURNAL` | incremental journaling of each exchange as it closes |
| `turn_spans` | `WISP_TURN_SPANS` | turn + tool-call span emission |
| `proposal_boundary` | `WISP_PROPOSAL_BOUNDARY` | `PROPOSAL` / `OUTCOME` records |
| `record_verdict` | `WISP_RECORD_VERDICT` | acceptance verdict (`VERDICT` event). **Defaults `false`** — unlike the others it adds a record to every existing caller's log |
| `task_graph` | `WISP_TASK_GRAPH` | materialized task graph (`TASK_GRAPH` + `NODE_TRANSITION` events). **Defaults `false`** — the message list remains authoritative |
| `recovery_ladder` | `WISP_RECOVERY_LADDER` | the recovery consumer at the turn boundary (`RECOVERY`, plus `ESCALATION` when exhausted). **Defaults `false`** |
| `goal_state` | `WISP_GOAL_STATE` | the derived goal state (`GOAL_STATE` record). **Defaults `false`** — records only; nothing acts on it |
| `stagnation_gate` | `WISP_STAGNATION_GATE` | **enforcement**: lets M13 withhold `done` for a bounded replan. **Defaults `false`** — observation and recording are unaffected, so it is a *separate* concern from `graph_oscillation_guard`, which disables the detector itself |
| `strict_derivation` | `WISP_CRITERIA_STRICT_DERIVATION` | **ADR-0048 R5** — lets the acceptance-criteria derivation decline to complete an objective whose requirement it could not determine. **Defaults `false`**, i.e. today's behaviour: the derivation's reasoning is journalled and acted on by nothing. Read at the composition point (`wisp/autonomous.py`), not inside the pure function. **Independent of `structured_declaration`** (ADR-0056 R1) — it withholds on the prose path with that flag OFF, and making it conditional would silently disable ADR-0048's fix for the measured false `GOAL_MET` |
| `structured_declaration` | `WISP_CRITERIA_STRUCTURED_DECLARATION` | **ADR-0050 R8** — lets an objective carry a `--- criteria ---` block that *states* its acceptance conditions. **Defaults `false`**, i.e. no declaration is parsed and every caller keeps ADR-0048's behaviour. ON, a malformed or unmeasurable declaration **raises** `CriteriaDeclarationRejected` and the run stops — it never falls back to the prose grammar. Read once, at the same composition point. **A valid declaration pre-empts `strict_derivation`** (ADR-0056 R2): the derivation returns early, so `strict` is *recorded and inert* — there is nothing to withhold when the objective has said. `use_declaration=True` with **no** block is a no-op |
| `turn_criteria_source` | `WISP_TURN_CRITERIA_SOURCE` | **ADR-0053 R7** — lets the **turn path's** required-criteria set carry the objective's declared criteria, unioned with `floor_guard_criteria(guard)`. **Defaults `false`**: with it off the verdict site is `floor_guard_verdict(guard)` unchanged, and the set is exactly `['floor:verification']`. ON, a declaration at the head of the prompt adds its criteria and the declaration's own probe evidence (`CommandProbe`, bounded by `spec.timeout_s`), so the verdict can be `FAIL` for a reason the floor guard does not enforce. **Deliberately independent of `structured_declaration`** — that flag gates the objective-level derivation; coupling them would put two read sites on one concern (ADR-0002). Read once, at `AgentRuntime.run_turn`'s entry |
| `acceptance_gate` | `WISP_ACCEPTANCE_GATE` | **ADR-0054 R6** — the **acceptance gate**: the engine's pre-`done` gate asks a read-only callable (`turn_criteria.DeclaredCriteriaGate`) and withholds `done` by ADR-0036's bounded delay-not-veto model when the objective's declared criteria are not satisfied. **Defaults `false`**, and the reason is measured: ADR-0051 R4 requires **≥ 2 capable models** and this environment serves exactly **1** of 13 (`scripts/acceptance_gate_population.py`). **Dependent on `turn_criteria_source`** — with the source off there are no declared criteria, so the gate would withhold on a verdict the record does not carry. Read once, at `AgentRuntime.run_turn`'s entry |
| `rest_approval` | `WISP_REST_APPROVAL` | **ADR-0057 R11** — a REST request for an **executable-config** action (`hooks.create`, `mcp.add_server`, `plugins.install`) asks a human over the WebSocket channel. **Defaults `false`**: with it off `require_rest_approval` returns immediately and every caller sees today's code. ON, those actions in `auto_edit`/`ask_all` send a `tool_approval_request` frame (the vocabulary **all three clients already read** — ADR-0061 corrected ADR-0057's "both": the VS Code extension reads it too) and wait, bounded by `REST_APPROVAL_TIMEOUT_S` (30 s). **With no client connected the request is DENIED** — it must not hang and must not silently allow. `full` does not ask; `read_only` denies outright. Independent of every other flag (ADR-0002) |

Three rules that are easy to get wrong:

- **Read a flag at its consumption site** — ADR-0002's rule, restated by ADR-0062 R4: `getattr(config,
  name, <safe default>)`, resolvable via `get_setting`. *"Read once, at the composition point"* is **not**
  the rule (ADR-0062 R4 names it as a defect). `AgentRuntime.run_turn` does read these at one site, because the stream loop
  and the `finally` block must agree; a flag read in two places is a flag that can disagree with itself.
- **`PROPOSAL` / `OUTCOME` / `VERDICT` / `TASK_GRAPH` / `NODE_TRANSITION` are AUDIT-ONLY.** None may
  append to `Session.messages`. The transcript is rebuilt from
  `ASSISTANT_MESSAGE(tool_calls=…)` + `TOOL_RESULT`; a second path in duplicates every tool reply on
  replay.
- **Durable writes are best-effort.** A turn that ran correctly must never be reported as failed
  because a journal or span write failed. The loss is visible as a gap in the session's sequence
  numbers. See `WISP_ARCHITECTURE_DECISIONS.md` ADR-0004.

### Reading a session: journal-first, with two fallbacks

`SessionRepository.reconstruct(sid)` is the journal-first reader, shape-compatible with
`UnifiedStore.load_session` (which five consumers still use — a tripwire asserts it). It falls back to the
blob in **two** cases, and both matter:

1. **No turn body in the journal** — every pre-P0 session. Replaying one yields `[{role: "user"}]`, which
   is a *non-empty* list, so the obvious check `if replayed.messages:` truncates the session to one
   message.
2. **A gap in the sequence** (`Session.gap_detected`) — ADR-0004 permits a durable write to fail
   silently, and a lost `TOOL_RESULT` yields an assistant `tool_calls` block with **no reply**, i.e. a
   provider-invalid transcript. ADR-0027.

`reconstruction_source(sid)` returns `journal` | `blob` | `none` — use it to see which path answered.
`reconstruct()` also reports `_source`, `_gap`, `_journal` and `_journal_records_at_risk`.

**`_journal` is populated on BOTH paths.** Which *transcript* to trust and which *records* survived are
independent questions, and the fallback used to answer only the first — silently discarding a surviving
escalation because the blob has no `escalation` key. `_journal` carries the journal-only records
(`JOURNAL_ONLY_RECORDS`) on either path; `_journal_records_at_risk` names what a gap endangers.
`Session.journal_records()` is the accessor; `Session.has_escalation` is the named question.

**Replay must reproduce the live transcript exactly.** `runtime.py` states this at the construction
site, and it is now true and asserted: `Session.apply` produces the same tool-reply shape as
`_exchange_parts` — `{role, tool_call_id, content}` and nothing else. It used to add a `name` key the
live path never sets, which made the journal and the blob disagree on the same session and made
`context_pruner` branch on the difference (ADR-0029, F25). If you add a key to a replayed message,
add it to the live path too — or assert the invariant and watch it fail.

### The recovery ladder is not consulted by the turn loop

`wisp/core/recovery.py` is complete and tested, but the live turn path's recovery behaviour is
**unchanged** — nothing on it calls the ladder. Do not assume failures are being classified or that
recovery is budgeted in production; it is not yet (item M12).

### A failure is classified through one adapter, and a refusal is never retried

`classify_failure_signal(message, recoverable, code)` (`core/recovery.py`) bridges the runtime's `error`
event to the P6 taxonomy. Precedence: **refusal → cancellation → error code → transport markers →
`recoverable` → `IMPLEMENTATION`**. The default is `IMPLEMENTATION`, deliberately not `SECURITY` —
`SECURITY`'s only legal rung is escalation, so defaulting to it would escalate every novel failure.

**The engine's refusals are denials, and the predicate knows it.** The engine emits pre-dispatch refusals
as `Blocked: …`; `_ENGINE_DENIAL_PREFIXES` (a **prefix** match, so *"not blocked:"* is not a refusal)
covers them. Before M12 none matched, so the orchestrator's retry loop — which says *"Don't retry
authorization denials"* — **retried them**. `CODE_FAILURE_CLASS` is total by test, so a new error code
cannot silently take the default.

**Watch the name:** `OutcomeClass.TIMEOUT` is reachable only from `APPROVAL_TIMEOUT`, a *denial* status —
it does **not** mean a turn timeout. `CODE_TURN_TIMEOUT` is `ENVIRONMENT`, not `TRANSIENT`. ADR-0032.

### Every prompt section is classified, and T1 holds

`SECTION_TRUST` in `wisp/context_assembler.py` is the **one table** saying which trust tag each system-
prompt section carries; `INSTRUCTION_PRIORITY = 0` names the tiers that carry instructions.

**Classify conservatively: if a section's content can originate in the workspace, it is `REPOSITORY`.**
That is why `git_context` is untrusted — a commit message is text an author wrote and it reaches the
prompt — and why `memory_block` is, since memory is workspace-scoped.

**Adding a section means adding a tag.** `test_every_appended_section_is_classified` reads the
`sections.append((...))` calls by AST and fails on a name missing from the table. That is not
bureaucracy: classifying the sections found a **live T1 violation** — `context_files` (the content of
`CLAUDE.md` / `.wisp/rules.md`) sat at priority −1, *ahead of* the system prompt, unfenced. A repository
whose `CLAUDE.md` carried an instruction put it before the rules that forbid it. ADR-0031.

`untrusted_sections_in_instruction_position(sections)` is T1 as a predicate, and it **fails closed**: an
unclassified section counts as untrusted. **T2 fencing is not done** — untrusted sections are positioned
correctly but not delimited.

### A subagent authorizes as a narrowed child, and the identity travels with the call

`SubagentRunner._child_principal()` derives a child principal from the parent's own identity (via
`auth.principal.executor_principal`, the shared rule) and stamps it into the child session; the core
forwards it to `ToolExecutor.execute(..., principal=…)`.

**Do not build a `ToolExecutor` per child.** Its constructor creates two `ThreadPoolExecutor`s whose
shutdown the composition root owns, so a per-child executor leaks two pools per subagent — and `fanout`
spawns many. That is why the principal is a per-call argument, and
`test_the_runner_does_not_construct_a_tool_executor` is the ratchet.

**The ordering matters when testing it.** The policy gate asks "is this tool permitted in this mode?"
and runs *first*, naming no controlling layer (F15); `authorize()` L1 asks "does this principal have this
capability?" and runs second. So a child inherits the parent's **mode** but not the parent's
**contract** — probe with a tool the mode permits and the contract excludes (`write_file`), not one the
mode already denies (`run_bash`). ADR-0030.

### The graph is a shape, not a payload

`wisp/core/task_graph.py` records *structure* — node identity, status, readiness, edges. It carries **no**
tool name, arguments, result or assistant text, and it must not: copying the transcript into the nodes
would create a second copy that can disagree with the first, which is the defect class this migration
exists to remove. The transcript projects from the **journal**; the graph contributes status.

The guard is **field classification, not a name blacklist**: every `TaskNode` field has a declared kind
in `NODE_FIELD_KINDS` (`STRUCTURAL` / `REFERENCE` / `PAYLOAD`), `PAYLOAD` has no member, and
`node_field_violations()` fails the suite on an unclassified, stale or payload field — so a payload
field cannot slip in by being named something else. ADR-0033. A `REFERENCE` field is an opaque handle
that carries no content; `work_unit` is one.

### A node references its work unit

The nodes were `turn:0 … turn:n-1`, generated from a **count** of closed exchanges, so nothing
connected a node to the work it recorded — the real precondition M9 found for M11. Now every node
carries `work_unit`: `call:<protocol id>` for a closed tool exchange (the same id the transcript and
the journal use), `output` for the terminal node. `node_id` stays `turn:i` — it is the graph's
*structural* key, and the identity is a separate field on purpose. `build_turn_graph` takes the work
units, not a count, so a node that records nothing is not constructible. ADR-0033.

The identity is **not recomputed**: `_serialize_tool_exchanges` returns `(events, exchange_call_ids)`
from the blocks `_exchange_parts` minted. Id-less exchanges get a fresh `uuid4` there, so a second pass
would name a work unit the transcript never recorded.

### An empty observation is not evidence of stagnation

`ProgressSignal` distinguishes *"we observed no progress"* from *"we observed nothing"* via `is_empty`.
`observe()` refuses an empty observation — it is not appended, not counted flat, and not fed to the trap.
This is not defensive tidiness: `from_verdict_and_graph` reads two **opt-in** records (`record_verdict`,
`task_graph`) that both default off, so its signal is empty on every turn of a default configuration, and
treating that as flat declared every multi-turn session stagnating (F32).

The live path therefore builds its own signal with `with_work()` from state that is **always present**:
the action identity (`action_key(tool, args)` — **not** `TaskNode.work_unit`, which is a per-call id that
would make every repeat look like new work) and the outcome hash. A refused call emits no `tool_call`
event, so its arguments are carried on the refusal by `_refusal_result_event`. ADR-0034.

The detector **records; it does not act**: routing to the recovery ladder and gating completion on
`may_report_goal_met()` are both deferred and pinned by tripwires in
`tests/test_stagnation_live_wiring.py`.

### A state-bearing record is not best-effort

`STATE_BEARING_EVENT_TYPES` (currently `{ESCALATION}`) names the records whose loss is a **correctness**
precondition rather than an observability one. `AgentRuntime._journal_turn_events` swallows a failed
write for everything else — ADR-0004's rule, because the loss shows up as a gap — but **re-raises** when a
batch contains one. The escalation *is* the parked run's state; continuing as though the write landed is
a false record, not a lost observation. ADR-0028.

If you add a kind to that set, add an ADR first. The set is pinned by a test parametrized over every other
event kind, so widening it fails the suite rather than passing quietly.

### The graph does not drive execution — and that is now a DECISION, not a gap

`wisp/core/task_graph.py` can create, expand, invalidate and supersede nodes — but **nothing on the live
turn path calls those functions**. The turn loop executes tools directly, and the graph is a *record* of
that work, not its driver. M11 landed the **precondition** (node identity, ADR-0033); **ADR-0060 decided
the second half**: Layer A is the driver and Layer B is a record, **permanently**. *"The graph drives
execution"* is rejected as a target, not deferred — `wisp.graph.types.Graph` is `frozen=True`,
`GraphExecutor` has no mid-run growth API, `run()` refuses a graph that is not complete up front, and no
`TaskGraph → Graph` lowering exists, while a turn's node set is produced by the model *during* the turn.
`test_the_graph_still_does_not_drive_execution` is the **contract**, with its reversal condition stated
in the test; the wider property is in `tests/reliability/test_layer_b_boundary.py`. Do not assume the
graph is authoritative — it is not, and that is the decision.

### Stage 3a of the verification gate does NOT gate

`core/acceptance.py` computes a `CompletionVerdict` and the runtime **records** it. Nothing on the
completion path consumes it: `turn_succeeded` still derives from terminal evidence alone, and
`VerificationFloorGuard` still owns the completion invariant. Enabling the gate is **stage 3b**, a
separate decision taken on a measured `INCONCLUSIVE` rate — the plan rates P3 the highest-risk phase in
the migration precisely because it changes completion semantics. ADR-0016.

### The completion gate is two gates, and only stagnation is bounded

`stateless.py`'s pre-`done` gate consults two independent authorities, **in this order**:

1. **`VerificationFloorGuard.rejection()`** — the verification floor. Unchanged, and consulted FIRST, so a
   verification rejection keeps its exact behaviour and the turn is never double-nudged.
2. **`completion_gate`** (ADR-0036) — a **read-only** predicate the runtime passes, closing over M13's
   per-turn `StagnationDetector`. When it returns `False`, the engine appends a replan nudge and loops
   again — for at most `_MAX_STAGNATION_INTERVENTIONS` (2) times, and never on the last iteration.

**The engine never receives the detector** — only the predicate; `observe()` mutates, so handing the object
over would give a second party write access to the one stagnation authority. The engine imports exactly one
name from `stagnation.py` (`compose_replan_nudge`), lazily, inside the withhold branch. AST ratchets in
`tests/reliability/test_post_m13_completion_enforcement.py` pin all of that.

**Bounded, so it is a delay and not a veto.** After the budget is spent the gate falls through to `done`, so
`turn_succeeded` stays `True` and the goal state is `GOAL_STAGNATED` — ADR-0035 line 1478's coexistence,
holding in the field. Withholding on the last iteration is forbidden because the loop would end, the budget
wrap-up would run, and the honest surrender would become a fatal `CODE_ITERATION_BUDGET`.

**It cannot change the goal state — the latch is monotonic (ADR-0037).** `trap_fired` is append-only, so
once a repeat closes `may_report_goal_met()` it never reopens inside the detector's lifetime; the only exit
is the turn boundary, where `run_turn` builds a fresh detector. Later genuine progress resets
`consecutive_flat` and still does not reopen it, so the interventions cannot convert a `GOAL_STAGNATED` into
a `GOAL_MET`. `min_consecutive` gates the **verdict** only, not the predicate — whose effective threshold is
**1**. Read ADR-0037 and `PHASE_POST_M13_COMPLETION_ENFORCEMENT_IMPLEMENTATION.md` §15.1 before relying on
enforcement.

### The acceptance criteria are host-derived, and silence is not consent

ADR-0048. `derive_acceptance` answers *"does this objective require a green suite?"* from the objective's
own prose, and that answer is the sole gate on `GOAL_MET` (ADR-0047 R5). Use
**`explain_acceptance(goal, workspace, *, baseline=None, strict=False)`** when the *reasoning* matters: it
returns a `CriteriaDerivation` carrying, per command spec, one of three outcomes and the objective's own
words that drove it.

| Outcome | Condition | Consequence |
|---|---|---|
| `STATED` | the objective states the requirement | the absolute criterion is promoted to required |
| `UNSTATED` | silent, but the objective states *something else* checkable (a `SymbolSpec`) | guards-only — an honest no-regression objective |
| `UNDETERMINED` | silent, and nothing else checkable is named | the host has no basis; it neither promotes nor silently degrades |

- **`derive_acceptance`'s signature is frozen** (ADR-0009). It is a thin caller of `explain_acceptance`
  with `strict=False`, so its ~40 `criteria_for` call sites and three callers are unaffected. Call
  `explain_acceptance` directly to reach the record.
- **Silence is never consent.** An `UNDETERMINED` spec is recorded, and under
  `WISP_CRITERIA_STRICT_DERIVATION` it also contributes a required, **unevidenceable** criterion that
  `evaluate`'s rule 3 turns into `INCONCLUSIVE` — never a promotion (that would invent a requirement the
  user did not state) and never a `FAIL` (that would assert a failure the evidence does not support).
- **Strict mode withholds only where the derivation actually chose** — `UNDETERMINED` **and** an
  *advisory* absolute criterion. On a green baseline the criterion is required anyway, so nothing is
  withheld and the flag cannot break an objective it has no business touching.
- **The derivation is journalled** once, beside the baseline, as a `{"kind": "derivation"}` line. It is
  read by nothing on the decision path; its purpose is that the host's answer stops being invisible.
- **Negation is not handled (R7).** *"Do not make the tests pass"* matches the grammar. The recorded span
  is how a reader sees it; the grammar was not widened again, because widening it is the mitigation
  ADR-0048 measures as insufficient.
- **A measured false `GOAL_MET` is still reachable with the flag off**: the repo's own benchmark task
  `FIX_BUG` ("Fix the bug in totals.py") has no suite word to match, so on a red baseline a no-op
  satisfies the guards. `tests/reliability/test_criteria_derivation_authority.py` pins it as a defect, and
  that class goes red when the derivation is fixed.

### An objective may declare its own criteria — and a rejected declaration stops the run

ADR-0050. `explain_acceptance(..., use_declaration=True)` consults a **declared block** before inferring
anything, and a valid declaration yields reason `DECLARED` with **the prose grammar not consulted**:

```text
--- criteria ---
command_succeeds: python -m pytest tests/ -q
symbol_defined: app.py::parse_duration
# comments and blank lines are allowed
--- /criteria ---
Fix the bug in app.py.
```

- **The block must be at the HEAD** of the objective. A block quoted mid-prose is not a declaration.
- **The grammar is closed and hand-rolled** — exactly two kinds, `command_succeeds` and `symbol_defined`.
  An unknown kind and a malformed line are **rejected**, not skipped. `pyyaml` is declared and available;
  it is deliberately *not* used, because a parser that accepts more than the grammar defines accepts
  shapes the host must then interpret.
- **The host validates runnability, never outcome**: `argv[0]` must resolve (workspace-relative executable
  or on `PATH`); `<path>::<symbol>` must be inside the workspace, exist, and be an identifier.
- **Rejection is loud and there is no fallback.** `CriteriaDeclarationRejected` is journalled
  (`{"kind": "declaration_rejected"}`) and raised. Falling back would measure something other than what
  the caller declared while appearing to measure it — which ADR-0048 R6 states *is* MODE A.
- **The model gains no channel, structurally.** The objective is supplied by the **caller**; the host
  validates; the harness measures. The model already sees the resulting criteria (ADR-0045 R13), so
  nothing new is exposed.
- **What it fixes, and what it does not:** MODE A and MODE B both close **on the declared path only**. An
  objective that declares nothing keeps today's behaviour and today's defects. The declaration does not
  make the classifier better; it makes the *objective* complete.
- **`derive_acceptance` never sees a declaration** — its signature is frozen (ADR-0009) and it calls
  `explain_acceptance(..., use_declaration=False)`.

### The turn path's criteria set is the floor's criterion plus the declaration's (ADR-0053)

`wisp/core/turn_criteria.py` is the **only** place the turn's criteria set is built. With
`turn_criteria_source` off it returns exactly `floor_only(guard)` — today's behaviour, byte-for-byte.
With it on and a declaration at the head of the prompt, the declared criteria and the declaration's own
probe evidence join the set.

**The gate's condition is `verdict_keys_on_declared(verdict)`** — a `FAIL` whose every named criterion is
a non-floor one. That is *not* `guard.rejection()` under another name, and the difference is drivable: a
mutation-verified turn satisfies the floor guard completely (floor-only: `PASS`/`GOAL_MET`) while a
declared `symbol_defined` criterion fails (declared: `FAIL`/`GOAL_FAILED`). A `FAIL` the floor guard
already enforces returns `False` from that predicate, so a gate keyed on it cannot duplicate the floor
guard.

**The failure routing is `derive_goal_state`, not the `done` gate — until ADR-0054 turns the gate on.**
The verdict is computed *after* the engine has emitted `done`, so the turn-level withholding gate
(ADR-0036) cannot act on it; a declared failure lands `GOAL_FAILED` (row 3) where a floor-only verdict
landed `GOAL_UNVERIFIED` or `GOAL_MET`. The ladder is unchanged.

**ADR-0054 adds the withholding, where it can act.** `acceptance_gate` (default OFF, dependent on
`turn_criteria_source`) makes the engine ask `turn_criteria.DeclaredCriteriaGate` — a **read-only
callable**, so the engine receives no criteria, no specs and no probe — at its pre-`done` gate. The probe
is taken **there**, because that is the moment the workspace is final, and it is **cached** so the verdict
site reuses it: one probe per declared turn. Withholding reuses ADR-0036's bound and **shares the turn's
extension budget**, then surrenders honestly, so the withheld turn still ends with `done` and
`turn_succeeded` stays a projection of terminal evidence. The gate is an **extension, not a success
signal**.

**A malformed declaration at the head of the prompt raises out of `run_turn`** (ADR-0050 R4 — loud, never
a fallback). A block that is not at the head is not a declaration at all, so it never reaches that path.

## Common patterns

### Adding a tool
1. Add schema dict to `TOOL_SCHEMAS` in `wisp/tools/registry.py`
2. Add implementation function to `TOOL_IMPLS`
3. Update `DEFAULT_SYSTEM` prompt if needed

### Adding a transport
1. Extend `Transport` ABC from `wisp/transport/base.py`
2. Implement `send()`, `recv()`, `approve()`, `start()`, `stop()`
3. Register in `wisp/transport/__init__.py`

### Adding CLI rendering
1. Add pure function to `wisp/transport/renderer.py` (mode-aware, testable)
2. Call from `CLITransport._render_event()` in `cli.py`
3. Follow existing patterns: use `BoxChars`, `display_width()`, `dim()`/`success()`/`error()`

### Adding a slash command
1. Register in `Dispatcher._register_builtins()` in `wisp/cli/dispatcher.py` via `@self.register("name", "desc", usage="/name")`
2. Handler signature `(ctx: ReplContext, args: str) -> CommandResult`; read workspace via dict-or-object config pattern (see `_rewind`); never touch transport privates
3. For terminal takeovers (monitor), suspend spinner + park typeahead + buffer bg writes, restore in `finally` (see `open_subagent_monitor()`)

## Testing

```bash
# Transport + UX tests (fast, no I/O)
pytest tests/test_progress.py tests/test_spinner.py tests/test_renderer.py tests/test_transport_cli.py -v

# Transport integration tests
pytest tests/test_websocket.py tests/test_transport_headless.py -v

# Core + runtime tests
pytest tests/test_core_stateless.py tests/test_runtime_concurrent.py tests/test_provider_integration.py -v

# Full suite (~357 test files, ~5,400 test functions — foreign-session WIP files excluded below)
python -m pytest tests/test_*.py -v

# CLI surface E2E (hermetic HOME + mock provider, PTY repl/tui, 7+ groups)
./scripts/audit_cli_surface.sh

# Full suite minus known-foreign WIP (uncollectable until coordinated)
python -m pytest tests/ -q --ignore=tests/test_auto_delegate_defense.py \
  --ignore=tests/test_delegation_research_only.py \
  --ignore=tests/test_input_and_interrupts.py \
  --ignore=tests/test_subagent_enterprise.py \
  --ignore=tests/e2e_live_background.py --ignore=tests/e2e_live_no_autodelegate.py \
  --ignore=tests/manual_test_repl_skill_ack.py --ignore=tests/smoke_repl_multi_turn.py

# Enterprise track gate (contracts, auth, runs, policy, trace, eval, task, release)
python3 -m pytest tests/test_contracts_*.py tests/test_auth_*.py tests/test_runs_*.py \
  tests/test_policy_*.py tests/test_trace_*.py tests/test_eval_*.py \
  tests/test_task_*.py tests/test_release_*.py tests/test_no_bypass.py -q

# Durable record + proposal boundary + verdicts + task graph
# (migration P0-P9 + M2/M3/M4/M16/M9/M15/M14/M12/M11/M13 + POST-M13 + ADR-0035/0036/0037
#  + the NEXT chain ADR-0045/0046/0047/0048)
# 1505 tests — 1504 pass, 1 fails (F38: a test that encoded the pre-F8 exchange ordering).
#
# ⚠️ THE COUNT IS NOT MEASURABLE ON THIS HOST (2026-09-25) — do not quote one (F111, F94).
# Re-measured in every one of the corpus-governance mission's four changes, as F85 requires,
# and it does not reproduce: the block reports 154 spurious failures WITH and WITHOUT that
# mission's new guard (154 failed / 1379 passed vs 154 failed / 1351 passed), while the three
# failing files pass in isolation (114 passed in 2.6 s). Free memory measured 60-87 MB of
# 16 GB; the block's wall time tripled (118 s -> 360 s). F36 describes the milder half: it
# says the kernel KILLS the run (exit=137); here the run COMPLETES and reports a wrong
# answer, which is the instrument-defect class one level up. "Never quote a count from prose
# — run the block" is necessary but not sufficient: never quote one from a block run on a
# starved host. A count's scope includes the host's free memory (F94). See CONTEXT.md §11.
#
# Also: two CONCURRENT pytest processes race on the shared pytest-of-<user> temp directory
# and produce "PermissionError: EEXIST: mkdir ..." at fixture setup — 869 errors from one
# overlapping run, which reads as a code failure and is not (F112). RUN THE BLOCK ALONE, with a
# private --basetemp — ADR-0062 R6 makes both a rule.
# The block below was extended with the four NEXT-mission files, the five
# documentation-authority / criteria-authority / F8-classification / precedence /
# structured-criteria files, the four 2026-09-25-mission files (gate-enablement,
# dag-retirement, F8-published-status, criteria-source), the two later
# 2026-09-25 files (acceptance-gate-enablement, objective-flag-composition), and the
# outcome-classification pair (the taxonomy guard + its delegation guard, added by the
# outcome-classification mission so the guard that was RED for four phases is now in a
# block that actually runs), and the M4 pair (the wiring guard, which was in NO running
# block until F93, and the REST-composition guard, ADR-0059), and the Layer B boundary
# guard (ADR-0060); the earlier "849 tests" figure was the pre-NEXT count.
# NEVER quote a count from prose — run the block. (F85: measure it after the LAST change
# to any member, not after the change that motivated measuring.)
python3 -m pytest tests/test_durable_layer_reachable.py tests/test_turn_journal_incremental.py \
  tests/test_action_idempotency_key.py tests/test_proposal_boundary_records.py \
  tests/test_proposal_boundary_no_bypass.py tests/test_verdict_layer_recorded.py \
  tests/test_gate_order_corpus.py tests/test_acceptance_verdict.py \
  tests/test_task_graph_materialization.py tests/test_graph_mutation.py \
  tests/test_recovery_ladder.py tests/test_stagnation_detection.py \
  tests/test_context_trust.py tests/test_structured_delegation.py \
  tests/test_session_reconstruction.py tests/test_durability_preconditions.py \
  tests/test_escalation_durability.py tests/test_execution_view_projection.py \
  tests/test_child_principal_wired.py tests/test_prompt_section_trust.py \
  tests/test_failure_signal_classification.py tests/test_node_identity.py \
  tests/test_stagnation_live_wiring.py tests/reliability/test_killpoints.py \
  tests/reliability/test_post_m13_authority_implementation.py \
  tests/reliability/test_post_m13_completion_enforcement.py \
  tests/reliability/test_post_m13_stagnation_gate_validation.py \
  tests/reliability/test_f8_tool_execution_restored.py \
  tests/reliability/test_verification_evidence_adapter.py \
  tests/reliability/test_next_convergence_controller.py \
  tests/reliability/test_next_autonomous_wiring.py \
  tests/reliability/test_progress_aware_recovery.py \
  tests/reliability/test_multi_turn_productive_recovery.py \
  tests/reliability/test_current_authorities_pins.py \
  tests/reliability/test_criteria_derivation_authority.py \
  tests/reliability/test_f8_error_classification.py \
  tests/reliability/test_precedence_canonical.py \
  tests/reliability/test_structured_criteria.py \
  tests/reliability/test_gate_enablement_contract.py \
  tests/reliability/test_dag_retirement_contract.py \
  tests/reliability/test_layer_b_boundary.py \
  tests/reliability/test_layer_c_disposition.py \
  tests/reliability/test_external_input_path.py \
  tests/reliability/test_current_findings_pins.py \
  tests/reliability/test_current_open_items_pins.py \
  tests/reliability/test_current_flags_pins.py \
  tests/reliability/test_derived_registers_entry_point.py \
  tests/reliability/test_f8_published_status.py \
  tests/reliability/test_criteria_source_on_turn_path.py \
  tests/reliability/test_acceptance_gate_enablement.py \
  tests/reliability/test_objective_flag_composition.py \
  tests/reliability/test_rest_approval.py \
  tests/test_outcome_classification_authority.py \
  tests/reliability/test_outcome_classification_delegation.py \
  tests/reliability/test_key_trust_workflow.py \
  tests/reliability/test_m4_policy_wiring.py \
  tests/reliability/test_rest_authorization_composition.py \
  tests/test_m4_governance_wiring.py \
  tests/reliability/test_replay_verification.py \
  tests/reliability/test_context_protected.py \
  tests/reliability/test_run_bounds.py \
  tests/reliability/test_idempotency.py \
  tests/reliability/test_injection_scan.py \
  tests/reliability/test_redaction_point.py \
  tests/reliability/test_clock_injection.py \
  tests/reliability/test_cost_meter.py \
  tests/reliability/test_cost_gate.py -q --basetemp="$TMPDIR/wisp-block-$$"   # alone — ADR-0062 R6
```

### The environment will fight you

Always `env -u PYTHONPATH` — the WorkBuddy `sitecustomize.py` shim blocks pytest's temp `mkdir` and
produces **false** failures.

**Installing a declared dependency.** `uv` is **not on `PATH`** (it lives at `~/.local/bin/uv`), and
`~/.cache/uv` already holds unpacked wheels — so prefer offline:

```bash
env -u PYTHONPATH UV_OFFLINE=1 ~/.local/bin/uv pip install \
    --python .venv/bin/python --offline '<pkg>==<locked-version>'
```

Dry-run with `--dry-run` first and check every resolved version against `uv.lock`. This edits neither
`uv.lock` nor `pyproject.toml`. Two traps: the interpreter has **no CA path**
(`ssl.get_default_verify_paths()` → `cafile: None`), so `pip`/`urllib` fail TLS even though the machine has
egress — `SSL_CERT_FILE=<certifi>/cacert.pem` fixes it; and **`.venv/bin/pip` has a broken pre-move
shebang**, so use `python -m pip` or `uv`.

**The full suite cannot run in one process here** (F36 — the kernel kills it). Chunk it, union the results,
and say the method was weaker than a two-run intersection. `baseline-failures-stable.txt` is **stale**: it
was measured with `jsonschema` absent, so it conflated that outage with everything else.

### Reachability is mandatory for new durable code

The Phase 0 audit found **eight** complete, tested, unreachable subsystems. Every path this migration
wires ships a test that drives a **production entry point** (`CompositionRoot`, `AgentRuntime.run_turn`,
`BackgroundAgentManager.launch`) and asserts the durable artifact appears — not merely a unit test of
the component. `test_proposal_boundary_no_bypass.py` extends this to a structural invariant: the
authority consumers, the consult/record arity of `ToolExecutor.execute`, and the reachability of each
new call edge are pinned by AST analysis.

### RED-first for anything that touches the gate chain

`tests/test_gate_order_corpus.py` fingerprints each case by its **structured** outcome (the
machine-readable `status` plus the deciding layer parsed from the structured `reason`), never by prose.
Write it, make it green against the *unmodified* implementation, then change the gates. A corpus
written afterwards records the change rather than the baseline. See ADR-0014.

### Baseline comparison: do not use `git stash`

The tree carries pre-existing uncommitted work. Stashing only the files you touched reverts them to
**HEAD**, discarding that work — which makes unrelated ratchet failures look like your regressions.
**Copy the files you are about to edit somewhere outside the repo first**, then compare against those
copies. See `WISP_MIGRATION_STATUS.md` finding F12.

### The instrument is not exempt from the discipline

**A probe with a defect is invisible, because the probe is what catches the other defects.** Six recorded
instances, every one found by accident: **F41** (a test double that raised from `post()`, hiding that the
4xx body log had never worked), **F54** (every test built its core through `CompositionRoot`, so the
hand-built factory was never exercised and `wisp bench` refused every mutation), the `.pyc` purge
(`PHASE_STRUCTURED_CRITERIA.md` §5 — stale bytecode reported the harness's defect as the subject's),
**P5**/**Q1** (`PHASE_PRECEDENCE_CORRECTION.md` §5, §6 — a helper that returned the wrong section; a
regex that skipped an emptied finding), `PHASE_DAG_RETIREMENT.md` §7.1 (a string scan over a Python tree
that read its own docstring as a caller), and `PHASE_CRITERIA_SOURCE.md` §6 (the pin guard checks a
pinned line is *non-blank*, so it caught 1 of 5 stale pins).

**The class:** *the instrument does not reproduce the production control flow, or does not fail when the
subject fails, and reports its own defect as a result about the subject.*

- **A probe that does not falsify is a finding, not a pass.** Investigate the flake; do not retry it. If
  breaking the thing under test leaves the suite green, the test is not testing what it claims.
- **A check over a collection needs a floor.** Assert the collection is non-empty *before* iterating it,
  or the check passes by finding nothing.
- **A check over Python code must parse it (AST), not scan it as text.** A string scan reads docstrings
  and comments as code.
- **A double must reproduce the production control flow it replaces** — or say it does not, and fail if
  the production path changes in a way the double does not model.
- **A guard that pins a state rather than a property is a nuisance.** Write it to fail on a *real*
  violation, not on the next legitimate addition.

## File conventions

- Tests live in a flat `tests/` tree (plus `tests/reliability/` and `tests/security/`). Naming follows the module under test (`wisp/transport/progress.py` → `tests/test_progress.py`) but paths do NOT mirror source layout
- New modules go in `wisp/` subpackage, not flat
- Transport modules: one class per file, shared utilities in `renderer.py`
- No `__init__.py` changes needed for internal transport modules used only by `cli.py`
