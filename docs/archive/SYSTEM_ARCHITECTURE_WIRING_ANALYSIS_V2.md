# Wisp System Architecture — Complete Wiring & Logical Connectivity Analysis

**Role:** Senior Software System Architect  
**Scope:** Full end-to-end architecture mapping, dependency graph, and logical connectivity audit  
**Date:** 2025-01-20  
**Codebase:** ~34K lines, 160+ Python modules across `wisp/` package

---

## Executive Summary

Wisp is a **multi-modal AI coding agent** with a layered architecture that has undergone significant refactoring (v2). The system demonstrates **strong separation of concerns** at the macro level but retains **tight coupling** in critical hot paths. The architecture is **logically sound** in its current state but has **7 categories of wiring issues** that create fragility, testability problems, and potential runtime failures.

**Overall Verdict:** 🟡 **Architecture is coherent but has coupling debt.** The CompositionRoot successfully centralizes wiring, but several anti-patterns (nested event loops, circular dependencies, god classes) remain in production code.

---

## 1. Architecture Layers (The Big Picture)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ LAYER 0: ENTRY POINTS                                                         │
│ ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐   │
│ │ python -m   │  │ wisp cli    │  │ wisp server │  │ wisp tui            │   │
│ │ wisp        │  │ (sync REPL) │  │ (FastAPI)   │  │ (Textual/React)     │   │
│ └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘   │
│        │                │                │                    │              │
│        └────────────────┴────────────────┴────────────────────┘              │
│                                   │                                         │
│                    ┌──────────────▼──────────────┐                          │
│                    │      CompositionRoot        │  ← ONE per process        │
│                    │  (DI container + lifecycle) │                          │
│                    └──────────────┬──────────────┘                          │
└───────────────────────────────────┼─────────────────────────────────────────┘
                                    │
┌───────────────────────────────────┼─────────────────────────────────────────┐
│ LAYER 1: TRANSPORT API            │                                         │
│ ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│ │ CLITransport│  │ TUITransport│  │ WSTransport │  │ HeadlessTransport   │  │
│ │ (sync)      │  │ (async)     │  │ (async)     │  │ (async, server)     │  │
│ └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│        All implement Transport ABC: send(), recv(), approve(), start()/stop()│
└───────────────────────────────────┼─────────────────────────────────────────┘
                                    │
┌───────────────────────────────────┼─────────────────────────────────────────┐
│ LAYER 2: AGENT CORE               │                                         │
│ ┌─────────────────────────────────▼─────────────────────────────────────┐   │
│ │                         AgentRuntime                                   │   │
│ │  - Session lifecycle (get_or_create_session, run_turn)                │   │
│ │  - Compaction (auto-compact before turn)                              │   │
│ │  - Auto-delegation (_maybe_delegate)                                  │   │
│ │  - Per-session asyncio.Lock (concurrent turn serialization)           │   │
│ │  - Core instance caching (warm-start, thread-safe)                    │   │
│ └─────────────────────────────────┬─────────────────────────────────────┘   │
│                                   │                                       │
│ ┌─────────────────────────────────▼─────────────────────────────────────┐   │
│ │                      WispAgentCore (stateless)                       │   │
│ │  - turn(): streaming turn loop (generate → tool_calls → execute)   │   │
│ │  - _build_system_prompt(): assembles context from 10+ sources       │   │
│ │  - _stream_events_async(): delegates to Provider                    │   │
│ │  - _get_tool_schemas(): aggregates from ToolRegistry + Extensions   │   │
│ └─────────────────────────────────┬─────────────────────────────────────┘   │
│                                   │                                       │
│ ┌─────────────────────────────────▼─────────────────────────────────────┐   │
│ │                      Provider (ABC)                                  │   │
│ │  - generate_stream_events(): yields content/tool_call/done/error    │   │
│ │  - OllamaProvider: concrete implementation                          │   │
│ │  - ProviderFactory: registration + instantiation                    │   │
│ └─────────────────────────────────────────────────────────────────────┘   │
└───────────────────────────────────┼─────────────────────────────────────────┘
                                    │
