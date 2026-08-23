# Multi-Agent Layer: Production Wiring Analysis

## How It Actually Works in Production

### 1. Entry Point: Tool Call from LLM

The LLM decides to spawn a subagent by emitting a tool call:

```json
{
  "function": {
    "name": "spawn_subagent",
    "arguments": {
      "task": "Audit auth.py for SQL injection vulnerabilities",
      "tools": ["read_file", "search_codebase"],
      "max_iterations": 10,
      "timeout_seconds": 120,
      "output_format": "json",
      "output_schema": {"findings": [{"severity": "string", "line": "number"}]}
    }
  }
}
```

### 2. ToolExecutor Intercepts (wisp/core/agent.py:1165)

```python
async def _spawn_subagent(self, args: dict, workspace: str) -> str:
    # Depth guard: prevents recursive explosion
    # MAX_SUBAGENT_DEPTH = 2
    # MAX_SUBAGENT_BRANCHING = 3

    # Build SubagentContract from tool args
    contract = SubagentContract(
        task=args.get("task", ""),
        tools=args.get("tools", ["all"]),
        max_iterations=int(args.get("max_iterations", 15)),
        timeout_seconds=float(args.get("timeout_seconds", 120)),
        output_format=args.get("output_format", "text"),
        workspace=workspace,
        auto_approve=self.config.auto_approve,
        worktree_isolated=args.get("worktree_isolated", False),
        max_tokens=args.get("max_tokens"),
        output_schema=args.get("output_schema"),
        _subagent_depth=depth + 1,
        _subagent_branch_count=branch_count + 1,
    )
```

**Key guards:**
- Depth limit: prevents subagent spawning subagents infinitely
- Branching limit: prevents one agent from spawning 100 parallel agents
- Cache: results cached for 1-5 minutes (TTL based on output format)
- Local model fallback: simple tasks use smaller local models
- Adaptive timeout: adjusts based on task complexity

### 3. SubagentOrchestrator.run() (wisp/multi_agent/subagent_orchestrator.py:582)

```python
async def run(self, contract: SubagentContract) -> SubagentResult:
    # 1. Depth guard (redundant but safe)
    # 2. Role validation (checks ROLE_CONFIGS)
    # 3. Contract validation (timeout > 0, iterations > 0)
    # 4. Cache check
    # 5. Token budget check
    # 6. Worktree creation (if worktree_isolated=True)
    # 7. Build child config (deep copy of parent + overrides)
    # 8. Create Session for subagent
    # 9. Build system prompt (role-specific or default)
    # 10. DISPATCH by isolation level:
    #     - "process": _spawn_subagent_process()  [multiprocessing]
    #     - default: _spawn_subagent_thread()     [asyncio.to_thread]
```

### 4. Thread-Based Execution (default path)

```python
async def _spawn_subagent_thread(...):
    # 1. Create heartbeat file for hung-thread detection
    # 2. Start heartbeat task (touches file every 5s)
    # 3. Start health monitor (checks heartbeat every 10s)
    # 4. Run in thread via asyncio.to_thread():
    #    self._run_agent_sync(...)  # creates NEW event loop
    # 5. Save session to disk
    # 6. Build SubagentResult with metrics
    # 7. Token estimation & budget tracking
    # 8. Cleanup worktree
```

### 5. _run_agent_sync: The Nested Event Loop Anti-Pattern

```python
def _run_agent_sync(self, ...):
    loop = asyncio.new_event_loop()  # <-- NESTED LOOP
    try:
        return loop.run_until_complete(
            self._run_agent(...)  # async method
        )
    finally:
        # Cancel pending tasks
        # Close loop
```

**Why this exists:** `asyncio.to_thread()` runs in a sync thread. But `_run_agent()` calls `agent.run_task()` which is async. So they create a new event loop inside the thread.

**This is an anti-pattern** but "necessary because _run_agent calls async code that may spawn subagents."

### 6. _run_agent: Actual Agent Execution

```python
async def _run_agent(self, contract, config, session, system_prompt, ...):
    from wisp.core.agent import WispAgentCore

    agent = WispAgentCore(
        config=config,
        session=session,
        role=f"subagent:{contract.name}",
    )

    # Apply tool filtering
    if contract.tools != ["all"]:
        agent._allowed_tools = set(contract.tools)

    # Run non-interactively via run_task()
    task_result = await agent.run_task(
        task_description=contract.task,
        workspace=workspace_path,
        max_iterations=max_iter,
        timeout_seconds=timeout_per_task,
        system_prompt=system_prompt,
    )

    # Collect tool calls from message history
    # Extract files changed from output
    # Return dict with success, output, error, files_changed, iterations_used
```

### 7. run_task() in WispAgentCore (wisp/core/agent.py:1648)

```python
async def run_task(self, task_description, workspace, max_iterations, timeout_seconds, system_prompt):
    # This is the SAME agent core that handles the main loop!
    # It runs the full turn loop (generate → tool calls → execute → repeat)
    # but without streaming events to a transport.
    # Returns {"success": bool, "output": str}
```

### 8. Result Flows Back Up the Stack

```
_run_agent() → dict
  ↓
_run_agent_sync() → dict (via nested event loop)
  ↓
_spawn_subagent_thread() → SubagentResult
  ↓
run() → SubagentResult
  ↓
_spawn_subagent() → str (output or error)
  ↓
ToolExecutor → JSON string → LLM
```

### 9. Parallel Execution: spawn_subagents()

```python
async def spawn_subagents(self, specs: list) -> list:
    orch = SubagentOrchestrator(parent_agent=self)
    contracts = [...]
    results = await orch.run_parallel(contracts)
    # run_parallel() uses asyncio.gather() to run all concurrently
```

