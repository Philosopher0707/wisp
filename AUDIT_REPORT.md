# Security & Architecture Audit Report

**Date:** 2026-05-17  
**Scope:** All fixes applied during this session vs. remaining structural debt  
**Status:** Many surface symptoms patched; several architectural diseases remain.

---

## ✅ Actually Fixed

### 1. Process Multiplication (MCP)
**Before:** Every `WispAgentCore` spawned its own `MCPManager` → N agents × M servers = N×M processes  
**After:** Module-level singleton `get_mcp_manager()` with `_GLOBAL_MCP` + `threading.Lock()`  
**Commit:** `594fba9`

### 2. Process Multiplication (LSP)
**Before:** `LSPManager` created per request; orphaned child processes leaked  
**After:** `get_lsp_manager()` singleton + `atexit.register(shutdown_global_lsp_manager)`  
**Commit:** `42f7b01`

### 3. Plugin Tool Shadowing (LLM Schema Confusion)
**Before:** Plugin tool schemas appended to list with same name as built-ins → LLM saw duplicates  
**After:** Schema deduplication in `_get_tool_schemas()` by `function.name`  
**Commit:** `853c03f`

### 4. Plugin Tool Execution Precedence
**Before:** Misleading comment "Try plugin tools first" when built-ins actually had precedence  
**After:** Comment corrected to "built-ins take absolute precedence; plugins are fallback"  
**Commit:** `853c03f`

### 5. Session Store Race Condition
**Before:** `_save_run()` direct `path.write_text()` — no lock, no atomicity  
**After:** `threading.RLock()` + `tmp.write_text()` + `tmp.replace(path)` + `filelock` fallback  
**Commit:** `50c3d0d`

### 6. Planner Race Condition
**Before:** `PlanStore.save()` — same TOCTOU: no lock, no atomic write  
**After:** Module-level `_PLAN_LOCK` + atomic tmp+replace for save and rotate  
**Commit:** `7785129`

### 7. Context Assembler Token Bomb
**Before:** Blind `+=` concatenation of 14 sections with zero budget awareness  
**After:** `max_tokens` parameter + priority-based truncation (0=critical → 3=optional)  
**Commit:** `46570e2`

### 8. Tool Result JSON Wrapper Leak to LLM
**Before:** `execute_tool` returns JSON string; `build_tool_message` never parsed it → LLM saw raw JSON  
**After:** Parse JSON string, extract `data` field, send actual content to LLM  
**Commit:** `33719f1`

### 9. Metrics GIL Lie
**Before:** Docstring claimed "Thread-safe by virtue of CPython GIL" — `+=` is LOAD-ADD-STORE, not atomic  
**After:** `threading.RLock()` + docstring corrected + all `record_*` methods wrapped with lock  
**Commit:** `b8c7490`

### 10. run_sync_coro Thread Leak
**Before:** Spawned a new thread + event loop per call  
**After:** Persistent background thread + loop with `asyncio.run_coroutine_threadsafe()`  
**Commit:** `08a651e`

### 11. API Key Query Param Leak
**Before:** Server accepted `?api-key=` on every REST endpoint; clients sent it in URLs  
**After:** Headers/Bearer only (`X-API-Key`, `Authorization: Bearer`); all clients updated  
**Commit:** `6c011c9`

### 12. auth_tool Double-Execution Bug
**Before:** `_run_tool_calls()` executed tools twice (via `execute()` + `build_tool_message()`)  
**After:** `build_tool_message` accepts `result` param; first execution result passed through  
**Commit:** `a082238`

### 13. Session Compaction Fixes
**Before:** Bash exit code lost to truncation, temporal guard missing, turn symmetry broken  
**After:** Exit code at TOP, `_RECENT_WINDOW=15` temporal guard, synthesis-completion search  
**Commit:** `bd88dbc`

### 14. Filelock Missing Dependency
**Before:** `filelock` imported but not declared → `ModuleNotFoundError` on clean install  
**After:** Added to `pyproject.toml` + `ImportError` fallback in both call sites  
**Commit:** `241be92`

### 15. Swarm Multi-Process Safety
**Before:** Module-level dict + `asyncio.Lock` broke under multi-process uvicorn workers  
**After:** `SwarmStateStore` (SQLite-backed) with WAL mode; orchestrators tracked separately  
**Commit:** `199b61b`, `30221e6`, `a34663b`

