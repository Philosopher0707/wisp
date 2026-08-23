# Wisp Architecture v2: Collapsing the Complexity

## The Diagnosis

After 4 rounds of precision patching, the codebase has **28 more fixes** but **0 fewer concepts**. The zoom-out grill revealed 10 systemic issues that cannot be solved by patching — they require **architectural collapse**.

## The Collapse Strategy

Instead of 10 managers, 4 persistence backends, 3 extension systems, and 5 security layers, we collapse to **4 layers with 1 of each thing**:

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 0: Entry Points (4 files → 4 files, but 1 loop each)    │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────────┐  │
│  │ wisp   │  │ wisp   │  │ wisp   │  │ python -m wisp      │  │
│  │ cli    │  │ tui    │  │ server │  │ server              │  │
│  │ (sync) │  │ (async)│  │ (async)│  │ (async)             │  │
│  └────┬────┘  └────┬────┘  └────┬────┘  └──────────┬──────────┘  │
│       │            │            │                   │            │
│       └────────────┴────────────┴───────────────────┘            │
│                         │                                        │
│              ┌──────────▼──────────┐                             │
│              │  Composition Root   │  ← ONE per process           │
│              │  - load config      │     loads config once        │
│              │  - wire services    │     creates event loop       │
│              │  - start lifecycle  │     starts everything        │
│              └──────────┬──────────┘                             │
└─────────────────────────┼────────────────────────────────────────┘
                          │
┌─────────────────────────┼────────────────────────────────────────┐
│  LAYER 1: Transport API │  (was: cli.py, server.py websockets,     │
│                         │       tui/app.py, __main__.py)           │
│              ┌──────────▼──────────┐                             │
│              │   Transport (ABC)   │  ← ONE interface               │
│              │  - send(event)      │     all UIs implement        │
│              │  - recv() → prompt  │     all cores consume        │
│              │  - approve(tool)    │                              │
│              └──────────┬──────────┘                             │
│                       │                                          │
│         ┌─────────────┼─────────────┐                           │
│         │             │             │                           │
│    ┌────▼────┐   ┌────▼────┐   ┌────▼────┐                     │
│    │ CLITrans│   │ TUITrans│   │ WSTrans │                     │
│    │ (sync)  │   │ (async) │   │ (async) │                     │
│    └─────────┘   └─────────┘   └─────────┘                     │
└────────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────┼────────────────────────────────────────┐
│  LAYER 2: Agent Core    │  (was: core/agent.py + tool_executor.py  │
│                         │       + hooks.py + plugins + MCP)        │
│              ┌──────────▼──────────┐                             │
│              │   AgentRuntime      │  ← ONE orchestrator          │
│              │  - session lifecycle│     owns the turn loop       │
│              │  - compaction       │     owns session state       │
│              │  - background runs  │     delegates to core        │
│              └──────────┬──────────┘                             │
│                         │                                        │
│              ┌──────────▼──────────┐                             │
│              │   WispAgentCore     │  ← ONE engine (stateless)  │
│              │  - build prompt     │     receives store, provider │
│              │  - stream tokens    │     security policy, metrics │
│              │  - execute tools    │                              │
│              └──────────┬──────────┘                             │
│                         │                                        │
│              ┌──────────▼──────────┐                             │
│              │   Provider (ABC)    │  ← ONE interface           │
│              │  - OllamaProvider   │     composes OllamaClient    │
│              │  - MockProvider     │                              │
│              └─────────────────────┘                             │
└────────────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────┼────────────────────────────────────────┐
│  LAYER 3: Infrastructure│  (was: session.py, session_store.py,   │
│                         │       hooks.py, plugins/, mcp.py, etc)   │
│  ┌──────────────────────┼──────────────────────────────────────┐  │
│  │  ┌─────────────┐     │     ┌─────────────┐   ┌────────────┐  │  │
│  │  │ UnifiedStore│◄────┘────►│ Security    │   │ Telemetry  │  │  │
│  │  │ (SQLite)    │           │ Policy      │   │ (metrics)  │  │  │
│  │  │ - sessions  │           │ - permission│   │ - tracing  │  │  │
│  │  │ - runs      │           │ - trust     │   │ - health   │  │  │
│  │  │ - events    │           │ - hooks     │   │            │  │  │
│  │  │ - memory    │           │ - audit     │   │            │  │  │
│  │  │ - arena     │           │             │   │            │  │  │
│  │  └─────────────┘           └─────────────┘   └────────────┘  │  │
│  │                                                             │  │
│  │  ┌─────────────────────────────────────────────────────┐   │  │
│  │  │           ExtensionHost (ONE system, not three)      │   │  │
│  │  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  │   │  │
│  │  │  │ Plugin  │  │ Hook    │  │ MCP     │  │ Skill   │  │   │  │
│  │  │  │ (in-pro)│  │ (subpr) │  │ (subpr) │  │ (static)│  │   │  │
│  │  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘  │   │  │
│  │  │  All implement: Extension interface                    │   │  │
│  │  │  - name() → str                                      │   │  │
│  │  │  - tools() → list[Tool]                              │   │  │
│  │  │  - intercept(event) → EventResult                    │   │  │
│  │  │  - lifecycle() → start/stop                        │   │  │
│  │  └─────────────────────────────────────────────────────┘   │  │
│  └─────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

