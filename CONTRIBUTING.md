# Contributing to Wisp

Thank you for your interest in contributing to Wisp! This document outlines the development workflow, coding standards, and how to submit changes.

## Quick Start

```bash
# Clone and install
git clone https://github.com/your-org/wisp.git
cd wisp
pip install -e ".[dev]"

# Run tests
pytest tests/test_core_stateless.py -v

# Type checking
mypy wisp/core/stateless.py --strict

# Linting
ruff check wisp/
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Wisp Architecture                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐     │
│  │   Transport  │◄───│  WispAgent   │───►│    Tools     │     │
│  │   (CLI/TUI/  │    │    Core      │    │  (30+ tools) │     │
│  │   WebSocket) │    │  (stateless) │    │              │     │
│  └──────────────┘    └──────────────┘    └──────────────┘     │
│         ▲                   ▲                   ▲               │
│         │                   │                   │               │
│  ┌──────┴──────┐    ┌──────┴──────┐    ┌──────┴──────┐        │
│  │  Renderer   │    │  Provider   │    │  Security   │        │
│  │  (mode-     │    │  (Ollama/   │    │  (Policy/   │        │
│  │   aware)    │    │   OpenAI)   │    │   Approval) │        │
│  └─────────────┘    └─────────────┘    └─────────────┘        │
│         ▲                   ▲                   ▲               │
│         │                   │                   │               │
│  ┌──────┴───────────────────┴───────────────────┴──────┐      │
│  │              AgentRuntime (session mgmt)             │      │
│  │  • Sessions  • Compaction  • Core Cache  • Locks    │      │
│  └──────────────────────────────────────────────────────┘      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Stateless Core** — `WispAgentCore.turn(session, prompt, approval_handler)` has no internal state; all state is injected via `session` dict
2. **Transport Abstraction** — All I/O goes through `Transport` ABC (CLI, TUI, WebSocket, SSE, Headless)
3. **Event-Driven** — Core yields `AgentEvent` dicts; transports handle rendering
4. **Mode-Aware Output** — Terminal rendering supports unicode/ascii/accessible/minimal modes
5. **Pure Functions** — Rendering, progress tracking, tool execution are pure/testable

---

## Development Workflow

### Branching
- `main` — protected, release-ready
- Feature branches: `feat/<short-description>`
- Fix branches: `fix/<issue-number>-<short-description>`
- Docs: `docs/<description>`

### Commit Messages
Follow conventional commits:
```
feat(core): add circuit breaker for provider resilience
fix(transport): handle ANSI escape sequences in input
docs: add architecture diagram to CONTRIBUTING.md
refactor(tools): split filesystem tools into module
test(circuit_breaker): add half-open state tests
```

### Pull Requests
1. Create PR against `main`
2. Ensure all checks pass:
   - `pytest tests/ -x` (or relevant subset)
   - `mypy wisp/core/stateless.py --strict`
   - `ruff check wisp/`
3. Request review from maintainers
4. Squash merge after approval

---

## Code Standards

### Type Hints
- All new code must have type hints
- Core modules (`wisp/core/`, `wisp/providers/`, `wisp/transport/`) use `--strict` mypy
- Use `TYPE_CHECKING` for circular imports
- Prefer `Protocol` over concrete types for dependencies

### Testing
- **Unit tests**: Fast, no I/O, mock providers (`_MockProvider`, `_MockRuntime`)
- **Integration tests**: Real providers, marked with `@pytest.mark.live`
- **Transport tests**: Use `_MockIO` (StringIO-based) for stdin/stdout
- Test file mirrors source: `wisp/core/stateless.py` → `tests/test_core_stateless.py`

### Rendering
All terminal output must handle 4 modes:
- `unicode` — full emoji, box drawing
- `ascii` — ASCII-only box drawing
- `accessible` — semantic labels, no emoji (screen readers)
- `minimal` — flat output, no boxes

Use `BoxChars`, `OutputMode`, `display_width()`, `wrap_text_wide()` from `wisp.terminal_width`.

### Error Handling
- Provider errors: retry with backoff (2 attempts) in core
- Circuit breaker: fail fast after 5 consecutive failures
- Tool errors: normalized to `{"status": "error", "data": "...", "metadata": {...}}`
- Never leak exceptions to user — wrap in `error_event()`

---

## Adding a New Tool

1. **Add schema** to `TOOL_SCHEMAS` in `wisp/tools/registry.py`
2. **Add implementation** to appropriate module in `wisp/tools/` (or new module)
3. **Register** in `TOOL_IMPLS` dict
4. **Update** `DEFAULT_SYSTEM` prompt in `wisp/context_assembler.py` if needed
5. **Write tests** in `tests/test_tools_registry.py`

Example schema:
```python
{
    "type": "function",
    "function": {
        "name": "my_tool",
        "description": "Clear description of what the tool does",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."},
            },
            "required": ["param1"],
        },
    },
}
```

---

## Adding a New Transport

1. **Extend** `Transport` ABC from `wisp/transport/base.py`
2. **Implement** all 5 methods: `send()`, `recv()`, `approve()`, `start()`, `stop()`
3. **Register** in `wisp/transport/__init__.py`
4. **Use** renderer functions from `wisp/transport/renderer.py` for consistent output

---

## Configuration

Settings priority: **env vars > config file > defaults**

Config file: `~/.config/wisp/config.json`

```json
{
  "provider": "ollama",
  "model": "llama3.2",
  "temperature": 0.2,
  "auto_approve": false,
  "show_thinking": true,
  "max_iterations": 30
}
```

Env vars: `WISP_PROVIDER`, `WISP_MODEL`, `WISP_TEMPERATURE`, etc.

---

## Debugging

### Enable Debug Logging
```bash
export WISP_LOG_FORMAT=json
export PYTHONPATH=.
python -m wisp repl --model llama3.2
```

### Common Issues
| Issue | Solution |
|-------|----------|
| "No LLM provider configured" | Set `WISP_PROVIDER=ollama` or add to config |
| "Circuit breaker OPEN" | Wait 30s or check Ollama is running |
| Import errors | Run `pip install -e .` from repo root |
| Mypy errors | Check `TYPE_CHECKING` imports, add annotations |

---

## Release Process

1. Update version in `wisp/__init__.py`
2. Update `CHANGELOG.md`
3. Tag release: `git tag v0.x.x`
4. GitHub Actions builds and publishes to PyPI

---

## Getting Help

- **Issues**: GitHub Issues for bugs/features
- **Discussions**: GitHub Discussions for questions
- **Security**: Email security@wisp.dev for vulnerabilities

---

## License

MIT License — see `LICENSE` file.