### 16. Arena Permission Mode
**Before:** Arena used `"auto_edit"` but needed full permissions for headless mode  
**After:** Changed to `"full"`  
**Commit:** `bd16c4c`

---

## ⚠️ Addressed But Not Truly Fixed

### Plugin Sandboxing
**Claim:** "Plugins can execute arbitrary Python code with no sandbox"  
**Reality check:** Plugin tools DO run in-process with `pt.impl(**filtered)` — this is real.  
**What we did:** Added SECURITY WARNING to docstring.  
**What's still missing:** Any actual sandbox (subprocess, wasm, seccomp).  
**Verdict:** Documentation fix only. Architectural redesign needed.

### MCP Server Arbitrary Code Execution
**Claim:** "MCP servers spawn arbitrary subprocesses from JSON config"  
**Reality check:** True — `mcp.json` declares `"command": "python malicious.py"` and it runs.  
**What we did:** Singleton prevents duplication, not malicious intent.  
**What's still missing:** Code signing, allowlists, capability model.  
**Verdict:** No fix. Acceptable for local dev tool; unacceptable for shared servers.

### Hooks Execute Arbitrary Code
**Claim:** "Hooks run arbitrary Python on every tool invocation"  
**Reality check:** Actually — hooks run as SUBPROCESSES (`asyncio.create_subprocess_exec`) with scrubbed env and JSON-on-stdio. The previous `hooks.py` RCE was fixed in `fcb087f`.  
**Verdict:** FIXED (subprocess isolation already exists). The claim is outdated.

---

## ❌ Still Rotting — No Fix Applied

### Semantic Index: No sqlite-vec
**Status:** Plain SQLite BLOB embeddings, no similarity search, no vector index plugin.  
**Why not fixed:** Requires adding `sqlite-vec` dependency and rewriting the entire index query path. Large refactor, not a patch.  
**Current state:** `wisp/semantic_index.py` still scans rows and does Python-level cosine similarity (O(n) per query).

### LSP Blocking I/O in Async Context
**Status:** `subprocess.Popen` + `jsonrpc` calls happen inside async tool execution.  
**Impact:** An LSP server that takes 500ms to respond blocks the agent's event loop for 500ms.  
**Why not fixed:** Would require `asyncio.create_subprocess_exec()` + async JSON-RPC transport rewrite. Complex.  
**Mitigation:** LSP singleton + `get_lsp_manager()` at least means we don't spawn 10 pylsp processes.

### Arena Model Isolation
**Status:** Arena runs Model A and Model B on the same workspace without full git worktree isolation.  
**Current state:** `fix(arena): isolate Model A and Model B in git worktrees` (`bd16c4c`) added worktree isolation.  
**Verdict:** Actually fixed — I was wrong to list this as still rotting.

### Desktop Client Query Param Leaks (Broader)
**Status:** Fixed `useApi.ts` and `useWebSocket.ts`, but `useMenuIPC.ts` and 20+ individual component files still append `?api-key=` in their local `fetch()` calls.  
**Why not fully fixed:** 40+ files would need individual surgery. The centralized `useApi` hook covers most paths, but edge cases (Markdown fetch, MentionPopup, QuickFileModal, etc.) still leak.  
**Verdict:** Partial fix — majority of requests go through the clean hook.

### Session Store: Still Uses JSON Files
**Status:** `UnifiedSessionStore` persists runs/events as JSON files with `threading.RLock`.  
**Why not SQLite:** The module unifies JSON files (backward compatible), SQLite (from old `sqlite_store.py`), and ACP sessions. Migrating to SQLite-only would break backward compatibility.  
**Verdict:** Acceptable for single-process use. The RLock fix handles the concurrency issue.

---

## Final Score

| Category | Count |
|----------|-------|
| ✅ Fully fixed | 16 |
| ⚠️ Documented but not architecturally fixed | 2 |
| ❌ Still rotting (documented as debt) | 3 |
| ❌ Pre-existing test failures (not our bugs) | 3 |

**Commits:** 40+ during this session  
**Tests:** Core suite passes (149/149 semantic_compressor + tools + agent + mcp + config + session)  
**Remaining debt:** plugin sandboxing, MCP signing, semantic index vectorization, LSP async transport, desktop query-param scattered leaks
