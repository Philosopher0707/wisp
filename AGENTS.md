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
| `wisp/core/provider_stream.py` | Provider stream guard | `guarded_provider_stream()`: first-token + mid-chunk stall deadlines, transient-error/empty-stream retry with backoff, honest truncation notice; all deps injected (stream opener, normalizer, deadlines) so it is testable without a core |
| `wisp/core/engine.py` | Back-compat shim | Re-exports `WispAgentCore` from `stateless.py` |
| `wisp/core/events.py` | Event system | `AgentEvent`, 12 factory functions (`thinking()`, `tool_call()`, etc.), `EventType` enum |
| `wisp/core/runtime.py` | Session management | `AgentRuntime`: session CRUD, per-session locks; `_get_core(session_id)` caches one `WispAgentCore` per (session, fingerprint), FIFO-bounded (`MAX_SESSION_CORES`); `invalidate_core_cache()` on config change |
| `wisp/transport/base.py` | Transport ABC | `Transport`: `send()`, `recv()`, `approve()`, `start()`, `stop()` |
| `wisp/transport/cli.py` | CLI transport | `CLITransport`: REPL loop, event rendering, thinking/content buffering; `_write_bg_line()` pause/write/resume protocol for bg notices; `open_subagent_monitor()` suspend/park/drain bridge |
| `wisp/cli/dispatcher.py` | Slash-command router | `Dispatcher.register()` decorator + `ReplContext` (runtime, transport, session, config); builtins: help/doctor/provider/model/expand/subagents/rewind/hooks; unknown names fall back to legacy registry, never reach the LLM |
| `wisp/transport/renderer.py` | Terminal rendering | Pure functions: `render_tool_call()`, `_box()`, `_rule()`, `render_phase_bar()`, `render_turn_stats()` |
| `wisp/transport/progress.py` | Progress tracking | `ProgressTracker`, `TurnProgress` — phase detection, tool counting, file tracking |
| `wisp/transport/spinner.py` | Terminal spinner | `Spinner` — inline `\r`-based spinner with mode-aware frames |
| `wisp/transport/websocket.py` | Live WebSocket transport | `WebSocketTransport`: connection ↔ session routing, event streaming, bidirectional approval; wired through `wisp/server/routes/agents.py` |
| `wisp/transport/headless.py` | Headless transport | `HeadlessTransport`: collects events into result dict, no I/O |
| `wisp/tools/registry.py` | Tool definitions | `TOOL_SCHEMAS` (list), `TOOL_IMPLS` (dict), `execute_tool()`, `ToolRegistry` |
| `wisp/tool_executor.py` | Tool call lifecycle | `ToolExecutor`: approval gating, pre/post hooks, dangerous-command blocking, metrics; named tools dispatch via `_SPECIAL_TOOL_ROUTES` table (uniform `(executor, func_args, workspace)` adapters), then MCP / run_bash / generic-pool branches |
| `wisp/tools/orchestration.py` | Orchestration pattern tools | `vote`, `map_reduce`, `chain`, `dag` behind `OrchestrationDeps(orchestrator, build_contract, tool_error)` — free functions, executor methods are one-line delegates |
| `wisp/tools/subagent_tools.py` | Background-subagent lifecycle tools | `wait`/`list_agents`/`result`/`send`/`cancel` behind `SubagentDeps(resolve_manager, tool_error)`; wait clamps to the parent turn deadline |
| `wisp/multi_agent/` | Subagent system | `SubagentOrchestrator`, `SubagentRunner`, `WorktreeManager`, `BackgroundAgentManager`, `SubagentTelemetryBuffer` |
| `wisp/multi_agent/background.py` | Background agent registry | `BackgroundAgentManager`: launch/send/cancel, lifecycle pub-sub (`agent_started/progress/settled`); publishes all lifecycle + chained TASK_* events into `telemetry` rings; `prune()` drops rings |
| `wisp/multi_agent/telemetry.py` | Per-agent telemetry rings | `SubagentTelemetryBuffer`: dual-bounded (events + bytes) per-worker rings, replay (`transcript`) + cursor poll, settle-status mapping; `mask_text()` producer-boundary secret masking |
| `wisp/tools/checkpoints.py` | File checkpoints | `CheckpointStore` (bounded per-workspace snapshots), `snapshot_before_mutation()`, `tool_rewind` (list/restore, rewindable rewind); auto-hooked in write/edit/edit_multi with drop-on-failed-mutation |
| `wisp/tui/screens/subagents.py` | Worker monitor screen | `SubagentMonitorScreen` (roster + live transcript, `]`/`[` cycle — never Tab — `c` cancel, `q`/`Ctrl+O`/`Esc` exit) + `SubagentMonitorApp` standalone host for the REPL bridge |
| `wisp/sandbox/` | Command confinement | Package (`__init__` = providers); `router.py`: `SandboxRouter` (Docker → `PtySandbox` → `NoopSandbox`, TTL-cached decision, silent failover) + `get_router()`; legacy `get_sandbox()` unchanged |
| `wisp/tools/primitives.py` | Thin harness surface | `exec_sandbox` / `fs_mutate` / `git_checkpoint` (pydantic args, delegate to bash/filesystem/checkpoints); `PRIMITIVE_SCHEMAS`; core opts in via `thin_tools` config (schemas + prompt menu + dispatcher) |
| `wisp/core/verification.py` | Completion gate | `VerificationFloorGuard`: blocks finish until exit-0 postdates last mutation or grind floor (`min_turns` + nudges) exhausts; `HARNESS_REJECTION` text; `resolved()` triggers auto-capture. **Also projects itself onto the acceptance model** via `floor_guard_criteria/evidence/verdict()` — read-only; the guard's own behaviour is unchanged |
| `wisp/core/acceptance.py` | Acceptance verdicts (P3, stage 3a) | `Verdict` (`PASS`/`FAIL`/`INCONCLUSIVE`), `CriterionKind`, `AcceptanceCriteria`, `Evidence` (content-addressed, with `producer` + `observations`), `CompletionVerdict`, `evaluate()`, `invalidate()`, `route_for()`. **Records; does not gate** — routing is *derived* from the verdict so the graph vocabulary stays an output, not a competitor |
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

