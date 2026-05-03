"""Progress tracking and reporting for Wisp plans."""

from wisp.planner import Plan, PlanStore


def format_progress(plan: Plan) -> str:
    """Format a plan's progress as a human-readable string."""
    done, total = plan.progress()
    pct = (done / total * 100) if total else 0
    filled = int(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)

    lines = [
        f"Plan: {plan.goal}",
        f"Status: {plan.status}",
        f"Progress: [{bar}] {done}/{total} ({pct:.0f}%)",
        "",
    ]

    for i, task in enumerate(plan.tasks, 1):
        icon = {
            "done": "✓",
            "in_progress": "→",
            "pending": "○",
            "blocked": "⊘",
            "skipped": "⊘",
        }.get(task.status, "?")
        lines.append(f"  {icon} {i}. [{task.estimated_complexity}] {task.description}")
        if task.notes:
            lines.append(f"      {task.notes}")

    return "\n".join(lines)


def list_plans(workspace: str = "") -> str:
    """List all plans, optionally filtered by workspace."""
    store = PlanStore()
    plans = store.list_all()

    if workspace:
        plans = [p for p in plans if p["workspace"] == workspace]

    if not plans:
        return "No plans found."

    lines = [f"{'ID':<20} {'Goal':<40} {'Status':<10} {'Progress':<10} {'Updated'}"]
    lines.append("-" * 100)
    for p in plans:
        goal = p["goal"][:38]
        prog = f"{p['done_count']}/{p['task_count']}"
        updated = p["updated_at"][:10] if p["updated_at"] else "?"
        lines.append(f"{p['id']:<20} {goal:<40} {p['status']:<10} {prog:<10} {updated}")

    return "\n".join(lines)
