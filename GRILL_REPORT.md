# 🔥 WISP REFACTORED CODEBASE — GRILL REPORT

## Executive Summary

The refactored codebase shows **solid architectural separation** but has **critical bugs**, **memory leaks**, and **missing error handling** that will cause production failures.

---

## 1. CRITICAL BUGS 🚨

### 1.1 Duplicate Session Creation (entry.py)
**Location:** `wisp/entry.py` lines 87–99
**Severity:** HIGH

```python
# Create session (async)
session = asyncio.run(root.runtime.get_or_create_session(...))

# Create session (async)  ← DUPLICATE!
session = asyncio.run(root.runtime.get_or_create_session(...))
```

**Impact:** Every REPL startup calls `get_or_create_session` twice. The second call overwrites the first, potentially losing race conditions or causing unnecessary DB writes.

**Fix:** Remove the duplicate block.

---

### 1.2 Cache Invalidation Missing (engine.py)
**Location:** `wisp/core/engine.py`
**Severity:** HIGH

The engine caches:
- `_assembler_cache` (ContextAssembler instance)
- `_static_prompt_cache` (dict keyed by workspace)

**Problem:** These caches are **never invalidated**. When:
- `.wisp/rules.md` changes
- Skills are added/removed
- Git context changes (new branch, commits)
- Files are added/removed (repo map changes)

The engine continues serving **stale system prompts** indefinitely.

**Impact:** The agent operates with outdated context — wrong skills, stale repo map, old rules.

**Fix:** Add cache invalidation hooks or TTL-based expiration.

---

### 1.3 Core Factory Creates New Instance Per Turn (runtime.py)
**Location:** `wisp/core/runtime.py` line 66
**Severity:** MEDIUM-HIGH

```python
core = self.core_factory()  # NEW instance every turn
```

**Problem:** The stateless core's caches (`_static_prompt_cache`, `_assembler_cache`) are **lost between turns**. Every turn rebuilds the entire system prompt from scratch — skills, repo map, project context, git status. This is **expensive** (21KB of I/O and computation per turn).

**Impact:** 
- Latency: +200–500ms per turn
- CPU waste: Rebuilding repo map, reading files, discovering skills
- No warm-start benefit

**Fix:** Cache the core instance per session or move caches to runtime.

---

## 2. MEMORY LEAKS 🧠

### 2.1 Unbounded Static Prompt Cache
**Location:** `wisp/core/engine.py` line 34
**Severity:** MEDIUM

```python
_static_prompt_cache: dict = field(default_factory=dict, repr=False)
```

**Problem:** Cache key is `(ws,)` — one entry per workspace. In a long-running server with many workspaces, this grows **unbounded**. Each entry holds ~21KB.

**Impact:** 100 workspaces × 21KB = 2.1MB. Not huge, but unbounded growth is a leak pattern.

**Fix:** Use `functools.lru_cache` or TTL cache.

---

### 2.2 Transport Thinking Buffer Not Cleared on Exception
**Location:** `wisp/transport/cli_v2.py`
**Severity:** LOW-MEDIUM

```python
try:
    async for event in self.runtime.run_turn(session, prompt):
        self._render_event(stdout, event)
    self._flush_thinking(stdout)
    self._flush_content(stdout)
except Exception as exc:
    # Buffers are NOT reset here!
    stdout.write(f"Error: {exc}\n")
```

**Problem:** If an exception occurs mid-turn, `_thinking_buffer` and `_content_buffer` are **not cleared**. On the next turn, stale content bleeds through.

**Fix:** Add `self._reset_buffers()` in the except block.

---

### 2.3 Session Messages Grow Unbounded
**Location:** `wisp/core/runtime.py`
**Severity:** MEDIUM

```python
session["messages"].append({"role": "user", "content": prompt})
# ... turn execution ...
session["messages"].append({"role": "assistant", "content": ...})
```

**Problem:** Messages are appended but never trimmed by runtime. The `maybe_compact` exists but is **never called** during `run_turn`.

**Impact:** Long sessions will exceed context window and cause provider errors.

**Fix:** Call `maybe_compact` before each turn or implement automatic trimming.

---

## 3. SECURITY GAPS 🔒

### 3.1 Security Check Happens After Event Yielded
**Location:** `wisp/core/engine.py` lines 67–82
**Severity:** MEDIUM

```python
for event in self.provider.generate_stream_events(...):
    normalized = self._normalize_event(event)
    
    # Event is already yielded to transport BEFORE security check!
    if normalized.get("type") == "tool_call":
        decision = self.security.check(action, context)
        if not decision.allowed:
            yield error_event  # Only yields error AFTER tool_call was already yielded
```

**Problem:** The `tool_call` event is **yielded to the transport before security is checked**. The user sees "🔧 run_bash" before the "Blocked: READ_ONLY" error.

**Impact:** UI shows a tool call that was never executed — confusing and potentially alarming.