┌───────────────────────────────────┼─────────────────────────────────────────┐
│ LAYER 3: INFRASTRUCTURE           │                                         │
│ ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│ │ UnifiedStore│  │ SecurityPolicy│  │ ExtensionHost│  │ Telemetry         │  │
│ │ (SQLite)    │  │ (rule engine) │  │ (plugins,    │  │ (metrics, tracing)│  │
│ │ - sessions  │  │ - permissions │  │  hooks, MCP,  │  │                   │  │
│ │ - runs      │  │ - trust       │  │  skills)      │  │                   │  │
│ │ - events    │  │ - audit       │  │               │  │                   │  │
│ │ - memory    │  │               │  │               │  │                   │  │
│ └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│ ┌─────────────────────────────────────────────────────────────────────────┐  │
│ │                        ToolExecutor                                      │  │
│ │  - Pre-tool hooks → Plan mode guard → Dangerous cmd block → Approval    │  │
│ │  - Executes: native tools / MCP tools / subagent spawn                  │  │
│ │  - Post-tool hooks → Audit logging → Metrics                            │  │
│ └─────────────────────────────────────────────────────────────────────────┘  │
│ ┌─────────────────────────────────────────────────────────────────────────┐  │
│ │                    SubagentOrchestrator                                    │  │
│ │  - BudgetTracker (token accounting)                                      │  │
│ │  - ResultCache (TTL-based, SHA key)                                    │  │
│ │  - WorktreeManager (git worktree isolation)                              │  │
│ │  - SubagentRunner (direct async execution)                              │  │
│ │  - Patterns: map_reduce, vote, chain                                     │  │
│ └─────────────────────────────────────────────────────────────────────────┘  │
│ ┌─────────────────────────────────────────────────────────────────────────┐  │
│ │                        ServiceRegistry                                   │  │
│ │  - start(): dependency-order startup                                   │  │
│ │  - stop(): reverse-order shutdown with timeout                         │  │
│ │  - healthy(): health checks for all services                           │  │
│ └─────────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Complete Wiring Map (Dependency Graph)

### 2.1 Entry Points → CompositionRoot

| Entry Point | File | Creates | Calls | Notes |
|-------------|------|---------|-------|-------|
| `python -m wisp` | `__main__.py` | `WispConfig()` | `run_mode()` in `entry.py` | CLI arg parsing, then delegates |
| `wisp server` | `__main__.py:cmd_server` | — | `run_mode("server")` | Server creates its own root in lifespan |
| `wisp repl` | `__main__.py:cmd_repl` | — | `run_mode("cli")` | REPL mode, persistent loop |
| `wisp run` | `__main__.py:cmd_run` | — | `run_mode("cli", prompt=...)` | Single-shot mode |
| `wisp tui` | `__main__.py:cmd_tui` | `WispConfig()` | `WispTUIApp(config)` | Textual TUI (separate path) |
| `wisp print` | `__main__.py:cmd_print` | — | `run_headless()` or HTTP POST | Headless JSON output |

**Wiring Issue #1:** `__main__.py` still has legacy paths (`cmd_tui` creates config directly, bypasses `CompositionRoot`). The `cmd_print` tries HTTP first, then falls back to `run_headless()` which creates its own config/runtime.

### 2.2 CompositionRoot → Infrastructure Services

```
CompositionRoot.__post_init__()
  ├── UnifiedStore(db_path) ──────────────────────► SQLite persistence
  ├── ImmutableAuditTrail(store) ─────────────────► Audit logging
  ├── SecurityPolicy(permission_mode, audit_trail) ► Permission engine
  ├── ExtensionHost() ───────────────────────────► Plugin/Hook/MCP/Skill registry
  ├── Telemetry() ─────────────────────────────────► Metrics collection
  ├── HookManager(workspace) ──────────────────────► Shared with ToolExecutor
  ├── MCPManager(workspace) ───────────────────────► Shared with ToolExecutor
  ├── FileLock(workspace) ─────────────────────────► Concurrent file safety
  ├── LSPManager(workspace) ───────────────────────► Language server ops
  ├── ToolRegistry() ──────────────────────────────► Native tool schemas
  ├── SubagentOrchestrator(config, workspace, hook_manager)
  │     └── SubagentRunner(parent_config, workspace, store)
  ├── ToolExecutor(config, hook_manager, mcp, file_lock, lsp_manager, subagent_orchestrator)
  │     └── [Phase 2 injection] subagent_orchestrator._runner._tool_executor = tool_executor
  ├── Compactor(provider_factory, model) ──────────► LLM-powered summarization
  └── AgentRuntime(store, security, extensions, telemetry, core_factory, compactor, orchestrator)
        └── core_factory() → WispAgentCore(config, provider, security, extensions, tool_executor)
```

