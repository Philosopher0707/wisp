# Deep Dive: 5 Critical Issues in wisp/core/agent.py

## Issue 1: `_run_agent_sync` Creates New Event Loop (PARTIALLY FIXED)

### Location
`wisp/multi_agent/orchestrator.py:2484-2520` (calls `wisp/core/agent.py:1732`)

### The Problem
`_run_agent_sync` creates a **brand new event loop** inside a thread to run async code:

```python
def _run_agent_sync(...):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(
            self._run_agent(...)  # async method
        )
    finally:
        # Cancel pending tasks before closing (FIXED)
        ...
        loop.close()
```

**Why this is dangerous:**

1. **Nested event loops**: When `asyncio.to_thread()` calls `_run_agent_sync`, it runs in a thread managed by the **parent's event loop**. Inside that thread, it creates a **second event loop**. This is technically allowed but creates a "loop within a loop" anti-pattern.

2. **Thread-local state pollution**: `asyncio.new_event_loop()` sets the new loop as the thread-local default. If any code inside `_run_agent` calls `asyncio.get_event_loop()` or `asyncio.get_running_loop()`, it gets the NEW loop, not the parent's. This can cause subtle bugs with:
   - `asyncio.create_task()` — tasks created in the wrong loop
   - `asyncio.gather()` — coroutines scheduled in the wrong loop
   - `asyncio.Queue` — queues bound to the wrong loop

3. **Resource leaks**: Even with the fix (cancelling pending tasks), if `_run_agent` creates background tasks that don't check their cancellation status, they may:
   - Hold file descriptors open
   - Keep database connections alive
   - Block the thread pool executor

### Concrete Failure Scenario

```python
# Parent loop (main thread)
async def parent():
    # This runs in parent's event loop
    result = await asyncio.to_thread(_run_agent_sync, contract)
    # ThreadPoolExecutor runs _run_agent_sync in a worker thread
    # Inside that thread, _run_agent_sync creates loop #2
    # _run_agent() spawns subagent → creates loop #3 (in another thread)
    # Each loop has its own task queue, timer heap, and selector
```

**Memory impact**: Each event loop allocates:
- ~50KB for the loop object itself
- Selector (epoll/kqueue) with file descriptor table
- Timer heap for scheduled callbacks
- Task queue and future registry

With 10 parallel subagents × 3 loops each = **30 event loops** = ~1.5MB overhead.

### The Fix (Already Applied)
```python
finally:
    try:
        pending = asyncio.all_tasks(loop)
        if pending:
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    except Exception:
        pass
    loop.close()
```

**Verdict**: ✅ Fixed for the common case, but the architectural pattern is still risky. Consider using `asyncio.run_coroutine_threadsafe()` instead of creating new loops.

---

## Issue 2: `run_task` Uses Sync `_run_turn_streaming` Inside Async Context

### Location
`wisp/core/agent.py:1732-1817`

### The Problem

```python
async def run_task(self, ...):  # ← async method
    while iteration < max_iterations:
        response = self._run_turn_streaming(system)  # ← sync call!
        # ...
        async for event in self._run_tool_calls(tool_calls, workspace):
            # ← async iteration
```

`_run_turn_streaming` is a **sync method** that drains an async generator:

```python
def _run_turn_streaming(self, system: str) -> dict:
    for _ in self._run_turn_streaming_events(system):  # ← sync iteration over async generator!
        pass
    return getattr(self.client, "stream_response", None) or {}
```

And `_run_turn_streaming_events` is a **sync generator** that yields async events:

```python
def _run_turn_streaming_events(self, system: str):  # ← NOT async def
    for event in self.client.generate_stream_events(...):  # ← sync iteration
        yield thinking(event.text)  # ← yields AgentEvent objects
```

**Why this works (but is fragile):**

1. `generate_stream_events` returns a **sync Iterator** (not async), so `for ... in` works fine.
2. The `yield` inside `_run_turn_streaming_events` makes it a **sync generator**.
3. `_run_turn_streaming` iterates this sync generator with `for _ in ...` — this is valid Python.

**The hidden danger:**

The sync generator `_run_turn_streaming_events` calls `self.client.generate_stream_events()`, which makes **blocking HTTP requests** to Ollama. Inside an `async def run_task()`, this blocks the **entire event loop** for the duration of the model call.