---

## What Gets Deleted

| Current | Count | After Collapse | Count |
|---------|-------|----------------|-------|
| Manager classes | 10 | ServiceRegistry (manages lifecycle) | 1 |
| Persistence backends | 4 (JSON, SQLite, git, memory) | UnifiedStore (SQLite) | 1 |
| Extension systems | 3 (plugins, hooks, MCP) | ExtensionHost | 1 |
| Security layers | 5 | SecurityPolicy | 1 |
| Config instantiations | 15+ | Config (singleton, DI) | 1 |
| Event loops | 4+ | 1 per process | 1 |
| server.py endpoints | 48 functions | Routers per domain | 6 files |

---

## Layer-by-Layer Design

### Layer 0: Entry Points

**Rule:** Each entry point creates exactly one event loop, loads config once, and delegates to the Composition Root.

```python
# wisp/__main__.py (CLI entry)
def main():
    config = Config.from_env_and_args()  # ONE read
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    root = CompositionRoot(config, loop)
    root.start()  # starts all services
    
    try:
        if config.mode == "cli":
            CLITransport(root.runtime).run()
        elif config.mode == "tui":
            loop.run_until_complete(TUITransport(root.runtime).run())
        elif config.mode == "server":
            loop.run_until_complete(ServerTransport(root.runtime).run())
    finally:
        root.shutdown()  # stops all services in reverse order
```

**Why this collapses questions:**
- No more `asyncio.run()` scattered across 15 call sites
- No more `get_running_loop()` guessing games
- Background thread from `async_utils.py` becomes unnecessary — the entry point owns the loop

---

### Layer 1: Transport API

**Rule:** All UIs implement the same `Transport` interface. The core never knows which transport is running.

```python
class Transport(ABC):
    @abstractmethod
    async def send(self, event: AgentEvent) -> None: ...
    
    @abstractmethod
    async def recv(self) -> Prompt: ...
    
    @abstractmethod
    async def approve(self, tool_call: ToolCall) -> Approval: ...
```

**Why this collapses questions:**
- The TUI and CLI share the same interface — no feature parity drift
- Integration tests become: `for transport in [CLITransport, WSTransport]: run_same_test(transport)`
- The server.py WebSocket handler becomes a `WSTransport` implementation, not 48 inline functions

---

### Layer 2: Agent Core

**Rule:** `WispAgentCore` is stateless. All state lives in `AgentRuntime`.

```python
class AgentRuntime:
    """Owns the lifecycle of one agent session."""
    
    def __init__(
        self,
        config: Config,
        store: UnifiedStore,
        provider: Provider,
        security: SecurityPolicy,
        extensions: ExtensionHost,
        telemetry: Telemetry,
    ):
        self.config = config
        self.store = store
        self.core = WispAgentCore(
            config=config,
            provider=provider,
            security=security,
            extensions=extensions,
            telemetry=telemetry,
        )
    
    async def run(self, transport: Transport) -> None:
        session = self.store.load_or_create(self.config.session_id)
        
        async for prompt in transport.recv():
            async for event in self.core.turn(session, prompt):
                await transport.send(event)
                
                if event.type == "tool_call":
                    approval = await transport.approve(event.tool_call)
                    if not approval.approved:
                        self.core.reject_tool(event.tool_call, approval.reason)
            
            self.store.save(session)
```

**Why this collapses questions:**
- `AgentMetrics` is no longer lazy-loaded — it's injected as `Telemetry`
- Session compaction happens in `AgentRuntime`, not scattered across `core/agent.py`
- Background runs are just `AgentRuntime` instances with a `FileTransport` instead of `CLITransport`

---

### Layer 3: Infrastructure

#### UnifiedStore (replaces session.py + session_store.py + JSON files)