**Wiring Issue #2:** Phase 2 injection (`subagent_orchestrator._runner._tool_executor = self.tool_executor`) is a circular dependency workaround. The `SubagentRunner` needs `ToolExecutor` to run subagent tools, but `ToolExecutor` needs `SubagentOrchestrator` to spawn subagents. This is resolved by late injection but creates temporal coupling.

### 2.3 AgentRuntime → WispAgentCore (Turn Loop)

```
AgentRuntime.run_turn(session, prompt, approval_handler)
  ├── get_or_create_session(session_id, model, workspace)
  │     └── UnifiedStore.load_session() or create new
  ├── maybe_compact(session) ──► Compactor.compact_if_needed()
  ├── _maybe_delegate(prompt, session, config) ──► SubagentOrchestrator
  ├── _get_core() ──► cached WispAgentCore (warm-start)
  │     └── core.turn(session, prompt, approval_handler)
  │           ├── _build_system_prompt(session, query=prompt)
  │           │     └── ContextAssembler (10+ sources)
  │           ├── _get_tool_schemas()
  │           │     ├── ToolRegistry.tools()
  │           │     └── ExtensionHost.tools()
  │           ├── _stream_events_async(system_prompt, messages, tools)
  │           │     └── Provider.generate_stream_events()
  │           │           └── OllamaProvider (HTTP streaming)
  │           ├── For each tool_call:
  │           │     ├── ApprovalGate.check() ──► SecurityPolicy
  │           │     ├── ExtensionHost.intercept()
  │           │     └── ToolExecutor.execute()
  │           │           ├── Pre-tool hooks
  │           │           ├── Dangerous command block
  │           │           ├── Permission mode guard
  │           │           ├── Approval gating
  │           │           ├── execute_tool() ──► native/MCP/subagent
  │           │           └── Post-tool hooks + audit
  │           └── yield events (content, tool_call, tool_result, error, done)
  └── Save session + emit turn stats
```

**Wiring Issue #3:** `_get_core()` caches the core instance but the cache key doesn't include config mutations. If config changes between turns, stale core is used. The cache is thread-safe (`threading.Lock`) but not config-version-safe.

### 2.4 Transport Layer Wiring

```
CLITransport (sync, blocking)
  ├── runtime: AgentRuntime
  ├── config: WispConfig
  ├── start() ──► print banner
  ├── recv() ──► input() [blocking]
  ├── send(event) ──► _render_event() → stdout
  ├── approve(tool_call) ──► interactive y/n prompt
  └── stop() ──► cleanup

TUITransport (async, Textual)
  ├── runtime: AgentRuntime
  ├── start() ──► textual app.run()
  ├── send(event) ──► queue for UI thread
  ├── approve(tool_call) ──► async dialog
  └── stop() ──► app.exit()

WebSocketTransport (async, FastAPI)
  ├── runtime: AgentRuntime
  ├── handle(ws, session_id, model, workspace)
  │     └── runtime.get_or_create_session()
  ├── receive_message(ws, message)
  │     └── runtime.run_turn(session, prompt)
  │           └── yield events → ws.send_json()
  └── disconnect(ws)

SSETransport (async, server-sent events)
  └── Similar to WebSocket but over HTTP SSE

HeadlessTransport (async, no UI)
  └── For server-side / API execution
```

**Wiring Issue #4:** `WebSocketTransport.approve()` returns `True` (auto-approve). Real approval requires bidirectional messaging which isn't fully implemented. The `ServerTransport` shim has `_request_approval()` but it's not wired into the main flow.

### 2.5 Multi-Agent Wiring (SubagentOrchestrator)

