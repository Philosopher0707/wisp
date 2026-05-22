# Architecture Consolidation Gap Report

Assessed against ARCHITECTURE_V2.md target state.  
Generated: 2026-05-21

---

## Gap 1: Dual Cores

### Target (per v2)
Replace the old stateful `WispAgentCore` in `wisp/core/agent.py` with the new stateless `WispAgentCore` in `wisp/core/engine.py`. The new engine receives all state as injected parameters (provider, security, extensions, telemetry).

### Current State
| Aspect | Status |
|--------|--------|
| `wisp/core/agent.py` exists | ⚠️ YES — 65 KB, 1541 lines (old stateful) |
| `wisp/core/engine.py` exists | ✅ YES — 33 KB, 821 lines (new stateless) |
| `wisp/__init__.py` exports | ✅ Uses new `wisp.core.engine.WispAgentCore` |
| `wisp/composition.py` uses | ✅ Uses new `wisp.core.engine.WispAgentCore` |
| `wisp/agent.py` (backward compat) | ⚠️ Imports from `wisp.core.agent` (old core) |
| Deprecation warning in old core | ✅ Present: warns users to use new engine |

### Remaining
1. Delete `wisp/core/agent.py` once all consumers are migrated (see Gap 5).
2. Ensure `wisp/agent.py` is updated to extend or use the new engine instead of the old one.

---

## Gap 2: Transport ABC

### Target (per v2)
One `Transport` ABC in `wisp/transport/base.py`. Three implementations: `CLITransport`, `TUITransport`, `WSTransport` (with `SSETransport` as a 4th).

### Current State
| Component | Status |
|-----------|--------|
| `wisp/transport/base.py` (Transport ABC) | ✅ Fully implemented — 5 abstract methods: `send`, `recv`, `approve`, `start`, `stop` |
| `wisp/transport/cli_v2.py` (CLITransport) | ✅ Fully implements Transport ABC (910 lines) |
| `wisp/transport/tui.py` (TUITransport) | ✅ Implements Transport ABC, but `approve()` returns `False` (deny-all default) with a TODO for a real modal |
| `wisp/transport/websocket.py` (WebSocketTransport) | ✅ Fully implements Transport ABC |
| `wisp/transport/sse.py` (SSETransport) | ✅ Implements Transport ABC (102 lines) |
| `wisp/transport/server.py` (ServerTransport shim) | ✅ Backward-compat shim extending WebSocketTransport |
| `wisp/transport/cli.py` (old shim) | ✅ Backward-compat shim re-exporting from cli_v2 |

### Remaining
1. Implement the TUI approval modal (currently `approve()` always returns `False`).

---

## Gap 3: Old Persistence

### Target (per v2)
Old `UnifiedSessionStore` (from `wisp/session_store.py`) replaced by new `UnifiedStore` (from `wisp/infra/store.py`). All code uses the new store, ideally via CompositionRoot.

### Current State
| Aspect | Status |
|--------|--------|
| `wisp/infra/store.py` (UnifiedStore) | ✅ Fully implemented SQLite store (468 lines) |
| `wisp/composition.py` | ✅ Uses `UnifiedStore` directly |
| `wisp/server/main.py` | ✅ Uses CompositionRoot → UnifiedStore |
| `wisp/adapters.py` (old compat) | ✅ Has backward-compat `UnifiedSessionStore` wrapping `UnifiedStore` |
| `wisp/server/routes/sessions.py` | ⚠️ Tries CompositionRoot first, falls back to `get_store()` from adapters |
| Files still using `get_store()` from adapters | ⚠️ **6 files**: `__main__.py`, `acp_session.py`, `supervisor.py`, `tui/screens/session_picker.py`, `core/agent.py`, `multi_agent/_runner.py` |

### Remaining
1. Migrate the 6 remaining files from `get_store()` (adapters) to CompositionRoot-injected `UnifiedStore`.
2. Remove the backward-compat `UnifiedSessionStore` from `adapters.py` once migration is complete.

---

## Gap 4: Old server.py