```python
async def run_task(self, ...):
    # Event loop is running here
    response = self._run_turn_streaming(system)
    # ^ BLOCKS the event loop for 15-30 seconds!
    # No other coroutines can run during this time.
    # Heartbeat tasks, progress callbacks, WebSocket pings — all frozen.
```

### Concrete Failure Scenario

```python
# User has a WebSocket connection for real-time updates
# Main agent is running run_task() in the background

async def websocket_handler(websocket):
    while True:
        msg = await websocket.recv()  # ← This will TIME OUT
        # Because run_task() blocked the event loop,
        # websocket.recv() couldn't be scheduled
```

**Another scenario:**
```python
# Two subagents running in parallel
await asyncio.gather(
    agent1.run_task("task1"),  # Blocks loop for 30s
    agent2.run_task("task2"),  # Can't start until agent1 finishes!
)
# Expected: parallel execution
# Actual: sequential execution because both block the same loop
```

### Why It Was Designed This Way

The `run_task` method is meant for **non-interactive subagent execution**. The original author likely:
1. Wanted to reuse `_run_turn_streaming_events` (which was built for the sync CLI)
2. Didn't realize that calling a sync generator from async code blocks the loop
3. Assumed `asyncio.to_thread()` (in the orchestrator) would handle the blocking

But `asyncio.to_thread()` only helps if the **orchestrator** calls `run_task()` via `to_thread()`. In the current code:

```python
# orchestrator.py:1525
result = await asyncio.wait_for(
    asyncio.to_thread(
        self._run_agent_sync,  # ← This runs in thread
        ...
    ),
    timeout=contract.timeout_seconds,
)
```

The orchestrator DOES use `to_thread()`, so the blocking is contained. But if anyone calls `agent.run_task()` directly from async code (e.g., in the server or WebSocket handler), they'll block the loop.

### The Fix

Convert `_run_turn_streaming` to async and use `async for`:

```python
async def _run_turn_streaming_async(self, system: str) -> dict:
    async for _ in self._run_turn_streaming_events_async(system):
        pass
    return getattr(self.client, "stream_response", None) or {}

async def _run_turn_streaming_events_async(self, system: str):
    # If generate_stream_events is sync, wrap it:
    loop = asyncio.get_event_loop()
    gen = self.client.generate_stream_events(...)
    try:
        while True:
            event = await loop.run_in_executor(None, next, gen)
            yield thinking(event.text)  # etc.
    except StopIteration:
        pass
```

**Verdict**: ⚠️ **Medium risk** — Currently mitigated by `asyncio.to_thread()` in the orchestrator, but dangerous if `run_task()` is called directly from async contexts.

---

## Issue 3: Subagent Depth Limit of 1 Is Too Restrictive

### Location
`wisp/core/agent.py:1270-1272`

### The Problem

```python
depth = getattr(self, "_subagent_depth", 0)
if depth >= 1:
    return "[Error: subagents cannot spawn subagents (max depth = 1)]"
```

**Why depth=1 is problematic:**

1. **No hierarchical delegation**: A parent agent can't spawn a subagent that itself spawns subagents. This prevents:
   - Manager → Worker → Task patterns
   - Divide-and-conquer with recursive splitting
   - Map-reduce where mappers spawn their own helpers

2. **False positives on legitimate nesting**: If a user asks:
   ```
   "Analyze this codebase. For each module, spawn a subagent to analyze it,
   and each of those should check for security issues."
   ```
   The second level of subagents will fail with the depth error.

3. **Inconsistent with orchestrator**: The orchestrator has its own depth check:
   ```python
   # orchestrator.py
   if contract._subagent_depth >= 2:
       return SubagentResult(..., error="Max subagent depth exceeded")
   ```
   But the agent-level check fires FIRST at depth=1, preventing the orchestrator's depth=2 from ever being reached.

### Concrete Failure Scenario

```python
# User prompt: "Build a compiler. Spawn a subagent for lexer, parser, codegen.
# Each of those should research best practices first."

# Main agent spawns subagent "compiler-lexer"
#   → depth=0, allowed
#   → "compiler-lexer" tries to spawn "research-lexer-best-practices"
#   → depth=1, BLOCKED with "max depth = 1"
#   → Lexer subagent can't do research, produces inferior output
```