```
WispAgentCore.turn()
  └── ToolExecutor.execute("spawn_subagent", {...})
        └── SubagentOrchestrator.run(contract)
              ├── BudgetTracker.check()
              ├── ResultCache.get(contract)
              ├── WorktreeManager.create_worktree() [optional]
              ├── SubagentRunner.run(contract, workspace, system_prompt)
              │     ├── _build_child_config(contract, workspace)
              │     ├── UnifiedStore.create_session()
              │     ├── _run_agent(contract, child_cfg, session, system_prompt)
              │     │     ├── WispAgentCore(config=child_cfg, session=session)
              │     │     │     └── run_task(task_description, workspace, max_iterations, timeout)
              │     │     │           └── turn() [same code path as parent!]
              │     │     └── Collect tool calls, files changed, token estimates
              │     └── Return SubagentResult
              ├── ResultCache.set(contract, result)
              ├── BudgetTracker.record(tokens)
              ├── Persistence.save(contract, result)
              └── Telemetry.record(model, result)
```

**Wiring Issue #5:** The nested event loop anti-pattern. `SubagentRunner._run_agent_sync()` creates `asyncio.new_event_loop()` inside a thread (via `asyncio.to_thread()`). This is documented as "necessary because _run_agent calls async code that may spawn subagents." This is a fundamental async architecture problem.

**Wiring Issue #6:** Subagent depth/branch guards exist in TWO places: `WispAgentCore._spawn_subagent()` AND `SubagentOrchestrator.run()`. The first check prevents the tool call; the second is redundant but "safe." This is defensive programming but creates maintenance burden.

### 2.6 Extension Host Wiring

```
ExtensionHost
  ├── PluginExtension()
  │     └── tools() → from wisp.plugins
  ├── HookExtension(manager=HookManager)
  │     ├── intercept(event) → HookManager.pre_tool_call()
  │     └── tools() → []
  ├── MCPExtension(workspace, manager=MCPManager)
  │     ├── tools() → MCPManager.list_tools()
  │     └── intercept(event) → MCP tool execution
  └── SkillExtension(workspace)
        └── tools() → discovered skill schemas
```

**Wiring Issue #7:** `HookManager` is shared between `HookExtension` (for intercept) and `ToolExecutor` (for pre/post hooks). This creates implicit coupling — the hook manager state affects both extension interception and tool execution lifecycle.

---

## 3. Data Flow Analysis

### 3.1 Normal Turn Flow (CLI Mode)

```
User Input
    ↓
CLITransport.recv() → prompt string
    ↓
AgentRuntime.run_turn(session, prompt, approval_handler=transport.approve)
    ↓
  [Lock acquired for session]
    ↓
WispAgentCore.turn(session, prompt, approval_handler)
    ↓
_build_system_prompt() → assembles context from:
  - rules.md (project conventions)
  - skills (discovered agents)
  - repo map (file structure)
  - git context (status, diff)
  - memory (recalled facts)
  - compaction history
    ↓
Provider.generate_stream_events(system_prompt, messages, tools)
    ↓
OllamaProvider → HTTP POST /api/chat → SSE stream
    ↓
For each SSE event:
  ├── content → yield {"type": "content", "text": ...}
  ├── tool_call → SecurityPolicy.check() → ExtensionHost.intercept()
  │               → ToolExecutor.execute() → yield {"type": "tool_result", ...}
  └── done → yield {"type": "done"}
    ↓
CLITransport.send(event) → _render_event() → stdout
    ↓
Session saved to UnifiedStore
```

### 3.2 Subagent Spawn Flow

```
LLM emits tool_call: "spawn_subagent"
    ↓
ToolExecutor.execute("spawn_subagent", args)
    ↓
SubagentOrchestrator.run(contract)
    ↓
SubagentRunner.run(contract, workspace, system_prompt)
    ↓
_build_child_config() [deep copy parent config + overrides]
    ↓
WispAgentCore.run_task(task_description, workspace, max_iterations, timeout)
    ↓
[NEW event loop in thread] ← ANTI-PATTERN
    ↓
Same turn loop as parent agent
    ↓
Result collected → SubagentResult
    ↓
Return to parent as tool_result
```

### 3.3 Server Mode Flow (WebSocket)

