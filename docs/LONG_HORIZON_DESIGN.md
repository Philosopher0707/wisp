# Long-Horizon Task Execution: Design Specification

**Status:** Draft  
**Author:** Wisp Architecture Team  
**Date:** 2025-01-15

---

## 1. Problem Statement

Wisp's current agent loop (`WispAgentCore.run_task`) is designed for **single-turn, short-horizon** tasks:
- Default: 10 iterations, 120-second timeout
- No persistence between process restarts
- Static plans (no replanning on failure)
- Sequential execution only
- No progress visibility for multi-minute tasks

This breaks down for real-world engineering work:
- Refactoring a 50-file codebase
- Migrating frameworks (Flask → FastAPI)
- Multi-step research with verification
- CI/CD pipeline fixes requiring iterative test-debug cycles

**Goal:** Enable Wisp to execute tasks that span minutes to hours, survive crashes, adapt to failures, and report meaningful progress.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    User / Transport Layer                      │
│         (CLI --background, WebSocket, MCP, ACP)              │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│              LongHorizonRunner (Orchestrator)                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ TaskState   │  │ PlanEngine  │  │ ProgressReporter    │  │
│  │ (checkpoint)│  │ (create/    │  │ (events/stream)     │  │
│  │             │  │  replan)    │  │                     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
│  ┌─────────────┐  ┌─────────────────────────────────────┐    │
│  │ StepExecutor│  │ ParallelTaskExecutor (DAG)          │    │
│  │ (sequential │  │ (dependency-aware parallel subagents)│   │
│  │  fallback)  │  │                                     │    │
│  └─────────────┘  └─────────────────────────────────────┘    │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
│ WispAgentCore│ │Subagent  │ │  Tools      │
│ (run_task)   │ │Orchestra-│ │ (run_tests, │
│              │ │tor       │ │  edit_file) │
└──────────────┘ └──────────┘ └─────────────┘
```

**Design principle:** The long-horizon runner is a **higher-order orchestrator** that wraps `WispAgentCore.run_task()`. It does not replace the agent loop; it composes multiple agent runs into a durable, adaptive workflow.

---

## 3. Data Model

### 3.1 TaskState (The Checkpoint)

The canonical representation of a task at any point in time. Saved to disk after every step.

```python
@dataclass
class TaskState:
    # ── Identity ──────────────────────────────────────────
    task_id: str                    # UUID or slug, e.g. "task-20250115-143022"
    goal: str                       # Original user prompt
    
    # ── Plan ──────────────────────────────────────────────
    plan: list[Step]                # Ordered list of steps
    current_step_index: int = 0     # Active step pointer
    plan_version: int = 1           # Incremented on each replan
    
    # ── Execution history ─────────────────────────────────
    completed_steps: list[StepResult]
    failed_steps: list[StepFailure]
    replan_history: list[Plan]      # Every plan version for audit
    
    # ── Metadata ──────────────────────────────────────────
    status: TaskStatus              # pending | running | paused | completed | failed
    created_at: datetime
    updated_at: datetime
    last_checkpoint: datetime
    
    # ── Configuration ─────────────────────────────────────
    max_iterations: int = 100
    step_timeout: float = 300.0
    replan_on_failure: bool = True
    max_replans: int = 3
    
    # ── Context management ──────────────────────────────
    accumulated_context: str        # Summarized results so far
    context_token_count: int        # Approximate token count
```

### 3.2 Step (Atomic Unit of Work)

```python
@dataclass
class Step:
    id: str                         # "step-1", "step-1a" (substep)
    description: str                # Natural language instruction
    status: StepStatus              # pending | running | completed | failed | skipped
    
    # Execution
    tool_calls: list[dict]          # Record of tools used
    result: str                     # Output / summary
    error: str                      # Failure reason
    duration_ms: int = 0
    
    # Parallelism
    dependencies: list[str]         # Step IDs that must complete first
    parallel_group: str | None      # "group-a" for batched execution
    
    # Retry
    attempt_count: int = 0
    max_attempts: int = 3
```

### 3.3 Plan (Versioned)

```python
@dataclass
class Plan:
    version: int
    steps: list[Step]
    created_at: datetime
    reason: str                     # "initial" | "replan_after_step_4_failed"