**Fix:** Check security BEFORE yielding the tool_call event.

---

### 3.2 Tool Execution Bypasses Security
**Location:** `wisp/core/engine.py` lines 295–315
**Severity:** HIGH

```python
async def _execute_tool(self, event: dict, session: dict):
    # NO security check here!
    result = execute_tool(name, args, workspace=workspace)
```

**Problem:** `_execute_tool` does **not** call `security.check()`. It relies on the check happening earlier in the loop. But if a tool call comes from:
- Extension intercept
- Direct tool result injection
- Mock provider in tests

It bypasses security entirely.

**Impact:** READ_ONLY mode can be bypassed if tool calls are injected post-security-check.

**Fix:** Add security check inside `_execute_tool`.

---

### 3.3 No Audit Log Exposure
**Location:** `wisp/infra/security.py`
**Severity:** LOW

The `SecurityPolicy` maintains an `_audit_log` but there's **no way to access it** from the runtime or transport. Security violations are logged but never surfaced to the user or admin.

**Fix:** Expose `audit_log()` via runtime or telemetry.

---

## 4. DATA FLOW ISSUES 🌊

### 4.1 Tool Results Not Added to Session Messages
**Location:** `wisp/core/runtime.py`
**Severity:** HIGH

```python
async for event in core.turn(session, prompt):
    yield event
    if event.get("type") == "content":
        assistant_content.append(event.get("text", ""))

# Add assistant message
if assistant_content:
    session["messages"].append({"role": "assistant", "content": "".join(assistant_content)})
```

**Problem:** Tool results are **never added to session messages**. The LLM sees:
1. User message
2. Assistant message (content only)

But **not**:
- Tool calls made
- Tool results returned

**Impact:** The LLM has **no memory of tool execution**. On the next turn, it doesn't know what files were read, what bash commands returned, etc. This breaks multi-turn reasoning.

**Fix:** Add tool_call and tool_result messages to session.

---

### 4.2 Event Normalization Is Fragile
**Location:** `wisp/core/engine.py` lines 318–332
**Severity:** MEDIUM

```python
def _normalize_event(self, event: Any) -> dict:
    if isinstance(event, dict):
        return dict(event)
    result: dict[str, Any] = {}
    if hasattr(event, "type"):
        result["type"] = event.type
    elif hasattr(event, "phase"):
        result["type"] = event.phase
    else:
        result["type"] = "unknown"
    if hasattr(event, "__dict__"):
        result.update(event.__dict__)  # ← Exposes ALL internal attributes!
```

**Problem:** `result.update(event.__dict__)` copies **all** internal attributes of the provider's event object. This may include:
- Internal state
- Circular references
- Non-serializable objects
- Sensitive data

**Impact:** Transport receives polluted events with unexpected fields.

**Fix:** Whitelist known fields instead of copying `__dict__`.

---

### 4.3 Missing Event Types in Transport
**Location:** `wisp/transport/cli_v2.py`
**Severity:** LOW-MEDIUM

The transport handles: THINKING, CONTENT, TOOL_CALL, TOOL_RESULT, DONE, ERROR, SYSTEM, APPROVAL_REQUEST, STEERING_PAUSED, STEERING_RESUMED, STEERING_INJECT

But **not**:
- `checkpoint` — internal telemetry events may leak
- `stream_complete` — may cause premature turn end
- Custom event types from extensions

**Fix:** Add a default handler for unknown event types.

---

## 5. ASYNC SAFETY ⚡

### 5.1 asyncio.run Nesting Risk
**Location:** `wisp/entry.py` lines 87, 94, 144
**Severity:** MEDIUM

```python
session = asyncio.run(root.runtime.get_or_create_session(...))
# ... later in loop ...
asyncio.run(_turn())
```

**Problem:** `asyncio.run()` creates and destroys an event loop each time. If called from an already-running async context (e.g., server, test), it raises `RuntimeError`.

**Impact:** Cannot use REPL inside async tests or async server handlers.

**Fix:** Use `asyncio.get_event_loop().run_until_complete()` or require async entry.

---

### 5.2 Thread Safety in Transport
**Location:** `wisp/transport/cli_v2.py` lines 385–405
**Severity:** MEDIUM

```python
def _reader() -> None:
    while not stop_event.is_set():
        line = stdin.readline()
        # ...
        loop.call_soon_threadsafe(_put, prompt)
```

**Problem:** `_put` is a nested function captured by the thread. `call_soon_threadsafe` expects a callback that takes no arguments, but `_put` takes one. This works by accident because `call_soon_threadsafe` passes the item as an argument... wait, actually `call_soon_threadsafe(callback, *args)` — so it's correct.

But the real issue: **no backpressure**. The queue grows unbounded if the async loop is slow.

**Fix:** Add queue size limit or backpressure mechanism.

---

## 6. ERROR HANDLING 🛡️

