# Long-Horizon Task System

Wisp's **long-horizon task system** enables the agent to tackle complex, multi-step goals that span many turns, tool calls, and even process restarts. Tasks are checkpointed to disk after every step, so work is never lost — you can pause, resume, or recover from crashes at any point.

## Table of Contents

- [Overview](#overview)
- [Core Concepts](#core-concepts)
- [CLI Usage](#cli-usage)
- [Tool Usage (for the Agent)](#tool-usage-for-the-agent)
- [Session Integration](#session-integration)
- [Architecture](#architecture)
- [Storage Format](#storage-format)
- [Recovery & Resilience](#recovery--resilience)
- [API Reference](#api-reference)

---

## Overview

Traditional agent interactions are single-turn: the user asks a question, the agent responds, and the conversation moves on. But some goals — like "migrate this Flask app to FastAPI" or "refactor the entire auth module" — require dozens of steps across multiple files, with planning, execution, and verification.

The long-horizon task system solves this by:

1. **Persistent state** — Task progress is saved to disk after every step
2. **Structured plans** — Tasks have ordered plans with individual steps
3. **Lifecycle management** — Tasks can be created, paused, resumed, and cancelled
4. **Session association** — Tasks are linked to REPL sessions for continuity
5. **Parallel execution** — DAG-based parallel step execution (Phase 3)

---

## Core Concepts

### TaskState

The central data structure representing a long-horizon task:

```python
@dataclass
class TaskState:
    task_id: str          # Unique ID: task-YYYYMMDD-HHMMSS-XXXXXX
    goal: str             # Original user prompt
    plan: list[Step]      # Ordered list of steps
    status: TaskStatus    # pending | running | paused | completed | failed
    current_step_index: int
    iteration_count: int
    plan_version: int
    created_at: str
    updated_at: str
    last_checkpoint: str  # ISO timestamp of last save
```

### Step

Each step in a plan:

```python
@dataclass
class Step:
    description: str
    status: StepStatus    # pending | in_progress | completed | failed | skipped
    result: Optional[StepResult]
    failure: Optional[StepFailure]
    metadata: dict        # Tool calls, files touched, etc.
```

### TaskStatus

```
pending   →  running  →  completed
   ↓           ↓
paused      failed
```

- **pending** — Created but not yet started
- **running** — Currently executing
- **paused** — Explicitly paused by user
- **completed** — All steps finished successfully
- **failed** — A step failed and was not recoverable

---

## CLI Usage

### List all tasks

```bash
$ wisp task list

Long-horizon tasks (3):

  completed    5/5   Migrate Flask to FastAPI
  running      2/8   Refactor auth module
  paused       1/3   Update dependencies
```

### Show task details

```bash
$ wisp task status task-20260516-143022-a1b2c3d4

Task: task-20260516-143022-a1b2c3d4
  Goal:     Migrate Flask to FastAPI
  Status:   running
  Progress: 2/8 (25.0%)
  Completed: 1  Failed: 0
  Plan version: 3
  Current step: Replace Flask imports with FastAPI equivalents
  Last checkpoint: 2026-05-16T14:35:12.123456
```

### Start a new task

```bash
$ wisp task start "Migrate Flask app to FastAPI"

✓ Created long-horizon task: task-20260516-143022-a1b2c3d4
Goal: Migrate Flask app to FastAPI
Initial plan: 1 step(s)
Use task_status(task_id='task-20260516-143022-a1b2c3d4') to check progress.
```

### Pause a running task

```bash
$ wisp task pause task-20260516-143022-a1b2c3d4

Task task-20260516-143022-a1b2c3d4 paused at step 2/8.
```

### Resume a paused task

```bash
$ wisp task resume task-20260516-143022-a1b2c3d4

Resumed task: task-20260516-143022-a1b2c3d4
Goal: Migrate Flask app to FastAPI
Progress: 2/8 steps
Current step: Replace Flask imports with FastAPI equivalents
```

### Cancel a task

```bash
$ wisp task cancel task-20260516-143022-a1b2c3d4

Task task-20260516-143022-a1b2c3d4 cancelled.
```

---

## Tool Usage (for the Agent)

When operating in the REPL or via API, the agent has access to six long-horizon tools:

### `run_long_task`

Create a new long-horizon task with an initial plan.

```json
{
  "goal": "Migrate Flask app to FastAPI"
}
```

Returns the task ID and initial plan.

### `task_status`

Check the current status of a task.

```json
{
  "task_id": "task-20260516-143022-a1b2c3d4"
}
```

Returns progress percentage, current step, completed/failed counts.

### `list_tasks`

List tasks filtered by status.

```json
{
  "status_filter": "running"
}
```

Valid filters: `all`, `running`, `paused`, `completed`, `failed`.

### `pause_task`

Pause a running task, saving its current state.

```json
{
  "task_id": "task-20260516-143022-a1b2c3d4"
}
```

### `resume_task`

Resume a paused or crashed task.

```json
{
  "task_id": "task-20260516-143022-a1b2c3d4"
}
```

### `cancel_task`

Cancel a task, marking it as failed.

```json
{
  "task_id": "task-20260516-143022-a1b2c3d4"
}
```

---

## Session Integration

Long-horizon tasks are automatically associated with REPL sessions. When the agent calls `run_long_task` during a REPL session, the task ID is added to the session's `task_ids` list.

### Benefits

- **Automatic checkpointing** — When a session is saved, all running associated tasks are checkpointed
- **Session overview** — `wisp session show <id>` displays all associated tasks with their status
- **Cross-session continuity** — Resume a session and see which tasks are still running

### Example

```bash
$ wisp session list

Saved sessions (2):

  20260516-143022-abcdef
    Title:    Migrate Flask to FastAPI
    Model:    kimi-k2.5:cloud
    Started:  2026-05-16T14:30:22
    Updated:  2026-05-16T14:35:12
    Messages: 12
    Tasks:    1
    Continue: wisp -S 20260516-143022-abcdef "your next question"

$ wisp session show 20260516-143022-abcdef

... session preview ...

Associated tasks (1):
  running       25.0%  Migrate Flask to FastAPI

  Continue: wisp -S 20260516-143022-abcdef "your next question"
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                         User / CLI                           │
└──────────────────────┬──────────────────────────────────────┘
                       │
       ┌───────────────┼───────────────┐
       ▼               ▼               ▼
  ┌─────────┐    ┌─────────┐    ┌──────────┐
  │  CLI    │    │  REPL   │    │  Agent   │
  │ Commands│    │ Session │    │  Tools   │
  └────┬────┘    └────┬────┘    └────┬─────┘
       │              │              │
       └──────────────┼──────────────┘
                      ▼
            ┌─────────────────┐
            │   TaskManager   │
            │  (orchestrator) │
            └────────┬────────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐
  │TaskState │ │TaskStorage│ │  DAG     │
  │(dataclass)│ │(disk I/O) │ │(parallel)│
  └──────────┘ └──────────┘ └──────────┘
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| `TaskState` | `wisp/long_horizon/state.py` | Core data model |
| `TaskStorage` | `wisp/long_horizon/storage.py` | Atomic disk persistence |
| `ParallelTaskExecutor` | `wisp/long_horizon/dag.py` | DAG-based parallel execution |
| `TaskManager` | `wisp/long_horizon/manager.py` | High-level orchestration |
| Tool implementations | `wisp/tools/long_horizon.py` | 6 agent-facing tools |
| CLI commands | `wisp/__main__.py` | `wisp task *` subcommands |

---

## Storage Format

Tasks are stored in `~/.config/wisp/tasks/`:

```
~/.config/wisp/tasks/
├── task-20260516-143022-a1b2c3d4.json   # Checkpoint file
├── task-20260516-143022-a1b2c3d4.bak   # Atomic write backup
└── index.json                            # Registry for fast listing
```

### Checkpoint File

```json
{
  "task_id": "task-20260516-143022-a1b2c3d4",
  "goal": "Migrate Flask app to FastAPI",
  "status": "running",
  "plan": [
    {
      "description": "Replace Flask imports with FastAPI equivalents",
      "status": "in_progress",
      "result": null,
      "failure": null,
      "metadata": {}
    }
  ],
  "current_step_index": 2,
  "iteration_count": 5,
  "plan_version": 3,
  "created_at": "2026-05-16T14:30:22.123456",
  "updated_at": "2026-05-16T14:35:12.654321",
  "last_checkpoint": "2026-05-16T14:35:12.654321"
}
```

### Index Registry

```json
{
  "task-20260516-143022-a1b2c3d4": {
    "task_id": "task-20260516-143022-a1b2c3d4",
    "status": "running",
    "goal": "Migrate Flask app to FastAPI",
    "current_step": 2,
    "total_steps": 8,
    "updated_at": "2026-05-16T14:35:12.654321"
  }
}
```

The index enables O(1) listing without loading full checkpoint files.

---

## Recovery & Resilience

### Atomic Writes

All checkpoint writes use atomic file operations:
1. Write to `.tmp` file
2. Rename to `.bak` (backup)
3. Rename to final `.json`

If a crash occurs during write, the previous checkpoint remains intact.

### Corruption Handling

- Corrupt checkpoint files are skipped during index rebuild
- `load()` returns `None` for corrupt files
- The index is rebuilt from valid checkpoints on demand

### Session Recovery

When a session is loaded, associated tasks are displayed with their current status. Running tasks are automatically checkpointed when the session is saved.

---

## API Reference

### TaskStorage

```python
from wisp.long_horizon.storage import TaskStorage

storage = TaskStorage()

# Save a task
storage.save(task_state)

# Load a task
state = storage.load("task-20260516-143022-a1b2c3d4")

# Delete a task
storage.delete("task-20260516-143022-a1b2c3d4")

# List all tasks
all_tasks = storage.list_all()

# List by status
running = storage.list_by_status("running")
```

### TaskState

```python
from wisp.long_horizon.state import TaskState, Step, TaskStatus

# Create a task
state = TaskState.create(goal="Refactor auth module")

# Add steps
state.add_step("Extract password hashing")
state.add_step("Add JWT token support")

# Progress
state.advance()  # Move to next step
state.complete_current()  # Mark current step as done
state.fail_current("Reason for failure")

# Checkpoint
state.checkpoint()

# Properties
state.progress_pct  # 0.0 - 100.0
state.is_complete   # True if all steps done
state.is_failed     # True if any step failed
state.current_step  # Current Step object
```

### TaskManager

```python
from wisp.long_horizon.manager import TaskManager

manager = TaskManager(agent=agent)

# Start a task
task_id = await manager.start("Migrate Flask to FastAPI")

# Check status
status = manager.status(task_id)

# Control
await manager.pause(task_id)
await manager.resume(task_id)
await manager.cancel(task_id)
```

---

## Design Decisions

1. **JSON over SQLite** — Human-readable, easy to debug, no schema migrations
2. **Flat file per task** — Simple, no locking complexity between tasks
3. **Index registry** — Fast listing without loading all checkpoints
4. **Atomic writes** — Crash-safe checkpointing
5. **Session association** — Natural integration with existing session system
6. **DAG parallel execution** — Independent steps run concurrently for speed

---

## Future Enhancements

- **Task dependencies** — Steps that depend on other tasks (not just within-task DAG)
- **Web dashboard** — Visual task progress viewer
- **Notifications** — Alert when long-running tasks complete
- **Auto-resume** — Automatically resume crashed tasks on agent restart
- **Plan evolution** — LLM-driven plan refinement based on execution results