```

### 3.4 Enums

```python
class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLANNING = "replanning"

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
```

---

## 4. State Machine

```
                    ┌─────────────┐
         ┌─────────►│   PENDING   │◄────────┐
         │          │  (created)  │         │
         │          └──────┬──────┘         │
         │                 │ start()         │ pause()
         │                 ▼                 │
    resume()         ┌─────────────┐    ┌────┴────┐
         │           │   RUNNING   │───►│  PAUSED │
         └───────────┤  (stepping) │      └─────────┘
                     └──────┬──────┘
                            │
           ┌────────────────┼────────────────┐
           │                │                │
           ▼                ▼                ▼
      ┌─────────┐     ┌─────────┐     ┌─────────┐
      │COMPLETED│     │  FAILED │     │REPLANNING│
      │(success)│     │(exhausted)    │(temp)    │
      └─────────┘     └─────────┘     └────┬────┘
                                           │
                                           └────► RUNNING (new plan)
```

### Transitions

| From | To | Trigger |
|------|----|---------|
| `PENDING` | `RUNNING` | `start()` or `resume()` with no checkpoint |
| `RUNNING` | `PAUSED` | User interrupt, SIGTERM, or `pause_task` tool |
| `PAUSED` | `RUNNING` | `resume()` with checkpoint |
| `RUNNING` | `REPLANNING` | Step failure + `attempts < max` + `replan_on_failure=True` |
| `REPLANNING` | `RUNNING` | New plan generated successfully |
| `RUNNING` | `COMPLETED` | All steps completed successfully |
| `RUNNING` | `FAILED` | Max replans exceeded, or unrecoverable error |

---

## 5. Checkpointing Strategy

### 5.1 When to Checkpoint

| Event | Frequency | Rationale |
|-------|-----------|-----------|
| After every step completion | Every step | Minimal work lost on crash |
| Before tool execution | Every tool call | Can retry idempotent tools |
| After replanning | Per replan | Don't re-replan |
| On SIGTERM/SIGINT | Signal handler | Graceful shutdown |
| Every N iterations | Configurable (default 5) | Bound disk I/O |

### 5.2 Atomic Writes

To prevent corruption during crashes:

```python
def _atomic_write(path: Path, data: str):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)  # Atomic on POSIX
```

### 5.3 Storage Layout

```
~/.config/wisp/
├── sessions/              # Existing chat sessions
├── tasks/                 # NEW: Long-horizon task checkpoints
│   ├── task-20250115-143022-migrate-flask.json
│   ├── task-20250115-151000-refactor-auth.json
│   └── index.json         # Registry of all tasks (for fast listing)
└── memory/                # Existing cross-session memory
```

### 5.4 Index Registry

`index.json` maintains a lightweight registry for O(1) listing:

```json
{
  "tasks": [
    {
      "task_id": "task-20250115-143022-migrate-flask",
      "goal": "Migrate codebase from Flask to FastAPI",
      "status": "running",
      "current_step": 3,
      "total_steps": 12,
      "updated_at": "2025-01-15T14:35:10Z"
    }
  ]
}
```

---

## 6. Replanning Algorithm

### 6.1 When to Replan

1. **Step failure** — timeout, error, or bad result
2. **Scope discovery** — step succeeds but reveals unexpected complexity
3. **Context pressure** — context window approaching limit
4. **User request** — explicit `replan` tool call

### 6.2 Replanning Prompt

```
You are replanning a long-horizon task. Here is the current state:

Original goal: {goal}
Completed steps ({n}):
{completed_summary}

Current step that failed:
  ID: {failed_step.id}
  Description: {failed_step.description}
  Failure reason: {error}
  Attempts: {failed_step.attempt_count}/{failed_step.max_attempts}

Remaining work (old plan):
{remaining_steps}