```
Client connects via WebSocket
    ↓
FastAPI route → WebSocketTransport.handle(ws, session_id, model, workspace)
    ↓
AgentRuntime.get_or_create_session(session_id, model, workspace)
    ↓
Client sends {"type": "user", "text": "..."}
    ↓
WebSocketTransport.receive_message(ws, message)
    ↓
AgentRuntime.run_turn(session, prompt)
    ↓
Events streamed back via ws.send_json()
    ↓
Client receives and renders
```

---

## 4. Logical Connectivity Assessment

### 4.1 ✅ Correctly Wired Components

| Component Pair | Connection Type | Assessment |
|----------------|-----------------|------------|
| `CompositionRoot` → `AgentRuntime` | Constructor injection | ✅ Clean DI, all deps explicit |
| `AgentRuntime` → `WispAgentCore` | Factory pattern (`core_factory`) | ✅ Stateless core, runtime owns lifecycle |
| `WispAgentCore` → `Provider` | ABC + Factory | ✅ Swappable providers |
| `WispAgentCore` → `ToolExecutor` | Constructor injection | ✅ Executor is dependency, not mixin |
| `ToolExecutor` → `SecurityPolicy` | Constructor injection | ✅ Policy is injected, not global |
| `ExtensionHost` → extensions | Registry pattern | ✅ Unified interface, fail-closed |
| `UnifiedStore` → SQLite | Thread-local connections | ✅ WAL mode, concurrent-safe |
| `ServiceRegistry` → services | Dependency-order lifecycle | ✅ start()/stop() with timeout |

### 4.2 ⚠️ Wiring Issues (Anti-Patterns)

#### Issue 1: Circular Dependency — ToolExecutor ↔ SubagentOrchestrator

**Location:** `composition.py:165-170`

```python
# Phase 2: inject tool_executor into orchestrator's runner
self.subagent_orchestrator._runner._tool_executor = self.tool_executor
```

**Problem:** Two-phase initialization with direct attribute mutation. If `start()` is called before phase 2, subagents fail. The circular dependency indicates these components should be refactored — perhaps `ToolExecutor` shouldn't need to spawn subagents directly, or subagent spawning should be a higher-level concern.

**Severity:** Medium — works in practice but fragile to initialization order changes.

**Fix:** Extract a `SubagentSpawner` interface that both can depend on, or make subagent spawning a runtime concern (AgentRuntime coordinates both).

#### Issue 2: Nested Event Loops in SubagentRunner

**Location:** `multi_agent/_runner.py` (implied by MULTI_AGENT_WIRING.md)

**Problem:** `asyncio.to_thread()` runs sync code in a thread. But `_run_agent()` is async, so it creates `asyncio.new_event_loop()` inside the thread. This is a well-known asyncio anti-pattern that can cause:
- Thread starvation
- Resource leaks (loops not properly closed)
- Debugging nightmares (which loop am I on?)

**Severity:** High — can cause hangs, resource exhaustion, and test flakiness.

**Fix:** Run subagents directly in the parent's event loop using `asyncio.timeout()` and `asyncio.gather()`. The `SubagentRunner` already claims "No nested event loops" in its docstring but the implementation may still have legacy paths.

#### Issue 3: Config Cache Invalidation

**Location:** `core/runtime.py:_get_core()`

**Problem:** Core instance is cached with `threading.Lock` but no config version check. If `WispConfig` is mutated between turns, the cached core uses stale config.

**Severity:** Low-Medium — config is typically static after startup.

**Fix:** Add a config fingerprint (hash of relevant fields) to the cache key, or make config immutable.

#### Issue 4: WebSocket Approval Not Implemented

**Location:** `transport/websocket.py:approve()`

```python
async def approve(self, tool_call: dict) -> bool:
    """... For now, auto-approve."""
    return True
```

**Problem:** Server mode cannot do interactive approval. All tool calls are auto-approved, which is a security concern.

**Severity:** High for production server deployments.

**Fix:** Implement bidirectional approval flow: send `approval_request` event → wait for client response → proceed. The `ServerTransport` shim has `_request_approval()` but it's not wired.

#### Issue 5: Dual Entry Point Paths

**Location:** `__main__.py` vs `entry.py`

**Problem:** `__main__.py` has both legacy direct paths (`cmd_tui` creates config directly) and new `entry.py` paths. The TUI path bypasses `CompositionRoot` entirely.

