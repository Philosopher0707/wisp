# AGENTS.md

Guidance for AI coding agents working in the Wisp codebase.

## How to approach tasks

1. **Read the architecture layers** — know which layer your change belongs in before coding
2. **Follow existing patterns** — new transports extend `Transport` ABC, new tools add schemas to `registry.py`, CLI rendering uses pure functions from `renderer.py`
3. **Test first** — all new code needs tests. Transport tests use `_MockRuntime` + `_MockIO`. Core tests use mock providers with real `WispAgentCore`
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
| `wisp/transport/cli.py` | CLI transport | `CLITransport`: REPL loop, event rendering, thinking/content buffering |
| `wisp/transport/renderer.py` | Terminal rendering | Pure functions: `render_tool_call()`, `_box()`, `_rule()`, `render_phase_bar()`, `render_turn_stats()` |
| `wisp/transport/progress.py` | Progress tracking | `ProgressTracker`, `TurnProgress` — phase detection, tool counting, file tracking |
| `wisp/transport/spinner.py` | Terminal spinner | `Spinner` — inline `\r`-based spinner with mode-aware frames |
| `wisp/transport/websocket.py` | Live WebSocket transport | `WebSocketTransport`: connection ↔ session routing, event streaming, bidirectional approval; wired through `wisp/server/routes/agents.py` |
| `wisp/transport/headless.py` | Headless transport | `HeadlessTransport`: collects events into result dict, no I/O |
| `wisp/tools/registry.py` | Tool definitions | `TOOL_SCHEMAS` (list), `TOOL_IMPLS` (dict), `execute_tool()`, `ToolRegistry` |
| `wisp/tool_executor.py` | Tool call lifecycle | `ToolExecutor`: approval gating, pre/post hooks, dangerous-command blocking, metrics; named tools dispatch via `_SPECIAL_TOOL_ROUTES` table (uniform `(executor, func_args, workspace)` adapters), then MCP / run_bash / generic-pool branches |
| `wisp/tools/orchestration.py` | Orchestration pattern tools | `vote`, `map_reduce`, `chain`, `dag` behind `OrchestrationDeps(orchestrator, build_contract, tool_error)` — free functions, executor methods are one-line delegates |
| `wisp/tools/subagent_tools.py` | Background-subagent lifecycle tools | `wait`/`list_agents`/`result`/`send`/`cancel` behind `SubagentDeps(resolve_manager, tool_error)`; wait clamps to the parent turn deadline |
| `wisp/multi_agent/` | Subagent system | `SubagentOrchestrator`, `SubagentRunner`, `WorktreeManager`, `DelegationAnalyzer` |
| `wisp/multi_agent/background.py` | Background agent registry | `BackgroundAgentManager`: launch/send/cancel, lifecycle pub-sub (`agent_started/progress/settled`) |
| `wisp/skill_capture.py` | Workflow capture | `SkillCapture`: record tool sequences, detect repeats, render Warp-compatible SKILL.md with merge-on-recapture |
| `wisp/config.py` | Configuration | `WispConfig` dataclass |
| `wisp/colors.py` | Terminal colors | `success()`, `error()`, `warning()`, `dim()`, `info()`, `accent()`, `bold()` |
| `wisp/terminal_width.py` | Display width | `display_width()`, `BoxChars`, `OutputMode`, `is_accessible()` |
| `wisp/tui/task_owner.py` | TUI fire-and-forget ownership | `OwnedTasks`: named spawn, exception logging via done-callbacks, `cancel_all()` on unmount — no bare `create_task` in screens (structural pin enforces) |

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

## Testing

```bash
# Transport + UX tests (fast, no I/O)
pytest tests/test_progress.py tests/test_spinner.py tests/test_renderer.py tests/test_transport_cli.py -v

# Transport integration tests
pytest tests/test_websocket.py tests/test_transport_headless.py -v

# Core + runtime tests
pytest tests/test_core_stateless.py tests/test_runtime_concurrent.py tests/test_provider_integration.py -v

# Full suite (all 184 test files collect cleanly — 2,765+ tests)
python -m pytest tests/test_*.py -v
```

## File conventions

- Tests mirror source paths: `wisp/transport/progress.py` → `tests/test_progress.py`
- New modules go in `wisp/` subpackage, not flat
- Transport modules: one class per file, shared utilities in `renderer.py`
- No `__init__.py` changes needed for internal transport modules used only by `cli.py`