```python
class UnifiedStore:
    """Single SQLite database for all persistent state."""
    
    def __init__(self, db_path: Path):
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()
    
    @contextmanager
    def transaction(self):
        """All writes go through here. Atomic across tables."""
        try:
            yield self.db
            self.db.commit()
        except:
            self.db.rollback()
            raise
    
    def save_session(self, session: Session) -> None:
        with self.transaction():
            self._upsert("sessions", session.to_dict())
            for msg in session.messages:
                self._upsert("messages", msg)
    
    def save_run(self, run: BackgroundRun) -> None:
        with self.transaction():
            self._upsert("runs", run.to_dict())
            for event in run.events:
                self._upsert("events", event)
```

**Why this collapses questions:**
- No more JSON file locking, no more `filelock` dependency
- Sessions, runs, events, memory, arena results — all in one database
- Cross-table consistency: a run and its events are committed together
- The `_SessionManagerCore` / `SessionManager` / `UnifiedSessionStore` trinity becomes one class

#### SecurityPolicy (replaces permission modes + trust + hooks + path blocking)

```python
@dataclass(frozen=True)
class SecurityPolicy:
    """One decision function for all security questions."""
    permission_mode: PermissionMode
    trusted_workspaces: frozenset[Path]
    
    def check(self, action: Action, context: Context) -> Decision:
        # Layer 1: Permission mode (coarse)
        if not self._mode_allows(action):
            return Decision(block=True, reason=f"Mode {self.permission_mode} blocks {action}")
        
        # Layer 2: Trust (workspace-level)
        if not self._workspace_trusted(context.workspace):
            return Decision(block=True, reason="Untrusted workspace")
        
        # Layer 3: Hooks (fine-grained, user-defined)
        hook_result = self._run_hooks(action, context)
        if hook_result.action == "block":
            return Decision(block=True, reason=hook_result.reason)
        
        # Layer 4: Audit (always)
        self._audit(action, context, approved=True)
        
        return Decision(block=False)
```

**Why this collapses questions:**
- One place to answer "can this tool run?"
- Hooks are just another layer in the decision stack, not a separate system
- The OS-level read-only enforcement becomes a `SecurityPolicy` concern, not a `HookManager` side effect

#### ExtensionHost (replaces plugins + hooks + MCP)

```python
class ExtensionHost:
    """One system for all extensions."""
    
    def __init__(self):
        self._extensions: list[Extension] = []
    
    def register(self, ext: Extension) -> None:
        self._extensions.append(ext)
        ext.start()
    
    def tools(self) -> list[Tool]:
        return [t for ext in self._extensions for t in ext.tools()]
    
    def intercept(self, event: Event) -> EventResult:
        for ext in self._extensions:
            result = ext.intercept(event)
            if result.action != "allow":
                return result
        return EventResult.allow()
    
    def shutdown(self) -> None:
        for ext in reversed(self._extensions):
            ext.stop()

# Implementations:
class PluginExtension(Extension): ...      # in-process Python
class HookExtension(Extension): ...        # subprocess scripts
class MCPExtension(Extension): ...         # subprocess stdio/SSE
class SkillExtension(Extension): ...       # static markdown files
```

**Why this collapses questions:**
- One lifecycle: all extensions start together, stop together
- One namespace: `myplugin__read_file`, `myhook__pre_bash`, `mcp__filesystem__read` — all prefixed
- One security model: all extensions run through the same `SecurityPolicy`
- The user doesn't choose between "plugin" and "hook" and "MCP" — they choose "extension type"

#### Telemetry (replaces AgentMetrics + logging + health checks)

```python
class Telemetry:
    """Observability for the agent."""
    
    def __init__(self, config: TelemetryConfig):
        self.metrics = MetricsCollector()
        self.tracer = Tracer()
        self.health = HealthChecker()
    
    def record_turn(self, latency_ms: float, tokens: int) -> None:
        self.metrics.histogram("turn_latency_ms", latency_ms)
        self.metrics.counter("turns_total").inc()
        self.metrics.gauge("session_tokens").set(tokens)
    
    def check_health(self) -> HealthStatus:
        return self.health.check_all()
```

**Why this collapses questions:**
- Metrics are structured from the start — no more ad-hoc `logger.warning()`
- Health checks are explicit, not implicit from "can I connect to Ollama?"
- The server `/api/health` endpoint calls `telemetry.check_health()`, not inline logic

---

## The Refactoring Plan