**Severity:** Medium — creates maintenance burden, risk of divergent behavior.

**Fix:** Consolidate all entry points through `entry.py` → `CompositionRoot`. The TUI should use the same DI path.

#### Issue 6: HookManager Shared Between Extension and ToolExecutor

**Location:** `composition.py:130-140`

**Problem:** `HookManager` is created once and shared between `HookExtension` (intercept path) and `ToolExecutor` (pre/post tool hooks). This creates implicit coupling where hook state affects both interception and execution.

**Severity:** Low — works correctly but violates single-responsibility.

**Fix:** Separate into `InterceptHookManager` and `ToolHookManager`, or clarify the lifecycle in documentation.

#### Issue 7: Redundant Depth/Branch Guards

**Location:** `core/engine.py` + `multi_agent/subagent_orchestrator.py`

**Problem:** Subagent depth and branching limits are checked in both the tool call handler AND the orchestrator. This is defensive but creates maintenance burden — changing limits requires updating two places.

**Severity:** Low — harmless redundancy.

**Fix:** Centralize guards in `SubagentOrchestrator` only. The tool handler should trust the orchestrator to enforce limits.

---

## 5. Cross-Cutting Concerns Analysis

### 5.1 Security Wiring

```
SecurityPolicy (PermissionMode + RuleEngine)
  ├── PermissionMode: FULL | ASK_ALL | AUTO_EDIT | READ_ONLY
  ├── PriorityRuleEngine (composable rules)
  │     ├── Safe read tool whitelist
  │     ├── Ask-all block list
  │     ├── Auto-edit block list
  │     └── Custom rules (add_rule)
  ├── Trusted workspace check
  ├── Hook evaluation
  └── Audit trail (ImmutableAuditTrail → SQLite)

ToolExecutor security flow:
  1. Pre-tool hooks (HookManager)
  2. Plan mode guard
  3. Dangerous command block (regex patterns)
  4. Permission mode guard (SecurityPolicy)
  5. Approval gating (interactive or auto)
  6. Post-tool audit logging
```

**Assessment:** ✅ Security is well-layered with defense in depth. Each layer can block independently. The fail-closed design (deny on error) is correct.

### 5.2 Telemetry Wiring

```
Telemetry (infra/telemetry.py)
  ├── Metrics collection (counters, histograms)
  ├── Tracing (trace IDs, spans)
  ├── Health checks
  └── Structured logging (JSON format option)

Integration points:
  - AgentRuntime: turn duration, event counts
  - ToolExecutor: tool execution duration, success/failure
  - SubagentOrchestrator: subagent metrics, token usage
  - Provider: LLM latency, token counts
```

**Assessment:** ✅ Telemetry is properly decoupled. No business logic depends on telemetry.

### 5.3 Persistence Wiring

```
UnifiedStore (SQLite)
  ├── sessions table (id, model, workspace, messages, compaction_history)
  ├── runs table (id, session_id, prompt, status)
  ├── events table (id, run_id, type, data)
  ├── memory table (id, content, importance)
  ├── background_runs table
  └── session_events table (idempotency, replay)

Thread safety: threading.local() connections + WAL mode
Backup: JSONL fallback for audit trail
```

**Assessment:** ✅ Single source of truth, proper schema, concurrent-safe. The thread-local connection pattern is correct for SQLite.

---

## 6. Module Dependency Graph

### 6.1 Core Dependency Tree

```
wisp/
├── __main__.py ──────┐
├── entry.py ─────────┤
│                     ▼
│              composition.py
│                     │
│         ┌───────────┼───────────┐
│         ▼           ▼           ▼
│    core/runtime.py  infra/*   transport/*
│         │           │           │
│         ▼           ▼           ▼
│    core/engine.py  tool_executor.py
│         │              │
│         ▼              ▼
│    providers/     multi_agent/
│                   subagent_orchestrator.py
│                        │
│                        ▼
│                   _runner.py
│                        │
│                        ▼
│                   core/engine.py [recursive!]
│
└── server/ ──────────► FastAPI app (separate entry)
```

### 6.2 Circular Dependencies Detected

