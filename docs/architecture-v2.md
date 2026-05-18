# Wisp Architecture v2 — Hexagonal Core + Layered Infrastructure

## Overview

The current architecture is **functionally correct** but structurally ad-hoc. 10 systemic issues create friction for every change: god modules (3K-line server.py), manager proliferation with no common lifecycle, persistence anarchy across four backends, configuration as a global variable, layered-but-uncoordinated security, leaking async/sync boundaries, zero integration between transport layers, three distinct extension systems solving the same problem, a TUI that shares nothing with the CLI, and no observability beyond text logging.

The v2 architecture addresses all 10 by applying three principles:

1. **Dependency Inversion** — the core defines interfaces; infrastructure implements them
2. **Explicit Composition Root** — one place wires the entire object graph, one event loop per process
3. **Unified Lifecycle** — every service shares `start() → health() → stop()`

---

## Layers (top → bottom)

### 1. Entry Points

Four products sharing one backend. Each creates a `Transport` and hands it an `AgentCore`.

| Entry Point | Creates |
|---|---|
| `wisp repl` / `wisp <prompt>` | `CLITransport` |
| `wisp tui` | `TUITransport` |
| `wisp server` | `HTTPTransport` + `WSTransport` |
| `wisp --print` / CI | `HeadlessTransport` |

### 2. Composition Root

A single bootstrap module (`wisp/bootstrap.py`) loaded by every entry point:

1. Load `WispConfig` from env vars → config file → defaults (resolve once)
2. **Freeze** the config (immutable from this point forward)
3. Create `ServiceRegistry` in dependency order
4. Wire domain services, injecting config + repository + provider
5. Start the **single event loop** for the process
6. Return the configured `WispAgentCore`

**Why this matters:** `WispConfig` is currently instantiated in 15+ places, reading from the environment at different times with potentially different values. In v2 it's created once, frozen, and injected. No ambient reads.

### 3. Transport Layer

Five transports, one interface:

```python
class Transport(ABC):
    """All I/O: rendering, user input, approval prompts."""

    async def on_event(self, event: AgentEvent) -> None: ...
    async def ask_approval(self, tool: str, args: dict, reason: str) -> bool: ...
```

Every transport receives the same `AgentEvent` stream from the same `WispAgentCore`. The transport decides how to render it — Rich panels for CLI, Textual widgets for TUI, WebSocket JSON frames for server.

**Why this matters:** Currently each transport invents its own event handling. A change to `AgentEvent` serialization can break WebSocket but not CLI. With a common interface, a single integration test can verify all five transports produce equivalent behavior for the same agent run.

### 4. Agent Core (unchanged, preserved)

`WispAgentCore` is already well-factored: pure logic, zero I/O, event-driven. It stays as-is. The only change: `approval_handler` becomes the `Transport.ask_approval()` method rather than an ad-hoc callback.

### 5. Domain Services (the new middle layer)

Five services that replace the "Manager" proliferation:

| Service (v2) | Replaces | Responsibility |
|---|---|---|
| **SecurityPolicy** | 5 scattered mechanisms | Composes permission_mode, trust, path blocking, dangerous-command detection, and approval handling into a single `Decision` |
| **ToolService** | ToolExecutor + execute_tool | Orchestrates tool execution through SecurityPolicy; handles MCP, subagent, and native tools uniformly |
| **ContextService** | ContextAssembler + _build_system_prompt | Builds system prompts from modular sections; skills, memory, git, project context, code index all plug in |
| **DelegationService** | SubagentOrchestrator + _auto_parallel_research | Spawns subagents with pattern composition (map-reduce, vote, chain); handles budget tracking and depth guards |
| **ExtensionRegistry** | PluginRegistry + HookManager + MCPManager | Unified registration for all extension types; tools, hooks, and MCP servers register through the same API |

All five implement the `Service` interface:

```python
class Service(ABC):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def health(self) -> HealthStatus: ...
```

### 6. Infrastructure

| Component (v2) | Responsibility |
|---|---|
| **WispConfig (frozen)** | Loaded once at bootstrap, immutable thereafter, injected everywhere |
| **Repository** | Single persistence boundary: SQLite primary, JSON compat layer. Sessions, runs, rate limits, metrics all go through here. Single `UnitOfWork` pattern for transaction boundaries |
| **ProviderPool** | Ollama + remote LLM backends with connection pooling |
| **Observability** | Structured metrics export (Prometheus-compatible), health endpoint that actually checks dependencies, structured JSON logging |

---

## How This Fixes Each Systemic Issue

### 1. server.py: The God Module → Domain Routers

**Current:** 3K lines, 48 top-level classes and functions, no separation of concerns.
**v2:** Split into `wisp/server/routes/` with one router per domain:

```
wisp/server/
  routes/
    agent.py        # prompt execution
    files.py        # file CRUD
    arena.py        # arena mode
    swarm.py        # swarm orchestration
    admin.py        # health, config, metrics
    extensions.py   # plugins, hooks, MCP
  dependencies.py   # FastAPI Depends() factory functions
  middleware.py     # CORS, rate limiting
  transport.py      # HTTPTransport, WSTransport
```

Each router declares its own Pydantic models, its own FastAPI `APIRouter`, and receives dependencies via FastAPI's `Depends()`. A change to the arena endpoint cannot break the rate limiter because they share nothing.

### 2. Manager Proliferation → Service Interface

**Current:** 10+ Manager classes, each with its own lifecycle pattern (asyncio.Lock, plain dicts, subprocesses, file locks).
**v2:** All domain services implement the `Service` interface. The `ServiceRegistry` starts them in dependency order and stops them in reverse. When the server shuts down, `ServiceRegistry.stop()` tears down everything — no orphaned subprocesses, no stale WebSocket connections, no unclosed file locks.

