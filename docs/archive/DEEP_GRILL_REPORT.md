# 🔥 DEEP GRILL REPORT — Post-P0/P1 Fixes

## Executive Summary

After fixing P0 and P1 bugs, 10 **medium-to-high severity** issues remain.
These are edge cases, race conditions, and subtle bugs that will bite in production.

---

## 🚨 HIGH SEVERITY

### 1. Race Condition on `_core_cache` (runtime.py)
**Location:** `wisp/core/runtime.py` line 165

```python
def _get_core(self) -> Any:
    if self._core_cache is None:
        self._core_cache = self.core_factory()  # ← Race!
    return self._core_cache
```

**Problem:** Two concurrent turns can both see `_core_cache is None` and create
two separate core instances. One wins, the other is orphaned. Worse: if one
core has cached prompts and the other doesn't, you get inconsistent behavior.

**Impact:** In server mode with concurrent requests, each request may get a
different core instance — cache thrashing, memory bloat, inconsistent context.

**Fix:** Add `threading.Lock` around cache access.

---

## ⚠️ MEDIUM SEVERITY

### 2. Session Dict Mutated Without Lock (runtime.py)
**Location:** `wisp/core/runtime.py` lines 75–78, 121–145

```python
session["messages"].append({"role": "user", "content": prompt})
# ... async iteration ...
session["messages"].append({"role": "assistant", ...})
```

**Problem:** `session` is a plain dict. Two concurrent `run_turn()` calls on
the same session interleave their appends, corrupting the message list.

**Impact:** Messages from different turns get interleaved. The LLM sees:
```
user: hello
user: how are you  ← from concurrent turn
assistant: response to hello
```

**Fix:** Add per-session lock or use `asyncio.Lock`.

---

### 3. Exception Swallowing in Security Check (engine.py)
**Location:** `wisp/core/engine.py` lines 89–90, 103–104

```python
except Exception as e:
    logger.warning("Security check failed: %s", e)
    # ← Continues as if security passed!
```

**Problem:** If `security.check()` raises (e.g., database error, hook crash),
the exception is logged and **execution continues**. The tool call proceeds
without security validation.

**Impact:** A buggy hook or database outage silently bypasses security.

**Fix:** Treat security exceptions as "deny" — yield error and continue.

---

### 4. No JSON Handling for Tool Results (engine.py)
**Location:** `wisp/core/engine.py` line 379

```python
result = execute_tool(name, args, workspace=workspace)
# result may be: dict, str, bytes, Path, Exception, None
```

**Problem:** Tool results are passed raw to the transport. If a tool returns:
- A `Path` object → serialization fails
- A `bytes` object → JSON encoding fails
- A custom class → `TypeError` on JSON serialization
- `None` → transport may crash

**Impact:** Server mode returns 500 errors. CLI shows raw Python reprs.

**Fix:** Normalize tool results to a standard JSON-serializable schema.

---

### 5. Reader Thread Never Joined (cli_v2.py)
**Location:** `wisp/transport/cli_v2.py` lines 410–411

```python
reader_thread = threading.Thread(target=_reader, daemon=True)
reader_thread.start()
# ...
finally:
    stop_event.set()  # ← Signals stop but doesn't wait
```

**Problem:** The daemon thread is signaled to stop but never `join()`ed.
If the thread is blocked on `stdin.readline()`, it may not exit until
EOF. On rapid REPL start/stop cycles, threads accumulate.

**Impact:** Thread leak on rapid REPL restarts. Not fatal (daemon threads
don't block shutdown), but messy.

**Fix:** Add `reader_thread.join(timeout=1.0)` in finally.

---

### 6. Tool Result Format Inconsistency (engine.py)
**Location:** `wisp/core/engine.py` lines 360–379

```python
yield _flatten_event(tool_result_event(
    name,
    {"status": "error", "data": f"Security blocked: {decision.reason}"},
    duration_ms=0,
))
# vs
yield _flatten_event(tool_result_event(name, result, duration_ms=duration_ms))
```

**Problem:** Security block yields `{"status": "error", "data": "..."}`
but successful execution yields the raw tool result (could be anything).
The transport can't distinguish success from failure without guessing.

**Impact:** Transport shows ✓ for errors, ✗ for success, depending on
result shape.

**Fix:** Always wrap tool results in a standard schema:
```python
{"status": "ok" | "error", "data": ..., "metadata": {...}}
```

---

### 7. Missing Input Validation (runtime.py)
**Location:** `wisp/core/runtime.py` lines 42–44

```python
async def get_or_create_session(
    self, session_id: str, model: str, workspace: str
) -> dict:
```

**Problem:** No validation on inputs:
- `session_id = ""` → creates session with empty ID
- `session_id = None` → crashes on `self.store.load_session(None)`
- `model = ""` → passes empty model to provider
- `workspace = None` → crashes on `Path(None)`

**Impact:** Invalid inputs cause cryptic crashes deep in the stack.

**Fix:** Add validation at entry points.

---

### 8. Hardcoded Values (engine.py, runtime.py)
**Location:** Multiple

```python
max_tokens=1200        # engine.py — repo map budget
max_messages: int = 50  # runtime.py — compaction threshold
```

**Problem:** These should be configurable but are hardcoded. Users with
large context windows (128K) or long-running sessions can't adjust.

**Impact:** Suboptimal behavior for non-default setups.

**Fix:** Read from config with sensible defaults.

---

### 9. Test Gaps
**Location:** `tests/`

Missing tests:
- `test_entry.py` — entry.py has zero tests
- Concurrent turn tests — no multi-threaded/multi-async tests
- Cache invalidation tests — no tests for `invalidate_caches()`
- Tool result serialization tests — no tests for non-JSON results
- Signal handling tests — no tests for Ctrl+C behavior

---

## ℹ️ LOW SEVERITY

### 10. `while True` in REPL (cli_v2.py)
**Location:** `wisp/transport/cli_v2.py` line 420

```python
while True:
    prompt = await queue.get()
    if prompt is None:
        break
```

**Problem:** If `queue.get()` never returns (bug in queue), the loop spins
forever. The `None` sentinel is the only exit.

**Impact:** REPL hangs on queue bug. Low probability.

**Fix:** Add timeout or max iteration guard.

---

## Scorecard

| Category | Before P0/P1 | After P0/P1 | After Deep Grill |
|----------|-------------|-------------|------------------|
| Architecture | B+ | A | A |
| Correctness | C | B+ | B+ |
| Security | B | A- | B+ (swallowing) |
| Performance | C | B+ | B+ |
| Error Handling | D | B | B |
| Event Standardization | F | A | A |
| Memory Safety | C | B | B |
| Concurrency | N/A | N/A | C (no locks) |
| **Overall** | **C+** | **B+** | **B** |

---

## Recommendations

### Immediate (P0)
1. Add `threading.Lock` around `_core_cache` access
2. Fix security exception swallowing — treat as deny
3. Normalize tool results to standard schema

### Short-term (P1)
4. Add per-session `asyncio.Lock` for concurrent turns
5. Add JSON serialization for tool results
6. Add input validation at runtime entry points
7. Join reader thread on REPL exit

### Long-term (P2)
8. Make hardcoded values configurable
9. Add concurrent turn tests
10. Add entry.py tests