### Why It Was Set to 1

Likely to prevent:
- **Infinite recursion**: Subagent A spawns B, B spawns C, C spawns D...
- **Exponential explosion**: Each subagent spawns N subagents, total = N^depth
- **Resource exhaustion**: Each subagent uses memory, API tokens, file handles

But depth=1 is overly conservative. A depth of 2 or 3 with proper guards would be safer:

```python
MAX_SUBAGENT_DEPTH = 2  # Allow one level of nesting
MAX_SUBAGENT_BRANCHING = 3  # Each subagent can spawn at most 3 children
```

### The Fix

```python
# In agent.py
MAX_SUBAGENT_DEPTH = 2
MAX_SUBAGENT_BRANCHING = 3

depth = getattr(self, "_subagent_depth", 0)
if depth >= MAX_SUBAGENT_DEPTH:
    return f"[Error: max subagent depth ({MAX_SUBAGENT_DEPTH}) exceeded]"

# Track branching count
branch_count = getattr(self, "_subagent_branch_count", 0)
if branch_count >= MAX_SUBAGENT_BRANCHING:
    return f"[Error: max subagent branching ({MAX_SUBAGENT_BRANCHING}) exceeded]"

# When spawning, increment both
child_contract._subagent_depth = depth + 1
child_contract._subagent_branch_count = branch_count + 1
```

**Verdict**: ⚠️ **Medium risk** — Prevents legitimate hierarchical patterns. Should be configurable.

---

## Issue 4: Auto-Research Hardcodes KV Caching Angles

### Location
`wisp/core/agent.py:1551-1575`

### The Problem

```python
def _research_angles(self, prompt: str) -> list[str]:
    prompt_lower = prompt.lower()

    # KV caching research
    if "kv cache" in prompt_lower or "kv caching" in prompt_lower:
        return [
            "Research the foundational problem: why KV caching is needed in transformers...",
            "Research architectural improvements: Multi-Query Attention, Grouped-Query Attention...",
            "Research compression methods: quantization, eviction policies, H2O, SnapKV...",
            "Research system-level optimizations: vLLM PagedAttention, continuous batching...",
        ]

    # Generic research breakdown
    return [
        f"Research the core concepts and fundamentals: {prompt}",
        f"Research recent advances and state-of-the-art: {prompt}",
        f"Research practical implementations and tools: {prompt}",
        f"Research limitations, challenges, and future directions: {prompt}",
    ]
```

**Why this is problematic:**

1. **Hardcoded domain knowledge**: The KV caching angles are specific to a single user's past query. They're now permanently embedded in the codebase for ALL users.

2. **Maintenance burden**: Every new domain needs a new `if` branch. The code will grow into a giant switch statement:
   ```python
   if "kv cache" in prompt_lower:
       return [...]
   elif "rag" in prompt_lower:
       return [...]
   elif "fine-tuning" in prompt_lower:
       return [...]
   elif "docker" in prompt_lower:
       return [...]
   # ... 100 more branches
   ```

3. **Generic angles are too generic**: The fallback (`core concepts`, `recent advances`, `practical implementations`, `limitations`) is a one-size-fits-all template that doesn't adapt to the query domain.

4. **No user control**: Users can't override the angles or disable auto-research for specific domains.

### Concrete Failure Scenario

```python
# User asks: "Research the best practices for PostgreSQL connection pooling"
# Expected: Angles about pgBouncer, PgPool, application-level pooling, etc.
# Actual: Generic angles that don't mention connection pooling specifics

angles = [
    "Research the core concepts and fundamentals: Research the best practices for PostgreSQL connection pooling",
    "Research recent advances and state-of-the-art: Research the best practices for PostgreSQL connection pooling",
    # ...
]
# Subagents get redundant tasks with no domain-specific guidance
```

### Better Approach

Use the **LLM itself** to generate research angles dynamically:

```python
async def _research_angles(self, prompt: str) -> list[str]:
    """Use a lightweight model to generate domain-specific research angles."""
    angle_prompt = f"""Break this research query into 3-4 parallel investigation angles.
    Each angle should be specific and non-overlapping.
    
    Query: {prompt}
    
    Return as a JSON list of strings."""
    
    # Use a fast/cheap model for this
    response = await self._quick_completion(angle_prompt, max_tokens=500)
    angles = json.loads(response)
    return angles[:4]
```