### Phase 1: Extract Infrastructure (2 weeks)
1. Create `wisp/infra/unified_store.py` — migrate sessions, runs, events to SQLite
2. Create `wisp/infra/security_policy.py` — merge permission modes, trust, hooks
3. Create `wisp/infra/extension_host.py` — unify plugins, hooks, MCP
4. Create `wisp/infra/telemetry.py` — replace AgentMetrics

### Phase 2: Extract Core (1 week)
1. Make `WispAgentCore` stateless — inject store, security, telemetry
2. Create `AgentRuntime` — owns session lifecycle, compaction, background runs
3. Delete `SessionManager`, `_SessionManagerCore`, `UnifiedSessionStore` — use `UnifiedStore`

### Phase 3: Extract Transports (1 week)
1. Create `Transport` ABC
2. Implement `CLITransport`, `TUITransport`, `WSTransport`
3. Split `server.py` into routers: `routes/sessions.py`, `routes/files.py`, `routes/arena.py`, etc.

### Phase 4: Composition Root (3 days)
1. Create `CompositionRoot` — wires everything
2. Update entry points to use it
3. Delete `async_utils.py` background thread — entry point owns the loop

### Phase 5: Tests (1 week)
1. Write `test_transport_unified.py` — same test, all transports
2. Write `test_security_policy.py` — decision matrix
3. Write `test_extension_host.py` — lifecycle, isolation

---

## What the New File Tree Looks Like

```
wisp/
├── __main__.py              # Entry point dispatcher
├── cli.py                   # CLI argument parsing
├── composition.py           # CompositionRoot
│
├── core/
│   ├── agent.py             # WispAgentCore (stateless)
│   ├── runtime.py           # AgentRuntime (stateful)
│   ├── events.py            # AgentEvent, types
│   └── tools/
│       ├── schemas.py       # TOOL_SCHEMAS
│       ├── registry.py      # Built-in tool registry
│       └── executor.py      # Tool execution
│
├── transport/
│   ├── base.py              # Transport ABC
│   ├── cli.py               # CLITransport
│   ├── tui.py               # TUITransport
│   └── server/
│       ├── main.py          # FastAPI app (100 lines)
│       ├── ws.py            # WebSocket transport
│       ├── routes/
│       │   ├── sessions.py
│       │   ├── files.py
│       │   ├── arena.py
│       │   ├── swarm.py
│       │   ├── plugins.py
│       │   └── health.py
│       └── deps.py          # FastAPI dependencies (DI)
│
├── infra/
│   ├── config.py            # Config (was wisp/config.py)
│   ├── store.py             # UnifiedStore (SQLite)
│   ├── security.py          # SecurityPolicy
│   ├── extensions.py        # ExtensionHost
│   ├── telemetry.py         # Metrics, tracing, health
│   └── lifecycle.py         # ServiceRegistry
│
├── extensions/
│   ├── plugins.py           # PluginExtension
│   ├── hooks.py             # HookExtension
│   ├── mcp.py               # MCPExtension
│   └── skills.py            # SkillExtension
│
├── providers/
│   ├── base.py
│   ├── ollama.py
│   └── mock.py
│
└── tests/
    ├── test_transport_unified.py
    ├── test_security_policy.py
    ├── test_extension_host.py
    └── test_composition.py
```

**Lines of code estimate:**
- Current: ~39K source + ~23K tests = ~62K total
- After: ~25K source + ~15K tests = ~40K total
- **Reduction: 35% fewer lines, 60% fewer concepts**

---

## The Meta-Collapse

The deepest question from the zoom-out grill was:

> Are you patching a fundamentally sound architecture that had implementation bugs? Or are you polishing an architecture that has too many responsibilities?

**The answer:** The current architecture is a **bag of features**, not a **system of layers**. Each feature (hooks, MCP, arena, swarm, plugins) was added as a vertical slice that touches every layer. The result is a codebase where:

- `server.py` knows about arena voting
- `hooks.py` knows about file permissions
- `session.py` knows about JSON serialization
- `mcp.py` knows about subprocess management

The v2 architecture inverts this: **each feature is a horizontal layer that knows about one thing**. The arena is a route. The hook is an extension. The session is a row in a table. The permission is a policy decision.

**This is not a refactor. This is a rewrite of the wiring.** The business logic (how to parse a tool call, how to stream tokens, how to compact a session) stays mostly the same. The architecture (who owns what, who calls whom, where state lives) changes completely.

The question is not "can we afford to do this?" The question is **"can we afford not to?"**

Every new feature added to the current architecture increases the surface area exponentially. The v2 architecture makes new features additive, not multiplicative.

---

*End of architecture document.*
