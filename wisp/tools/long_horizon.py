"""Long-horizon task tools for Wisp.

These tools provide CRUD operations for long-horizon tasks via the
storage layer. Actual execution is handled by TaskManager or the
transport layer.

Tools:
    run_long_task: Create a new task with initial plan.
    resume_task: Resume a paused/crashed task.
    task_status: Get current task progress.
    list_tasks: List all tasks.
    pause_task: Pause a running task.
    cancel_task: Cancel a task.
"""

from __future__ import annotations

import json
import logging

from wisp.long_horizon.state import TaskState, Step, TaskStatus
from wisp.long_horizon.storage import TaskStorage

logger = logging.getLogger(__name__)


# ── Tool implementations ─────────────────────────────────────────────

def tool_run_long_task(
    goal: str,
    max_iterations: int = 50,
    step_timeout: int = 300,
    parallelize: bool = True,
    workspace: str = ".",
) -> str:
    """Create and start a new long-horizon task.

    Creates a task checkpoint with an initial plan. The task will be
    executed by the agent in the background.

    Returns the task_id for tracking.
    """
    storage = TaskStorage()
    state = TaskState.create(
        goal=goal,
        max_iterations=max_iterations,
        step_timeout=float(step_timeout),
        replan_on_failure=True,
        max_replans=3,
    )

    # Create a simple initial plan (1 step = the goal itself)
    # The runner will replan with the agent when it starts
    state.plan.steps = [
        Step(id="step-1", description=goal),
    ]

    storage.save(state)

    return json.dumps({
        "status": "ok",
        "tool": "run_long_task",
        "data": (
            f"Created long-horizon task: {state.task_id}\n"
            f"Goal: {goal}\n"
            f"Initial plan: {len(state.plan.steps)} step(s)\n"
            f"Use task_status(task_id='{state.task_id}') to check progress."
        ),
        "metadata": {
            "task_id": state.task_id,
            "goal": goal,
            "total_steps": state.total_steps,
        },
    })


def tool_resume_task(
    task_id: str,
    workspace: str = ".",
) -> str:
    """Resume a previously started long-horizon task from its checkpoint.

    The task must exist in storage and not be completed.
    """
    storage = TaskStorage()
    state = storage.load(task_id)

    if state is None:
        return json.dumps({
            "status": "error",
            "tool": "resume_task",
            "data": f"Task not found: {task_id}",
            "metadata": {},
        })

    if state.is_complete:
        return json.dumps({
            "status": "ok",
            "tool": "resume_task",
            "data": f"Task {task_id} is already completed.",
            "metadata": {"task_id": task_id, "status": "completed"},
        })

    state.set_status(TaskStatus.RUNNING)
    storage.save(state)

    return json.dumps({
        "status": "ok",
        "tool": "resume_task",
        "data": (
            f"Resumed task: {task_id}\n"
            f"Goal: {state.goal}\n"
            f"Progress: {state.current_step_index}/{state.total_steps} steps\n"
            f"Current step: {state.current_step.description if state.current_step else 'None'}"
        ),
        "metadata": {
            "task_id": task_id,
            "goal": state.goal,
            "current_step": state.current_step_index,
            "total_steps": state.total_steps,
        },
    })


def tool_task_status(
    task_id: str,
) -> str:
    """Get the current status and progress of a long-horizon task."""
    storage = TaskStorage()
    state = storage.load(task_id)

    if state is None:
        return json.dumps({
            "status": "error",
            "tool": "task_status",
            "data": f"Task not found: {task_id}",
            "metadata": {},
        })

    current = state.current_step
    lines = [
        f"Task: {state.task_id}",
        f"Goal: {state.goal}",
        f"Status: {state.status.value}",
        f"Progress: {state.current_step_index}/{state.total_steps} steps ({round(state.progress_pct, 1)}%)",
        f"Completed: {state.completed_count}",
        f"Failed: {state.failed_count}",
        f"Plan version: {state.plan_version}",
    ]
    if current:
        lines.append(f"Current step: {current.description}")
    if state.last_checkpoint:
        lines.append(f"Last checkpoint: {state.last_checkpoint}")

    return json.dumps({
        "status": "ok",
        "tool": "task_status",
        "data": "\n".join(lines),
        "metadata": {
            "task_id": task_id,
            "status": state.status.value,
            "current_step": state.current_step_index,
            "total_steps": state.total_steps,
            "progress_pct": round(state.progress_pct, 1),
        },
    })


def tool_list_tasks(
    status_filter: str = "all",
) -> str:
    """List all long-horizon tasks with their statuses.

    Args:
        status_filter: Filter by status (all, pending, running, paused, completed, failed).
    """
    storage = TaskStorage()
    tasks = storage.list_by_status(status_filter) if status_filter != "all" else storage.list_all()

    if not tasks:
        return json.dumps({
            "status": "ok",
            "tool": "list_tasks",
            "data": f"No tasks found (filter: {status_filter}).",
            "metadata": {"count": 0},
        })

    lines = [f"Tasks (filter: {status_filter}):"]
    for t in tasks:
        progress = f"{t.get('current_step', 0)}/{t.get('total_steps', 0)}"
        lines.append(
            f"  {t['task_id']}: {t['status']} ({progress}) — {t['goal'][:60]}"
        )

    return json.dumps({
        "status": "ok",
        "tool": "list_tasks",
        "data": "\n".join(lines),
        "metadata": {"count": len(tasks)},
    })