### 3. Persistence Anarchy → Repository Pattern

**Current:** JSON files for sessions, SQLite for rate limits, git worktrees for subagents, in-memory dicts for connections.
**v2:** A single `Repository` class with SQLite as the primary backend. JSON file support exists only as a migration compatibility layer. All state mutations go through a `UnitOfWork` that ensures atomicity across tables.

### 4. Config as Global Variable → Frozen Singleton

**Current:** `WispConfig()` instantiated at 15+ call sites, each reading the environment at different times.
**v2:** Created once in the Composition Root, frozen, and injected into every service. No service reads the environment directly.

### 5. Security Model → SecurityPolicy Composite

**Current:** Five mechanisms with no documented relationship or ordering.
**v2:** `SecurityPolicy` composes them into a single `can_execute(tool, args, context) → Decision` method. The ordering is explicit and tested:

1. read_only? → **block** all writes
2. auto_edit? → **block** bash and git (unless approval_handler says yes)
3. ask_all? → **defer** to approval_handler
4. dangerous command? → **block** regardless of mode
5. trust boundary? → **block** untrusted extensions
6. approval_handler available? → **defer** to it

### 6. Async/Sync Boundary → One Event Loop

**Current:** `asyncio.run()` scattered across CLI, __main__, and transport layers.
**v2:** The Composition Root starts exactly one event loop per process. All transports run within it. `asyncio.run()` appears exactly once in the whole codebase.

### 7. Test Architecture → Cross-Transport Integration Tests

**Current:** 128 test files covering modules in isolation; zero integration between transport layers.
**v2:** New `tests/test_cross_transport.py`: runs the same prompt through all five transports (mocked I/O) and asserts identical `AgentEvent` sequences. A change to event serialization that breaks WebSocket must also fail the CLI test — because they share the same test harness.

### 8. Plugin/Hook/MCP Trinity → ExtensionRegistry

**Current:** Three separate extension systems with different security models.
**v2:** `ExtensionRegistry` provides a single `register(extension: Extension)` method. Registration happens in the Composition Root. The registry knows about all extensions and can answer "what tools are available?" with a single query regardless of whether the tool came from a plugin, a hook, or MCP.

### 9. TUI as Separate Product → Shared Transport Abstraction

**Current:** TUI shares nothing with CLI beyond `WispAgentCore`.
**v2:** Both implement `Transport`. Rendering widgets (`diff_block.py`, `thinking_block.py`) are shared between CLI and TUI — the widget handles layout; the transport handles the I/O channel (terminal escape codes vs Textual composables).

### 10. No Observability → Health + Metrics + Structured Logging

**Current:** `AgentMetrics` (in-memory), Python `logging` (text), rate limiter (SQLite).
**v2:** Every `Service` exposes `health()` → `/api/health` aggregates all. Metrics are structured and exportable (Prometheus text format). Logging uses structured JSON (`{timestamp, level, service, event, ...}`).

---

## Migration Plan

### Phase 1: Extract server.py (lowest risk, highest impact)

1. Create `wisp/server/routes/` directory
2. Move each endpoint group into its own router file
3. Extract Pydantic models into `wisp/server/models.py`
4. Create `wisp/server/dependencies.py` for FastAPI Depends()
5. Wire everything in `wisp/server/__init__.py`
6. Run e2e tests against old and new endpoints

**Risk:** Low. REST API surface unchanged. WebSocket unchanged.

### Phase 2: Introduce Service Interface

1. Define `Service` ABC in `wisp/core/service.py`
2. Wrap existing Managers in `ServiceAdapter` (delegates to existing API)
3. Create `ServiceRegistry`
4. Add `bootstrap.py` that wires everything
5. Verify all four entry points start/shutdown cleanly

**Risk:** Medium. Service lifecycle is new. Test shutdown thoroughly.

### Phase 3: Unified Persistence

1. Define `Repository` interface
2. Implement `SQLiteRepository`
3. Migrate session storage to SQLite (keep JSON as read-only compat)
4. Migrate rate limits, background runs, swarm state into Repository
5. Add `UnitOfWork` for transaction boundaries

**Risk:** Medium-high. Data migration. Must not lose sessions.

### Phase 4: SecurityPolicy Consolidation

1. Define `SecurityPolicy` class
2. Migrate `_check_permission_mode` into it
3. Migrate dangerous-command checks
4. Migrate trust checks
5. Wire into `ToolService`

**Risk:** Medium. Security regressions are critical. Test every mode x tool combination.

### Phase 5: Transport Interface + Cross-Transport Tests

1. Define `Transport` ABC
2. Refactor `CLITransport`, `ServerTransport` to implement it
3. Create `TUITransport`, `HTTPTransport`, `HeadlessTransport`
4. Write `test_cross_transport.py`
5. Add health endpoint

**Risk:** Medium. Refactoring existing transports. Keep old interfaces as adapters.

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Breaking existing CLI workflows | Medium | High | Phase 1 touches server only. CLI/REPL unchanged until Phase 5. |
| Data loss during persistence migration | Low | High | Keep JSON as read-only compat. Migrate with dual-write first, then cut over. |
| Security regression in new SecurityPolicy | Low | Critical | Every mode × tool combination tested. Keep old checks as safety net during transition. |
| Android app breaks due to transport changes | Low | Medium | Android uses WebSocket; transport interface wraps existing behavior. |
| Performance regression from SQLite | Low | Medium | SQLite is already used for rate limits and background runs. Sessions are write-once, read-occasionally. |
| Merge conflicts with active feature branches | High | Low | Small PRs, one phase at a time, merge main frequently. |