1. **`core/engine.py` ↔ `multi_agent/_runner.py`**: SubagentRunner creates WispAgentCore instances, which can spawn more subagents.
2. **`tool_executor.py` ↔ `multi_agent/subagent_orchestrator.py`**: ToolExecutor calls orchestrator to spawn subagents; orchestrator's runner may need tool_executor for subagent tools.
3. **`transport/server.py` ↔ `server/main.py`**: ServerTransport is a shim that imports from server routes.

**Assessment:** Circular dependency #1 is by design (recursive agent spawning). #2 is the initialization-order issue. #3 is backward-compatibility debt.

---

## 7. Testability Assessment

### 7.1 Testable Components ✅

| Component | Testability | Reason |
|-----------|-------------|--------|
| `WispAgentCore` | High | Stateless, all deps injected |
| `ToolExecutor` | High | Stateless, single tool at a time |
| `SecurityPolicy` | High | Pure functions, immutable |
| `ProviderFactory` | High | Easy to mock providers |
| `UnifiedStore` | Medium | SQLite in-memory for tests |
| `ExtensionHost` | High | Registry pattern, easy to mock |

### 7.2 Hard-to-Test Components ⚠️

| Component | Testability | Reason |
|-----------|-------------|--------|
| `AgentRuntime` | Low-Medium | Caches core, async locks, idempotency |
| `SubagentOrchestrator` | Low | Spawns real agents, nested loops |
| `CLITransport` | Low | Interactive I/O, signal handlers |
| `CompositionRoot` | Low | Creates everything, hard to mock |
| `ContextAssembler` | Medium | File system dependencies |

---

## 8. Recommendations (Prioritized)

### 🔴 Critical (Fix Before Production)

1. **Eliminate nested event loops in SubagentRunner** — Use `asyncio.timeout()` in parent's loop. This is the #1 source of hangs and resource leaks.

2. **Implement WebSocket approval flow** — Server mode currently auto-approves all tools. Add bidirectional messaging for approval requests.

### 🟡 High (Fix in Next Sprint)

3. **Resolve ToolExecutor ↔ SubagentOrchestrator circular dependency** — Extract a `SubagentSpawner` interface or move spawning to AgentRuntime.

4. **Consolidate entry points** — All modes (CLI, TUI, server, headless) should go through `entry.py` → `CompositionRoot`.

5. **Add config versioning to core cache** — Prevent stale core instances when config changes.

### 🟢 Medium (Technical Debt)

6. **Centralize subagent guards** — Remove duplicate depth/branch checks from `WispAgentCore`.

7. **Separate HookManager instances** — One for extension interception, one for tool lifecycle.

8. **Add integration tests for the full DI graph** — Verify CompositionRoot wires everything correctly.

---

## 9. Architecture Strengths

1. **CompositionRoot pattern** — All wiring in one place, easy to understand and modify.
2. **Transport ABC** — Clean separation between UI and core logic.
3. **Stateless core** — `WispAgentCore` has no mutable state, easy to test and cache.
4. **UnifiedStore** — Single SQLite database replaces 4+ persistence backends.
5. **ExtensionHost** — One interface for plugins, hooks, MCP, skills.
6. **Security layering** — Defense in depth with fail-closed design.
7. **ServiceRegistry** — Proper lifecycle management with dependency ordering.

---

## 10. Summary Matrix

| Layer | Components | Wiring Quality | Issues |
|-------|-----------|----------------|--------|
| Entry Points | `__main__.py`, `entry.py` | 🟡 Medium | Dual paths, TUI bypasses DI |
| Transport | CLI, TUI, WS, SSE | 🟢 Good | WS approval not implemented |
| Agent Core | Runtime, Engine | 🟢 Good | Config cache invalidation |
| Provider | Factory, Ollama | 🟢 Good | — |
| Tools | Registry, Executor | 🟢 Good | — |
| Multi-Agent | Orchestrator, Runner | 🟡 Medium | Nested loops, circular dep |
| Infrastructure | Store, Security, Telemetry | 🟢 Good | — |
| Extensions | Host, Plugins, MCP | 🟢 Good | Shared HookManager |

**Overall Architecture Grade: B+**
- Strong macro-level design ✅
- Clean separation of concerns ✅
- Some micro-level coupling issues ⚠️
- Critical async anti-pattern needs fixing 🔴