def tool_pause_task(
    task_id: str,
) -> str:
    """Pause a running long-horizon task.

    Saves the current checkpoint and updates status to paused.
    """
    storage = TaskStorage()
    state = storage.load(task_id)

    if state is None:
        return json.dumps({
            "status": "error",
            "tool": "pause_task",
            "data": f"Task not found: {task_id}",
            "metadata": {},
        })

    state.set_status(TaskStatus.PAUSED)
    storage.save(state)

    return json.dumps({
        "status": "ok",
        "tool": "pause_task",
        "data": f"Task {task_id} paused at step {state.current_step_index}/{state.total_steps}.",
        "metadata": {
            "task_id": task_id,
            "status": "paused",
            "current_step": state.current_step_index,
        },
    })


def tool_cancel_task(
    task_id: str,
) -> str:
    """Cancel a long-horizon task.

    Marks the task as failed and archives the checkpoint.
    """
    storage = TaskStorage()
    state = storage.load(task_id)

    if state is None:
        return json.dumps({
            "status": "error",
            "tool": "cancel_task",
            "data": f"Task not found: {task_id}",
            "metadata": {},
        })

    state.set_status(TaskStatus.FAILED)
    storage.save(state)

    return json.dumps({
        "status": "ok",
        "tool": "cancel_task",
        "data": f"Task {task_id} cancelled.",
        "metadata": {
            "task_id": task_id,
            "status": "failed",
        },
    })


def tool_cleanup_tasks(
    status_filter: str = "completed",
    older_than_days: int = 7,
    dry_run: bool = True,
) -> str:
    """Clean up old long-horizon tasks.

    Args:
        status_filter: Remove tasks with this status (completed, failed, all).
        older_than_days: Only remove tasks older than this many days.
        dry_run: If True, show what would be removed without deleting.
    """
    import time
    from pathlib import Path

    storage = TaskStorage()
    tasks_dir = Path(storage.tasks_dir)
    if not tasks_dir.exists():
        return json.dumps({
            "status": "ok",
            "tool": "cleanup_tasks",
            "data": "No tasks directory found.",
            "metadata": {"removed": 0},
        })

    cutoff = time.time() - (older_than_days * 86400)
    removed = []
    skipped = []

    for task_file in tasks_dir.glob("*.json"):
        try:
            import json
            data = json.loads(task_file.read_text())
            status = data.get("status", "")
            updated = data.get("updated_at", "")

            # Parse updated_at timestamp
            try:
                from datetime import datetime
                updated_ts = datetime.fromisoformat(updated).timestamp()
            except Exception:
                updated_ts = task_file.stat().st_mtime

            # Check status filter
            if status_filter != "all" and status != status_filter:
                skipped.append((task_file.stem, status, "status mismatch"))
                continue

            # Check age
            if updated_ts > cutoff:
                skipped.append((task_file.stem, status, "too recent"))
                continue

            if dry_run:
                removed.append((task_file.stem, status, updated))
            else:
                task_file.unlink()
                removed.append((task_file.stem, status, updated))
        except Exception as e:
            skipped.append((task_file.stem, "?", f"error: {e}"))

    lines = []
    if dry_run:
        lines.append(f"DRY RUN — {len(removed)} task(s) would be removed:")
    else:
        lines.append(f"Removed {len(removed)} task(s):")

    for tid, status, updated in removed:
        lines.append(f"  {tid} ({status}) — {updated[:19]}")

    if skipped:
        lines.append(f"\nSkipped {len(skipped)} task(s):")
        for tid, status, reason in skipped[:10]:
            lines.append(f"  {tid} ({status}) — {reason}")
        if len(skipped) > 10:
            lines.append(f"  ... and {len(skipped) - 10} more")

    return json.dumps({
        "status": "ok",
        "tool": "cleanup_tasks",
        "data": "\n".join(lines) if lines else "No tasks to clean up.",
        "metadata": {
            "removed": len(removed),
            "skipped": len(skipped),
            "dry_run": dry_run,
        },
    })


def tool_task_output(
    task_id: str,
) -> str:
    """Get the full output and artifacts of a completed long-horizon task.

    Returns the accumulated results from all completed steps.
    """
    storage = TaskStorage()
    state = storage.load(task_id)

    if state is None:
        return json.dumps({
            "status": "error",
            "tool": "task_output",
            "data": f"Task not found: {task_id}",
            "metadata": {},
        })

    lines = [
        f"Task: {state.task_id}",
        f"Goal: {state.goal}",
        f"Status: {state.status.value}",
        f"Progress: {state.completed_count}/{state.total_steps} steps",
        "",
    ]

    if state.completed_steps:
        lines.append("=== Completed Steps ===")
        for i, step_result in enumerate(state.completed_steps, 1):
            lines.append(f"\n--- Step {i}: {step_result.step_id} ---")
            lines.append(f"Description: {step_result.description}")
            lines.append(f"Result:\n{step_result.result}")
            if step_result.duration_ms:
                lines.append(f"Duration: {step_result.duration_ms}ms")
    else:
        lines.append("No completed steps yet.")

    if state.accumulated_context:
        lines.append("\n=== Accumulated Context ===")
        lines.append(state.accumulated_context)

    return json.dumps({
        "status": "ok",
        "tool": "task_output",
        "data": "\n".join(lines),
        "metadata": {
            "task_id": task_id,
            "status": state.status.value,
            "completed_steps": state.completed_count,
            "total_steps": state.total_steps,
        },
    })
