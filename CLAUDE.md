# CLAUDE.md

Project instructions for Claude Code when working in this repository.

## Project overview

Wisp is a local-first Python coding agent with Ollama backend. Architecture: event-driven stateless core → transport layer (CLI/WebSocket/TUI/headless) → I/O. SDK-style with layered abstractions.

## Build & test

```bash
pip install -e .                        # Install editable
python -m pytest tests/test_progress.py -v   # Single file
python -m pytest tests/test_transport_cli.py tests/test_progress.py tests/test_spinner.py tests/test_renderer.py -v  # Transport + UX
python -m pytest tests/ -v              # Full suite (may need explicit file list)

# Type checking (bare mypy uses the strict [tool.mypy] gate in pyproject.toml)
mypy
```

## Architecture layers

```
wisp/core/         Pure logic, zero I/O: WispAgentCore (turn loop), AgentEvent, AgentRuntime
wisp/transport/    I/O layer: Transport ABC + CLITransport, WebSocketTransport, HeadlessTransport, TUITransport
wisp/tools/        TOOL_SCHEMAS + TOOL_IMPLS in registry.py (~40 tools)
wisp/multi_agent/  Subagent orchestration: runner, orchestrator, worktree, delegation
wisp/infra/        Security, telemetry, extensions, store
```

## Key conventions

- **Events**: `AgentEvent` dataclass (frozen) with factory functions in `wisp/core/events.py`. Events flow: `engine.turn()` → `transport.send()` → `transport._render_event()`
- **Transports** implement `Transport` ABC (`base.py`): `send()`, `recv()`, `approve()`, `start()`, `stop()`
- **CLI rendering** uses mode-aware pure functions from `renderer.py` — all 4 output modes (unicode/ascii/accessible/minimal) handled via `BoxChars` and `OutputMode`
- **Testing**: pytest with `_MockRuntime` + `_MockIO` (StringIO-based) for transport tests. Stateless core tests use real `WispAgentCore` with mock providers
- **Config**: `WispConfig` dataclass in `wisp/config.py`. Resolution: env vars > config file > defaults
- **No comments** explaining WHAT code does — well-named identifiers handle that. Only WHY comments for non-obvious constraints

## CLI dashboard components (new)

- `wisp/transport/progress.py` — `ProgressTracker`: phase detection (understand→plan→execute→verify), tool counting, file tracking. Pure data, no I/O.
- `wisp/transport/spinner.py` — `Spinner`: inline terminal spinner with `\r` overwrites. Mode-aware frames.
- `wisp/transport/renderer.py` — `render_phase_bar()`, `render_turn_stats()`, `render_file_ticker()` added

## CLI event rendering flow

```
Tool Call → spinner.start(label)
Tool Result → spinner.succeed(label) or spinner.fail(label)
Phase change → render_phase_bar(new_phase)
Turn end → render_turn_stats(stats) + render_file_ticker(files) + separator
```