### 6.1 Runtime Has No Try/Except Around Core Turn
**Location:** `wisp/core/runtime.py` lines 57–78
**Severity:** HIGH

```python
async def run_turn(self, session: dict, prompt: str) -> AsyncIterator[dict]:
    session["messages"].append({"role": "user", "content": prompt})
    
    core = self.core_factory()
    async for event in core.turn(session, prompt):
        yield event
    
    # If core.turn() raises, we never reach here
    session["messages"].append({"role": "assistant", ...})
```

**Problem:** If `core.turn()` raises an exception:
1. User message is already in session
2. Assistant message is **never** added
3. Session is saved with an **unpaired user message**
4. Next turn sees this orphan message and may misinterpret it

**Impact:** Session corruption, confusing LLM behavior.

**Fix:** Wrap in try/finally to ensure cleanup.

---

### 6.2 Engine Doesn't Handle Provider Exceptions
**Location:** `wisp/core/engine.py` lines 57–105
**Severity:** HIGH

```python
for event in self.provider.generate_stream_events(...):
    # If provider raises mid-stream, the loop aborts
    # No partial results are preserved
```

**Problem:** If the provider raises an exception (network error, timeout, rate limit), the entire turn aborts. No partial content is returned.

**Impact:** User sees nothing — not even a partial response.

**Fix:** Wrap provider iteration in try/except, yield partial content on error.

---

### 6.3 Tool Execution Doesn't Handle Async Tools
**Location:** `wisp/core/engine.py` lines 295–315
**Severity:** MEDIUM

```python
result = execute_tool(name, args, workspace=workspace)
```

**Problem:** `execute_tool` is called **synchronously**. But some tools (like `run_bash` with async subprocess, `web_fetch` with async HTTP) may be async.

**Impact:** Blocking the event loop, potential deadlock.

**Fix:** Check if tool is async and await it.

---

## 7. OUTPUT STRUCTURE 📐

### 7.1 Inconsistent Event Formats
**Location:** Across codebase
**Severity:** MEDIUM

The codebase uses **three** event formats:
1. **AgentEvent** (dataclass): `AgentEvent(type=EventType.CONTENT, data={"text": "..."})`
2. **Flat dict**: `{"type": "content", "text": "..."}`
3. **Nested dict**: `{"type": "content", "data": {"text": "..."}}`

**Problem:** The transport's `_render_event` has normalization logic, but other consumers (server, headless) may not.

**Impact:** Event consumers receive inconsistent data.

**Fix:** Standardize on ONE format everywhere.

---

### 7.2 Tool Result Format Inconsistent
**Location:** `wisp/core/engine.py` line 312
**Severity:** MEDIUM

```python
yield {
    "type": "tool_result",
    "name": name,
    "result": result,  # May be dict, str, or ToolError
    "duration_ms": duration_ms,
}
```

**Problem:** `result` is passed raw from `execute_tool`. It may be:
- A dict (success)
- A string (legacy)
- A ToolError (exception)
- JSON string (from some tools)

The transport expects a specific format for diff rendering.

**Fix:** Normalize tool results to a standard schema.

---

## 8. TEST COVERAGE GAPS 🧪

### 8.1 Missing Tests
| Component | Tests | Gap |
|-----------|-------|-----|
| Engine | `test_core_stateless.py` (8 tests) | No cache invalidation tests |
| Runtime | `test_agent_runtime.py` | No error recovery tests |
| Transport | `test_transport_cli.py` | No multi-turn tests |
| Integration | `test_integration_e2e.py` | No security bypass tests |
| Entry | NONE | No entry.py tests at all |

### 8.2 No Performance Tests
- System prompt build time
- Cache hit/miss ratio
- Memory usage over long sessions

---

## 9. RECOMMENDATIONS ✅

### Immediate (P0)
1. **Fix duplicate session creation** in `entry.py`
2. **Add try/finally in runtime.run_turn** for session cleanup
3. **Add security check inside `_execute_tool`**
4. **Cache core instance** in runtime instead of factory-per-turn

### Short-term (P1)
5. **Add cache invalidation** for system prompt caches
6. **Add tool results to session messages**
7. **Standardize event format** across all layers
8. **Add default event handler** in transport

### Long-term (P2)
9. **Add performance benchmarks**
10. **Implement backpressure** in transport queue
11. **Add audit log exposure** via telemetry
12. **Add session locking** for concurrent turns

---

## Scorecard

| Category | Score | Notes |
|----------|-------|-------|
| Architecture | B+ | Clean separation, good abstractions |
| Correctness | C | Critical bugs in data flow |
| Security | B | Good policy, gaps in enforcement |
| Performance | C | Cache thrashing, no warm-start |
| Error Handling | D | Missing try/finally, no recovery |
| Testing | C | Good unit tests, missing integration |
| Memory Safety | C | Unbounded caches, buffer leaks |
| Async Safety | B | Minor nesting risks |

**Overall: C+ — Solid foundation, needs hardening for production.**