### Target (per v2)
No monolithic `wisp/server.py` at top level. Replaced by `wisp/server/main.py` + domain routers in `wisp/server/routes/`.

### Current State
| Aspect | Status |
|--------|--------|
| `wisp/server.py` at top level | ✅ **Does NOT exist** |
| `wisp/server/main.py` (new server) | ✅ FastAPI app with lifespan, CompositionRoot, security middleware, 22 routers |
| `wisp/server/routes/` | ✅ 22 domain routers |
| `wisp/server/__init__.py` | ✅ Backward-compat re-exports |
| `wisp/app_server.py` (WispAppServer) | ⚠️ **Still exists** (3.5 KB, used by tests and exported via server/__init__.py) |
| Tests importing from `wisp.server` | ⚠️ Tests still import `app`, `_run_agent_headless`, `PromptRequest`, `ConnectionManager`, etc. from `wisp.server` (all work via re-exports) |

### Remaining
1. Verify `WispAppServer` in `wisp/app_server.py` is still needed or can be removed.
2. Clean up test imports to use the new routes directly instead of `wisp.server` re-exports.

---

## Gap 5: Old Agent Core References

### Target (per v2)
No files import from `wisp.core.agent` or `wisp.agent` (the old deprecated modules).

### Current State
| File | Import | Status |
|------|--------|--------|
| `wisp/agent.py` | `from wisp.core.agent import WispAgentCore` | ⚠️ Backward-compat wrapper uses old core |
| `wisp/acp_session.py` | `from wisp.agent import WispAgent` | ⚠️ Uses old agent via compat layer |
| `wisp/multi_agent/cli.py` | `from wisp.agent import WispAgent` | ⚠️ Uses old agent |
| `wisp/multi_agent/subagent_orchestrator.py` | `from wisp.agent import WispAgent` | ⚠️ Uses old agent |
| `wisp/__main__.py` | `from wisp.agent import WispAgent` | ⚠️ Entry point uses old agent |
| `wisp/core/agent.py` itself | — | ✅ Has deprecation warning |

### Remaining
1. Migrate all 5 files above to use CompositionRoot + the new engine directly.
2. Then delete `wisp/agent.py` and `wisp/core/agent.py`.

---

## Gap 6: Infra Completeness

### Target (per v2)
Each `wisp/infra/` module should be fully implemented (not stubs), covering store, security, extensions, telemetry, lifecycle, and audit.

### Current State
| Module | Lines | Status | Notes |
|--------|-------|--------|-------|
| `wisp/infra/store.py` | 468 | ✅ Complete | Full SQLite store: sessions, runs, events, memory schemas |
| `wisp/infra/security.py` | 210 | ✅ Complete | 4-layer decision model (mode → trust → hooks → audit) |
| `wisp/infra/extensions.py` | 115 | ✅ Complete | ExtensionHost with register/tools/intercept/lifecycle |
| `wisp/infra/telemetry.py` | 150 | ✅ Complete | Turn/tool counters, health checks, JSON snapshot |
| `wisp/infra/lifecycle.py` | 110 | ✅ Complete | ServiceRegistry with dependency-ordered start/stop |
| `wisp/infra/audit.py` | 94 | ✅ Complete | Append-only hash-chained audit trail to JSONL |

### Remaining
None of the 6 infra modules are stubs — all are fully implemented. No remaining work here.

---

## Summary

| Gap | Title | Progress | Remaining Effort |
|-----|-------|----------|------------------|
| 1 | Dual cores | 70% | Delete old `core/agent.py` after migrating 5 consumers |
| 2 | Transport ABC | 90% | Implement TUI approval modal |
| 3 | Old persistence | 75% | Migrate 6 files from `get_store()` to CompositionRoot |
| 4 | Old server.py | 85% | Clean up `WispAppServer` and test imports |
| 5 | Old agent core refs | 60% | Migrate 5 files, then delete `agent.py` + `core/agent.py` |
| 6 | Infra completeness | 100% | All modules fully implemented |

**Overall architecture migration progress: ~80%**  
The new architecture layers exist and are functional. The remaining work is cleaning up old files and migrating consumers from backward-compat shims to the new interfaces.