Or use a **configurable angle registry**:

```python
# config.json
{
  "research_angles": {
    "kv_cache": [
      "Foundational problem and memory complexity",
      "Architectural improvements (MQA, GQA, FlashAttention)",
      "Compression and eviction methods",
      "System-level optimizations (vLLM, PagedAttention)"
    ],
    "default": [
      "Core concepts and fundamentals",
      "Recent advances and state-of-the-art",
      "Practical implementations and tools",
      "Limitations, challenges, and future directions"
    ]
  }
}
```

**Verdict**: ⚠️ **Low-Medium risk** — Works for the current use case but doesn't scale. Should be dynamic or configurable.

---

## Issue 5: `_adaptive_subagent_timeout` Clamps to 30-300s

### Location
`wisp/core/agent.py:1720-1755`

### The Problem

```python
def _adaptive_subagent_timeout(self, task: str, requested: float) -> float:
    # ...
    # Clamp: never less than 30s, never more than 300s
    adaptive = max(30.0, min(estimated_seconds, 300.0))

    # Respect explicit user request — don't override with larger adaptive timeout
    if requested >= 30.0:
        return requested
    return max(adaptive, requested)
```

**The logic bug:**

```python
if requested >= 30.0:
    return requested  # ← ALWAYS returns user request if >= 30s
return max(adaptive, requested)  # ← NEVER reached if requested >= 30
```

This means:
- If user requests 600s → returns 600s (ignores 300s cap!)
- If user requests 10s → returns max(adaptive, 10) = at least 30s
- The 300s cap is **never enforced** for user requests ≥ 30s

**Why this is dangerous:**

1. **Resource exhaustion**: A subagent with 600s timeout can hold a thread pool worker for 10 minutes. With pool size = 5, one long task blocks 20% of capacity.

2. **Cost explosion**: Cloud models charge per token. A 10-minute subagent could consume thousands of tokens while the user waits.

3. **Cascading timeouts**: If the parent has a 120s timeout but the subagent has 600s, the parent times out first and kills the subagent thread. The subagent's work is wasted.

### Concrete Failure Scenario

```python
# User spawns a subagent for a complex refactoring task
contract = SubagentContract(
    task="Refactor the entire auth module...",
    timeout_seconds=600,  # User thinks they need 10 minutes
)

# Parent orchestrator has default timeout = 120s
# Subagent starts working...
# At 120s, parent orchestrator times out and kills the thread
# Subagent's 10 minutes of work is discarded
# User sees: "[TIMED OUT after 120s]"
```

### The Fix

```python
def _adaptive_subagent_timeout(self, task: str, requested: float) -> float:
    # ...
    adaptive = max(30.0, min(estimated_seconds, 300.0))
    
    # Respect user request BUT enforce cap
    if requested >= 30.0:
        return min(requested, 300.0)  # ← Enforce cap!
    return adaptive
```

Even better: make the cap configurable:

```python
max_timeout = getattr(self.config, "max_subagent_timeout", 300.0)
return min(requested, max_timeout) if requested >= 30.0 else min(adaptive, max_timeout)
```

**Verdict**: 🔴 **High risk** — The 300s cap is silently bypassed for user requests ≥ 30s. Can cause resource exhaustion and wasted work.

---

## Summary Table

| Issue | Severity | Status | Fix Complexity |
|-------|----------|--------|---------------|
| 1. Nested event loops | Medium | ✅ Fixed | Low |
| 2. Sync blocking in async | Medium | ⚠️ Unfixed | Medium |
| 3. Subagent depth=1 | Medium | ⚠️ Unfixed | Low |
| 4. Hardcoded research angles | Low | ⚠️ Unfixed | Medium |
| 5. Timeout cap bypassed | **High** | 🔴 Unfixed | **Low** |

## Recommended Priority

1. **Fix Issue 5 first** (timeout cap) — One-line fix, high impact
2. **Fix Issue 2** (sync blocking) — Wrap sync generator in `run_in_executor`
3. **Fix Issue 3** (subagent depth) — Make configurable with branching limits
4. **Fix Issue 4** (research angles) — Make dynamic or config-driven
5. **Monitor Issue 1** — Already fixed, but watch for edge cases