Create a NEW plan for the remaining work. Requirements:
- Be specific and actionable
- Break complex steps into smaller ones
- Account for the failure (don't repeat the same approach)
- Include verification/validation steps
- Return as a numbered list, one step per line
- Each step should be independently verifiable
```

### 6.3 Replanning Constraints

- Cannot delete or modify completed steps
- Can merge, split, or reorder remaining steps
- Must preserve dependency chains (if DAG mode)
- Max 3 replans per task (configurable)
- Plan similarity detection: if new plan is >80% identical to old, reject and escalate

### 6.4 Context Compaction During Replan

If accumulated context exceeds threshold, summarize before replanning:

```python
async def _compact_context(self, state: TaskState) -> str:
    """Summarize completed steps to free context window."""
    prompt = (
        f"Summarize these {len(state.completed_steps)} completed steps "
        f"into key findings and decisions for the remaining work:\n"
        + "\n".join(f"- {r.step_id}: {r.result[:300]}" 
                   for r in state.completed_steps)
    )
    summary = await self.agent.run_task(prompt, max_iterations=2)
    state.accumulated_context = summary["output"]
    state.context_token_count = estimate_tokens(summary["output"])
```

---

## 7. DAG Parallel Execution

### 7.1 Dependency Model

Steps declare dependencies explicitly:

```python
steps = [
    Step(id="audit", description="Audit current code", dependencies=[]),
    Step(id="models", description="Migrate SQLAlchemy models", dependencies=["audit"]),
    Step(id="routes", description="Migrate route handlers", dependencies=["audit"]),
    Step(id="auth", description="Migrate auth middleware", dependencies=["audit"]),
    Step(id="tests", description="Update tests", dependencies=["models", "routes", "auth"]),
    Step(id="docs", description="Update documentation", dependencies=["tests"]),
]
```

### 7.2 Execution Algorithm

```
while pending_nodes:
    ready = [n for n in pending 
             if all(d in completed for d in n.dependencies)]
    
    if not ready and not running:
        break  # Deadlock or done
    
    # Launch up to MAX_PARALLEL (default 4)
    batch = ready[:MAX_PARALLEL - len(running)]
    for node in batch:
        launch_subagent(node)
    
    # Wait for first completion (event-driven)
    completed_node = await first_completed(running)
    
    # Check if result requires replanning
    if not completed_node.success and replan_on_failure:
        new_plan = replan(...)
        update_dag(new_plan)
```

### 7.3 Result Aggregation

Parallel step results are merged via summarization:

```python
async def _merge_results(self, results: list[StepResult]) -> str:
    if len(results) == 1:
        return results[0].result
    
    summary_prompt = (
        f"Summarize these {len(results)} parallel task results into "
        f"key findings for the next phase:\n"
        + "\n".join(f"- {r.step_id}: {r.result[:500]}" for r in results)
    )
    return await self.agent.run_task(summary_prompt, max_iterations=2)
```

### 7.4 Deadlock Detection

```python
def _detect_deadlock(self) -> bool:
    """True if no nodes are running and no nodes are ready,
    but pending nodes remain (circular dependencies)."""
    has_pending = any(n.status == StepStatus.PENDING for n in self.nodes.values())
    has_running = any(n.status == StepStatus.RUNNING for n in self.nodes.values())
    ready = [n for n in self.nodes.values()
             if n.status == StepStatus.PENDING
             and all(self.nodes[d].status == StepStatus.COMPLETED 
                     for d in n.dependencies)]
    return has_pending and not has_running and not ready
```

---

## 8. Error Handling & Escalation

### 8.1 Failure Categories

| Type | Example | Strategy |
|------|---------|----------|
| **Transient** | Network timeout, model rate limit | Retry with exponential backoff (max 3) |
| **Tool error** | File not found, syntax error | Replan with corrected approach |
| **Model error** | Hallucinated API, bad args | Replan with stricter instructions |
| **Fundamental** | Impossible goal, missing deps | Escalate to user |
| **Safety** | Dangerous command detected | Block, log, escalate |

### 8.2 Escalation Rules

```python
ESCALATION_RULES = {
    "max_consecutive_failures": 3,      # Same step failing repeatedly
    "max_failure_ratio": 0.3,          # 30% of total steps failed
    "max_replans_exceeded": True,
    "user_intervention_patterns": [
        "git push failed",
        "permission denied",
        "merge conflict",
        "test suite broken",
        "requires manual review",
    ]
}
```

### 8.3 Human-in-the-Loop Protocol

When escalation triggers:

1. Save checkpoint immediately
2. Emit `AgentEvent(TYPE_ESCALATION, {...})`
3. Transport presents to user:
   ```
   [ESCALATION] Task task-20250115-143022 requires intervention:
   
   Step 7 ("Update database migrations") failed 3 times.
   Last error: "Alembic revision conflict: multiple heads detected"
   
   Options:
   [c]ontinue  — Try with suggested fix
   [r]eplan    — Force full replan from current state
   [s]kip      — Skip this step, continue with next
   [a]bort     — Mark task as failed
   [i]nject    — Provide custom instructions
   ```
4. User response injected as system message
5. Task resumes with updated state

---

## 9. Integration Points

### 9.1 With Agent Loop

```
User prompt → Main agent → Detects long-horizon goal
                              ↓
                    Calls run_long_task tool
                              ↓
                    LongHorizonRunner.start()
                              ↓
                    For each step:
                        agent.run_task(step_description)
                              ↓
                    Returns final result to main agent
                              ↓
                    Main agent synthesizes + presents
```

The runner uses `WispAgentCore.run_task()` for each step. This ensures:
- All existing tools available
- Same approval/safety flow
- Context window management inherited
- Streaming events preserved

### 9.2 With Session Management

- Task checkpoints are **independent** of chat sessions
- A session can spawn and track multiple tasks
- Task results stored in `remember` for cross-session recall
- Session summary: "Previously worked on task-X (status: completed)"

### 9.3 With SubagentOrchestrator

- DAG executor delegates to `SubagentOrchestrator.spawn()`
- Each DAG node = one subagent contract
- Subagent depth limits prevent infinite recursion
- Subagent results feed back into task state

### 9.4 With Existing Tools

| Tool | Purpose |
|------|---------|
| `plan_task` | Create initial plan (already exists) |
| `mark_step_done` | Mark step complete (already exists) |
| `run_tests` | Verify step results (newly added) |
| `run_long_task` | Start long-horizon task (NEW) |
| `resume_task` | Resume from checkpoint (NEW) |
| `task_status` | Query progress (NEW) |
| `list_tasks` | Show all tasks (NEW) |
| `pause_task` | Graceful pause (NEW) |
| `cancel_task` | Abort task (NEW) |

---

## 10. File Structure

```
wisp/
├── long_horizon/
│   ├── __init__.py              # Public API exports
│   ├── state.py                 # TaskState, Step, Plan dataclasses
│   ├── runner.py                # LongHorizonRunner
│   ├── dag.py                   # ParallelTaskExecutor
│   ├── replanner.py             # Replanning logic + prompts
│   ├── progress.py              # ProgressReporter (events/streaming)
│   ├── errors.py                # TaskError, EscalationError
│   └── storage.py               # Checkpoint I/O, index registry
├── tools/
│   ├── long_horizon.py          # Tool implementations
│   └── _legacy.py               # Schema + TOOL_IMPLS updates
├── transport/
│   └── cli.py                   # --background, --resume flags
└── core/
    └── agent.py                 # Minimal hooks for task awareness

tests/
├── test_long_horizon/
│   ├── test_state.py            # Serialization, checkpoint I/O
│   ├── test_runner.py           # Step loop, timeout, replanning
│   ├── test_dag.py              # Dependency resolution, parallelism
│   ├── test_replanner.py        # Plan generation, constraints
│   └── test_storage.py          # Atomic writes, registry

docs/
└── LONG_HORIZON_DESIGN.md       # This document
```

---

## 11. API Design

### 11.1 Python SDK

```python
from wisp.long_horizon import LongHorizonRunner, TaskState

runner = LongHorizonRunner(
    agent=agent,
    max_iterations=50,
    step_timeout=300,
    replan_on_failure=True,
    max_parallel=4,
    progress_callback=lambda state: print(
        f"Step {state.current_step_index}/{len(state.plan)}"
    ),
)

# Start new task
async for event in runner.run("Migrate from Flask to FastAPI"):
    print(event)

# Resume crashed task
async for event in runner.run("", resume_from="task-20250115-143022"):
    print(event)

# Query status
state = TaskState.load("task-20250115-143022")
print(state.status, state.current_step_index)
```

### 11.2 CLI

```bash
# Start background task (detached, logs to file)
wisp --background "Refactor all database models to use SQLModel"
# → Created task: task-20250115-143022

# List all tasks
wisp --list-tasks
# ID                          Status    Progress    Goal
# task-20250115-143022        running   3/12        Refactor all database...

# Check specific task
wisp --task-status task-20250115-143022
# Step 3/12: Migrating User model...
# Last checkpoint: 14:35:10
# ETA: ~8 minutes

# Resume after crash
wisp --resume task-20250115-143022

# Interactive mode with long-horizon awareness
wisp "Migrate from Flask to FastAPI" --long-horizon
# [Wisp] This looks like a complex task. I'll break it into steps.
# [Wisp] Created plan with 12 steps. Starting step 1...
```

### 11.3 Tool Schemas

```json
{
  "type": "function",
  "function": {
    "name": "run_long_task",
    "description": "Execute a complex multi-step task with automatic checkpointing and replanning. Use for goals requiring more than 5 steps or expected to take longer than 5 minutes.",
    "parameters": {
      "type": "object",
      "properties": {
        "goal": {"type": "string", "description": "Detailed description of what to accomplish"},
        "max_iterations": {"type": "integer", "default": 50},
        "step_timeout": {"type": "integer", "default": 300},
        "parallelize": {"type": "boolean", "default": true}
      },
      "required": ["goal"]
    }
  }
}
```

```json
{
  "type": "function",
  "function": {
    "name": "resume_task",
    "description": "Resume a previously started long-horizon task from its last checkpoint.",
    "parameters": {
      "type": "object",
      "properties": {
        "task_id": {"type": "string", "description": "Task ID from previous run_long_task"}
      },
      "required": ["task_id"]
    }
  }
}
```

```json
{
  "type": "function",
  "function": {
    "name": "task_status",
    "description": "Get the current status and progress of a long-horizon task.",
    "parameters": {
      "type": "object",
      "properties": {
        "task_id": {"type": "string"}
      },
      "required": ["task_id"]
    }
  }
}
```

```json
{
  "type": "function",
  "function": {
    "name": "list_tasks",
    "description": "List all long-horizon tasks with their statuses.",
    "parameters": {
      "type": "object",
      "properties": {
        "status_filter": {"type": "string", "enum": ["all", "running", "paused", "completed", "failed"], "default": "all"}
      }
    }
  }
}
```

---

## 12. Testing Strategy

| Component | Test Type | Key Scenarios |
|-----------|-----------|---------------|
| `TaskState` | Unit | Serialization round-trip, checkpoint save/load, path handling, atomic writes |
| `LongHorizonRunner` | Unit + mocked agent | Step loop, timeout handling, replanning on failure, max iterations, completion |
| `ParallelTaskExecutor` | Unit + async | DAG resolution, max parallelism, deadlock detection, result aggregation |
| `Replanning` | Integration | Actual model calls with failure scenarios, plan similarity detection |
| `Storage` | Unit | Atomic writes under crash simulation, registry consistency |
| `CLI` | Integration | End-to-end with temp directories, signal handling |
| `Crash recovery` | Integration | Kill process mid-task, verify resume produces identical result |

### 12.1 Crash Recovery Test

```python
async def test_crash_recovery():
    """Simulate crash at step 3, verify resume from checkpoint."""
    runner = LongHorizonRunner(agent=mock_agent)
    
    # Run first 2 steps
    events = []
    async for event in runner.run("Test goal"):
        events.append(event)
        if len([e for e in events if "step completed" in e.data]) == 2:
            break  # Simulate crash
    
    task_id = runner.state.task_id
    
    # New runner, resume
    runner2 = LongHorizonRunner(agent=mock_agent)
    events2 = []
    async for event in runner2.run("", resume_from=task_id):
        events2.append(event)
    
    # Should start from step 3, not step 1
    assert runner2.state.current_step_index == 2
```

---

## 13. Performance Considerations

| Concern | Mitigation |
|---------|------------|
| Disk I/O from frequent checkpoints | Async writes, batched every N steps |
| Context window overflow | Aggressive summarization between steps |
| Model token costs from replanning | Temperature=0 for planning, cache plans |
| Subagent explosion | Max parallelism (4), depth limits (3) |
| Memory leaks from long runs | Explicit cleanup after each step, no global accumulators |
| Disk space from old checkpoints | Auto-archive completed tasks after 30 days |

---

## 14. Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Checkpoint corruption | High | Low | Atomic writes, JSON schema validation on load |
| Replanning loops | Medium | Medium | Max replans, plan similarity detection, cooldown |
| Model inconsistency | Medium | Medium | Temperature=0 for planning, structured output parsing |
| Context window overflow | High | High | Aggressive compaction, token estimation |
| Subagent deadlock | Medium | Low | Deadlock detection, timeout on all waits |
| User confusion about state | Medium | Medium | Clear progress events, explicit status tool |
| Security: task files contain code | Medium | Low | Store in user's config dir, no world-readable |

---

## 15. Implementation Phases

### Phase 1: Foundation (Day 1)
- `wisp/long_horizon/state.py` — TaskState, Step, Plan, enums
- `wisp/long_horizon/storage.py` — Checkpoint I/O, atomic writes, index registry
- Tests: `test_state.py`, `test_storage.py`

### Phase 2: Sequential Runner (Day 2)
- `wisp/long_horizon/runner.py` — LongHorizonRunner with step loop
- `wisp/long_horizon/replanner.py` — Replanning logic
- `wisp/long_horizon/progress.py` — Progress events
- Tests: `test_runner.py`, `test_replanner.py`

### Phase 3: Parallel Execution (Day 3)
- `wisp/long_horizon/dag.py` — ParallelTaskExecutor
- Tests: `test_dag.py`

### Phase 4: Tool Integration (Day 4)
- `wisp/tools/long_horizon.py` — Tool implementations
- Update `wisp/tools/_legacy.py` — Schemas + TOOL_IMPLS
- Tests: Tool execution via `execute_tool()`

### Phase 5: CLI Integration (Day 5)
- `wisp/transport/cli.py` — `--background`, `--resume`, `--list-tasks`
- Tests: End-to-end CLI tests

### Phase 6: Documentation (Day 6)
- User guide with example prompts
- Architecture decision record (ADR)

---

## 16. Open Questions

1. **Should the runner be a subagent itself?** — Pro: natural fit with existing orchestrator. Con: adds unnecessary indirection for sequential tasks.
2. **How to handle interactive steps?** — Some steps may need user approval (e.g., `git push`). Should the runner pause or delegate to the main agent?
3. **Should completed tasks be searchable?** — Integration with `search_codebase` for "how did we solve X last time?"
4. **Multi-agent task sharing?** — Can multiple Wisp instances collaborate on the same task checkpoint?

---

## Appendix A: Example Task Lifecycle

### User Request
```
"Migrate our entire API layer from Flask to FastAPI, including tests"
```

### Phase 1: Planning
```
[Agent] This is a complex multi-step task. I'll create a plan and execute it with checkpointing.

Plan (12 steps):
1. Audit all Flask imports and blueprints
2. Create FastAPI app factory
3. Migrate route handlers (users)
4. Migrate route handlers (auth)
5. Migrate route handlers (items)
6. Migrate middleware
7. Update dependency injection
8. Migrate tests (users)
9. Migrate tests (auth)
10. Migrate tests (items)
11. Run full test suite
12. Update documentation

Starting step 1...
```

### Phase 2: Execution (with failure)
```
[Step 1] Completed: Found 12 Flask imports, 3 blueprints, 2 middlewares
[Step 2] Completed: FastAPI app factory created at app/main.py
[Step 3] Completed: User routes migrated
[Step 4] Failed: Auth routes use Flask-Login, no FastAPI equivalent

[Replanning] Step 4 failed. Creating revised plan...

New Plan (13 steps):
4a. Research FastAPI auth alternatives
4b. Implement JWT auth middleware
4c. Migrate auth routes
...
```

### Phase 3: Checkpoint (process crash)
```
# Process killed at step 7
# Checkpoint saved: ~/.config/wisp/tasks/task-20250115-143022.json
```

### Phase 4: Resume
```bash
$ wisp --resume task-20250115-143022
```
```
[Agent] Resuming task task-20250115-143022 at step 7/13
[Step 7] Running: Update dependency injection...
```

### Phase 5: Completion
```
[Step 13] Completed: Documentation updated

Task completed successfully!
Summary:
- 12 files modified
- 3 new files created
- 147/147 tests passing
- Checkpoint archived
```

---

*End of design specification.*
