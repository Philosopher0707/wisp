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
| `wisp/core/engine.py` | Agent turn loop | `WispAgentCore.turn(session, prompt, approval_handler)` → `AsyncIterator[dict]` |
| `wisp/core/events.py` | Event system | `AgentEvent`, 12 factory functions (`thinking()`, `tool_call()`, etc.), `EventType` enum |
| `wisp/core/runtime.py` | Session management | `AgentRuntime`: session CRUD, per-session locks, core caching |
| `wisp/transport/base.py` | Transport ABC | `Transport`: `send()`, `recv()`, `approve()`, `start()`, `stop()` |
| `wisp/transport/cli_v2.py` | CLI transport | `CLITransport`: REPL loop, event rendering, thinking/content buffering |
| `wisp/transport/renderer.py` | Terminal rendering | Pure functions: `render_tool_call()`, `_box()`, `_rule()`, `render_phase_bar()`, `render_turn_stats()` |
| `wisp/transport/progress.py` | Progress tracking | `ProgressTracker`, `TurnProgress` — phase detection, tool counting, file tracking |
| `wisp/transport/spinner.py` | Terminal spinner | `Spinner` — inline `\r`-based spinner with mode-aware frames |
| `wisp/transport/server.py` | WebSocket transport | `ServerTransport`: event → JSON serialization, async approval |
| `wisp/transport/headless.py` | Headless transport | `HeadlessTransport`: collects events into result dict, no I/O |
| `wisp/tools/registry.py` | Tool definitions | `TOOL_SCHEMAS` (list), `TOOL_IMPLS` (dict), `execute_tool()`, `ToolRegistry` |
| `wisp/multi_agent/` | Subagent system | `SubagentOrchestrator`, `SubagentRunner`, `WorktreeManager`, `DelegationAnalyzer` |
| `wisp/config.py` | Configuration | `WispConfig` dataclass |
| `wisp/colors.py` | Terminal colors | `success()`, `error()`, `warning()`, `dim()`, `info()`, `accent()`, `bold()` |
| `wisp/terminal_width.py` | Display width | `display_width()`, `BoxChars`, `OutputMode`, `is_accessible()` |

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
2. Call from `CLITransport._render_event()` in `cli_v2.py`
3. Follow existing patterns: use `BoxChars`, `display_width()`, `dim()`/`success()`/`error()`

## Testing

```bash
# Transport + UX tests (fast, no I/O)
pytest tests/test_progress.py tests/test_spinner.py tests/test_renderer.py tests/test_transport_cli.py -v

# Transport integration tests
pytest tests/test_transport_server.py tests/test_transport_integration.py tests/test_transport_headless.py -v

# Core + runtime tests
pytest tests/test_core_stateless.py tests/test_runtime_concurrent.py tests/test_provider_integration.py -v

# Full suite (some files may not collect due to pre-existing issues)
python -m pytest tests/test_*.py -v
```

## File conventions

- Tests mirror source paths: `wisp/transport/progress.py` → `tests/test_progress.py`
- New modules go in `wisp/` subpackage, not flat
- Transport modules: one class per file, shared utilities in `renderer.py`
- No `__init__.py` changes needed for internal transport modules used only by `cli_v2.py`