The durable record (run registry, span sink, session journal, proposal boundary) is gated by five
independent flags. Each is read with `getattr(config, name, True)` — the `thin_tools` convention — so
`SimpleNamespace` test doubles keep working, and each is independently rollable.

| Flag | Env var | Gates |
|---|---|---|
| `durable_runs` | `WISP_DURABLE_RUNS` | `SQLiteRunStore` construction + `BackgroundAgentManager` persistence |
| `session_event_fidelity` | `WISP_SESSION_EVENT_FIDELITY` | transcript journal (`assistant_message` / `tool_call` / `tool_result`) |
| `turn_journal` | `WISP_TURN_JOURNAL` | incremental journaling of each exchange as it closes |
| `turn_spans` | `WISP_TURN_SPANS` | turn + tool-call span emission |
| `proposal_boundary` | `WISP_PROPOSAL_BOUNDARY` | `PROPOSAL` / `OUTCOME` records |
| `record_verdict` | `WISP_RECORD_VERDICT` | acceptance verdict (`VERDICT` event). **Defaults `false`** — unlike the others it adds a record to every existing caller's log |

Three rules that are easy to get wrong:

- **Read a flag ONCE.** `AgentRuntime.run_turn` reads all of them at one site because the stream loop
  and the `finally` block must agree; a flag read in two places is a flag that can disagree with itself.
- **`PROPOSAL` / `OUTCOME` are AUDIT-ONLY.** They must never append to `Session.messages`. The
  transcript is rebuilt from `ASSISTANT_MESSAGE(tool_calls=…)` + `TOOL_RESULT`; a second path in
  duplicates every tool reply on replay.
- **Durable writes are best-effort.** A turn that ran correctly must never be reported as failed
  because a journal or span write failed. The loss is visible as a gap in the session's sequence
  numbers. See `WISP_ARCHITECTURE_DECISIONS.md` ADR-0004.

### Stage 3a of the verification gate does NOT gate

`core/acceptance.py` computes a `CompletionVerdict` and the runtime **records** it. Nothing on the
completion path consumes it: `turn_succeeded` still derives from terminal evidence alone, and
`VerificationFloorGuard` still owns the completion invariant. Enabling the gate is **stage 3b**, a
separate decision taken on a measured `INCONCLUSIVE` rate — the plan rates P3 the highest-risk phase in
the migration precisely because it changes completion semantics. ADR-0016.

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

# Durable record + proposal boundary (migration P0-P2)
python3 -m pytest tests/test_durable_layer_reachable.py tests/test_turn_journal_incremental.py \
  tests/test_action_idempotency_key.py tests/test_proposal_boundary_records.py \
  tests/test_proposal_boundary_no_bypass.py tests/test_verdict_layer_recorded.py \
  tests/test_gate_order_corpus.py -q
```

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

## File conventions

- Tests live in a flat `tests/` tree (plus `tests/reliability/` and `tests/security/`). Naming follows the module under test (`wisp/transport/progress.py` → `tests/test_progress.py`) but paths do NOT mirror source layout
- New modules go in `wisp/` subpackage, not flat
- Transport modules: one class per file, shared utilities in `renderer.py`
- No `__init__.py` changes needed for internal transport modules used only by `cli.py`
