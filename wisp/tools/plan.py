"""Planning tools for Wisp — create, update, and mark plan tasks.

Delegates to wisp.planner for plan storage and management.
"""

import logging


logger = logging.getLogger(__name__)


def tool_plan_task(goal: str, tasks: str, workspace: str = ".") -> str:
    """Create a structured plan with subtasks.

    tasks should be a newline-separated list in this format:
      1. [low] Description here — files: a.py, b.py
      2. [medium] Another task — deps: 1 — files: c.py
      3. [high] Final task — deps: 1, 2
    """
    from wisp.planner import PlanStore, parse_plan_from_text

    plan = parse_plan_from_text(tasks, goal=goal, workspace=workspace)
    if not plan.tasks:
        return "⚠ No tasks parsed. Use format: '1. [low] Description — files: a.py'"

    store = PlanStore()
    store.save(plan)

    lines = [f"✓ Created plan: {plan.id}", f"Goal: {plan.goal}", f"Tasks: {len(plan.tasks)}", ""]
    for i, t in enumerate(plan.tasks, 1):
        deps = f" (deps: {', '.join(t.dependencies)})" if t.dependencies else ""
        files = f" [files: {', '.join(t.files_to_touch)}]" if t.files_to_touch else ""
        lines.append(f"  {i}. [{t.estimated_complexity}] {t.description}{deps}{files}")

    return "\n".join(lines)


def tool_mark_step_done(task_id: str, notes: str = "", workspace: str = ".") -> str:
    """Mark a plan task as completed."""
    from wisp.planner import PlanStore

    store = PlanStore()
    plan = store.load_active(workspace)
    if not plan:
        return "⚠ No active plan for this workspace."

    if plan.complete_task(task_id, notes=notes):
        store.save(plan)
        done, total = plan.progress()
        return f"✓ Marked task {task_id} as done. Progress: {done}/{total}"
    return f"⚠ Could not complete task {task_id}. Is it in progress?"


def tool_update_plan(task_id: str, status: str, notes: str = "", workspace: str = ".") -> str:
    """Update a plan task's status (pending, in_progress, done, skipped, blocked)."""
    from wisp.planner import PlanStore

    store = PlanStore()
    plan = store.load_active(workspace)
    if not plan:
        return "⚠ No active plan for this workspace."

    task = plan.get_task(task_id)
    if not task:
        return f"⚠ Task {task_id} not found."

    if status == "in_progress":
        plan.start_task(task_id)
    elif status == "done":
        plan.complete_task(task_id, notes)
    elif status == "skipped":
        plan.skip_task(task_id, notes)
    else:
        task.status = status
        if notes:
            task.notes = notes
        plan.touch()

    store.save(plan)
    done, total = plan.progress()
    return f"✓ Updated task {task_id} to '{status}'. Progress: {done}/{total}"