### 10. Pattern Execution (Map-Reduce, Vote, Chain)

These are **higher-order patterns** built on `run_parallel()`:

```python
async def run_map_reduce(self, task, items, mapper, reducer):
    # 1. Map: run_parallel([mapper(item) for item in items])
    # 2. Reduce: run_single(reducer + combined outputs)

async def run_vote(self, task, agents, consensus_threshold):
    # 1. run_parallel(agents)
    # 2. Parse outputs as yes/no
    # 3. Return majority vote

async def run_chain(self, contracts, pass_context=True):
    # 1. Run sequentially
    # 2. Optionally pass previous output as context to next
```

---

## Logical Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    WispAgentCore (parent)                    │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  _spawn_subagent() — tool call handler                  │  │
│  │  - depth/branch guards                                  │  │
│  │  - cache check                                          │  │
│  │  - adaptive timeout                                     │  │
│  │  - retry loop (0-2 retries with backoff)                │  │
│  │  - schema validation                                    │  │
│  └─────────────────────────────────────────────────────────┘  │
│                         ↓                                     │
│              SubagentOrchestrator.run()                       │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  - depth guard (redundant)                              │  │
│  │  - role validation                                      │  │
│  │  - contract validation                                  │  │
│  │  - cache check                                          │  │
│  │  - token budget check                                   │  │
│  │  - worktree creation (optional)                         │  │
│  │  - build child config                                   │  │
│  │  - create Session                                       │  │
│  │  - build system prompt                                  │  │
│  └─────────────────────────────────────────────────────────┘  │
│                         ↓                                     │
│         _spawn_subagent_thread() (default)                    │
│  ┌─────────────────────────────────────────────────────────┐  │
│  │  - heartbeat file + health monitor                      │  │
│  │  - asyncio.to_thread()                                  │  │
│  │  - _run_agent_sync() [nested event loop]                │  │
│  │  - _run_agent() [async]                                 │  │
│  │  - WispAgentCore.run_task()                             │  │
│  │  - save session                                         │  │
│  │  - token estimation                                     │  │
│  │  - cleanup worktree                                     │  │
│  └─────────────────────────────────────────────────────────┘  │
│                         ↓                                     │
│              SubagentResult → str → LLM                       │
└─────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### Why Thread-Based (not Async)?

```python
# Thread-based allows cancellation via asyncio.wait_for()
result = await asyncio.wait_for(
    asyncio.to_thread(self._run_agent_sync, ...),
    timeout=contract.timeout_seconds,
)
```

If this were pure async, a hung model call would block the event loop. The thread can be abandoned (though resources may leak).

### Why Nested Event Loop?

```python
# _run_agent_sync creates a new loop because:
# 1. It's running inside asyncio.to_thread() (sync context)
# 2. _run_agent() calls agent.run_task() which is async
# 3. run_task() may call _spawn_subagent() recursively
```

This is the only way to run async code inside a sync thread that may itself spawn async subagents.

### Why Worktree Isolation?

```python
if contract.worktree_isolated:
    worktree_path = await self._create_worktree(contract.name)
    # Subagent works in a git worktree
    # Parent workspace is untouched
    # Worktree deleted after completion
```

Prevents subagents from corrupting the parent's working directory. Useful for "what if" scenarios.

### Why Token Budget?

```python
budget_error = self._check_token_budget(contract)
# Prevents a single subagent from consuming the entire context window
# Tracks cumulative tokens across all subagents in this orchestrator
```

---

## Integration Points with Core Agent

| Integration | Location | Purpose |
|-------------|----------|---------|
| `_spawn_subagent()` | `core/agent.py:1165` | Tool call handler |
| `spawn_subagents()` | `core/agent.py:1309` | Parallel batch handler |
| `_check_delegation()` | `core/agent.py:1460` | Auto-delegation via CapabilityMatcher |
| `_auto_parallel_research()` | `core/agent.py:~800` | Research delegation |
| `run_task()` | `core/agent.py:1648` | Non-interactive agent loop |

---

## What Actually Gets Used in Production

### Used (active code paths):
1. **`SubagentOrchestrator.run()`** — single subagent execution
2. **`SubagentOrchestrator.run_parallel()`** — parallel batch
3. **`_spawn_subagent_thread()`** — default execution path
4. **`_run_agent_sync()`** — nested loop wrapper
5. **`_run_agent()`** — actual agent creation and execution
6. **`AgentRole` / `ROLE_CONFIGS`** — role-specific system prompts
7. **`schema_validator`** — output validation
8. **`CapabilityMatcher`** — auto-delegation decisions
9. **`partition_context`** — context splitting for large tasks

### NOT Used (dead weight):
1. **`SwarmOrchestrator`** — deprecated, superseded by SubagentOrchestrator
2. **`MessageBus`** — instantiated but never used for actual messaging
3. **`AgentRegistry`** — instantiated but no external queries
4. **`AgentFactory`** — only used internally by SwarmOrchestrator
5. **`WorkspaceLock`** — only used internally
6. **`CodebaseOrchestrator`** — never instantiated in production
7. **Pattern methods:** `run_map_reduce()`, `run_vote()`, `run_chain()` — defined but never called from core agent

---

## Summary

The multi-agent layer is **logically sound** but **architecturally bloated**:

- **Core flow works:** spawn_subagent → contract → orchestrator → thread → nested loop → agent.run_task() → result
- **Guards are good:** depth, branching, timeout, cache, token budget
- **Anti-patterns exist:** nested event loops, thread-per-subagent, 2,198-line god class
- **60% of exports are unused:** the public API is much larger than the actual usage
- **Tests are mostly good:** 48 + 120 + 26 = 194 tests pass (with 2 import bugs)

**The system works in production. It just carries a lot of unused conceptual baggage.**
