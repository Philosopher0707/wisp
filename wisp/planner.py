"""Structured planning and task decomposition for Wisp.

Breaks down user requests into subtasks with dependencies, tracks progress,
and persists plans across agent turns.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from wisp.config import WISP_CONFIG_DIR

logger = logging.getLogger(__name__)

PLANS_DIR = WISP_CONFIG_DIR / "plans"
_MAX_PLANS = 10  # Keep last N plans


@dataclass
class Task:
    """A single subtask within a plan."""

    id: str
    description: str
    estimated_complexity: str = "medium"  # low, medium, high
    dependencies: list[str] = field(default_factory=list)
    files_to_touch: list[str] = field(default_factory=list)
    status: str = "pending"  # pending, in_progress, done, blocked, skipped
    notes: str = ""
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Task:
        return cls(
            id=data.get("id", ""),
            description=data.get("description", ""),
            estimated_complexity=data.get("estimated_complexity", "medium"),
            dependencies=data.get("dependencies", []),
            files_to_touch=data.get("files_to_touch", []),
            status=data.get("status", "pending"),
            notes=data.get("notes", ""),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
        )

    def is_ready(self, done_ids: set[str]) -> bool:
        """True if all dependencies are satisfied."""
        return all(dep in done_ids for dep in self.dependencies)


@dataclass
class Plan:
    """A structured plan with tasks and progress tracking."""

    goal: str
    workspace: str
    id: str = ""
    tasks: list[Task] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    status: str = "active"  # active, completed, aborted
    current_task_id: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = _generate_plan_id()
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "workspace": self.workspace,
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "current_task_id": self.current_task_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Plan:
        return cls(
            goal=data.get("goal", ""),
            workspace=data.get("workspace", ""),
            id=data.get("id", ""),
            tasks=[Task.from_dict(t) for t in data.get("tasks", [])],
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            status=data.get("status", "active"),
            current_task_id=data.get("current_task_id", ""),
        )

    def touch(self):
        self.updated_at = _now_iso()

    def next_task(self) -> Optional[Task]:
        """Return the next ready task (all dependencies done, status pending)."""
        done_ids = {t.id for t in self.tasks if t.status == "done"}
        for task in self.tasks:
            if task.status == "pending" and task.is_ready(done_ids):
                return task
        return None

    def progress(self) -> tuple[int, int]:
        """Return (done_count, total_count)."""
        done = sum(1 for t in self.tasks if t.status == "done")
        return done, len(self.tasks)

    def is_complete(self) -> bool:
        """True if all tasks are done or skipped."""
        return all(t.status in ("done", "skipped") for t in self.tasks)

    def get_task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.id == task_id:
                return t
        return None

    def start_task(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task and task.status == "pending":
            task.status = "in_progress"
            task.started_at = _now_iso()
            self.current_task_id = task_id
            self.touch()
            return True
        return False

    def complete_task(self, task_id: str, notes: str = "") -> bool:
        task = self.get_task(task_id)
        if task and task.status in ("in_progress", "pending"):
            task.status = "done"
            task.completed_at = _now_iso()
            if notes:
                task.notes = notes
            self.current_task_id = ""
            self.touch()
            return True
        return False

    def skip_task(self, task_id: str, reason: str = "") -> bool:
        task = self.get_task(task_id)
        if task:
            task.status = "skipped"
            if reason:
                task.notes = reason
            self.touch()
            return True
        return False

    def abort(self):
        self.status = "aborted"
        self.touch()

    def format_for_prompt(self) -> str:
        """Format the plan as a system prompt block."""
        if not self.tasks:
            return ""

        done, total = self.progress()
        lines = [f"## Active Plan: {self.goal}"]
        lines.append(f"Progress: {done}/{total} tasks complete")

        next_task = self.next_task()
        if next_task:
            lines.append(f"Next task: {next_task.description} (complexity: {next_task.estimated_complexity})")
        elif self.is_complete():
            lines.append("All tasks complete!")
        else:
            lines.append("No ready tasks — some may be blocked.")

        lines.append("")
        lines.append("Tasks:")
        for i, t in enumerate(self.tasks, 1):
            status_icon = {
                "done": "✓",
                "in_progress": "→",
                "pending": "○",
                "blocked": "⊘",
                "skipped": "⊘",
            }.get(t.status, "?")
            dep_str = f" [deps: {', '.join(t.dependencies)}]" if t.dependencies else ""
            lines.append(f"  {status_icon} {i}. {t.description}{dep_str}")
            if t.notes:
                lines.append(f"      Note: {t.notes}")

        return "\n".join(lines)


# ── Persistence ────────────────────────────────────────────────────────

class PlanStore:
    """Persist and retrieve plans."""

    def __init__(self):
        PLANS_DIR.mkdir(parents=True, exist_ok=True)

    def save(self, plan: Plan) -> None:
        path = PLANS_DIR / f"{plan.id}.json"
        try:
            path.write_text(
                json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.debug("Saved plan %s (%d tasks)", plan.id, len(plan.tasks))
            self._rotate()
        except OSError as e:
            logger.error("Failed to save plan %s: %s", plan.id, e)

    def load(self, plan_id: str) -> Optional[Plan]:
        path = PLANS_DIR / f"{plan_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Plan.from_dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load plan %s: %s", plan_id, e)
            return None

    def load_active(self, workspace: str) -> Optional[Plan]:
        """Load the most recent active plan for a workspace."""
        plans = self._list_plans()
        for p in sorted(plans, key=lambda x: x["updated_at"], reverse=True):
            if p["workspace"] == workspace and p["status"] == "active":
                return self.load(p["id"])
        return None

    def list_all(self) -> list[dict]:
        """List all plan metadata (no tasks)."""
        return self._list_plans()

    def delete(self, plan_id: str) -> bool:
        path = PLANS_DIR / f"{plan_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def clear(self) -> None:
        for path in PLANS_DIR.glob("*.json"):
            path.unlink()

    def _list_plans(self) -> list[dict]:
        plans = []
        if not PLANS_DIR.exists():
            return plans
        for path in sorted(PLANS_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                plans.append({
                    "id": data.get("id", path.stem),
                    "goal": data.get("goal", "")[:60],
                    "workspace": data.get("workspace", ""),
                    "status": data.get("status", "?"),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "task_count": len(data.get("tasks", [])),
                    "done_count": sum(1 for t in data.get("tasks", []) if t.get("status") == "done"),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return plans

    def _rotate(self) -> None:
        plans = sorted(PLANS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if len(plans) > _MAX_PLANS:
            for old in plans[_MAX_PLANS:]:
                old.unlink()
                logger.info("Rotated old plan: %s", old.stem)


# ── Helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generate_plan_id() -> str:
    return f"plan-{uuid.uuid4().hex[:12]}"


def parse_plan_from_text(text: str, goal: str, workspace: str) -> Plan:
    """Parse a plan from LLM-generated text.

    Expected format:
      1. [low] Description here — files: a.py, b.py
      2. [medium] Another task — deps: 1 — files: c.py
      3. [high] Final task — deps: 1, 2
    """
    plan = Plan(goal=goal, workspace=workspace)
    task_pattern = re.compile(
        r"^\s*(?:\d+\.?\s*)?\[(low|medium|high)\]\s*(.+?)(?:\s*—\s*|\s*--\s*|\s*-\s*|\s*$)(.*)$",
        re.IGNORECASE,
    )

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        match = task_pattern.match(line)
        if match:
            complexity = match.group(1).lower()
            description = match.group(2).strip()
            extras = match.group(3).strip()

            files = []
            deps = []

            if extras:
                # Parse files: ...
                files_match = re.search(r"files?:\s*([^—]+)", extras, re.IGNORECASE)
                if files_match:
                    files = [f.strip() for f in files_match.group(1).split(",") if f.strip()]

                # Parse deps: ... or depends: ...
                deps_match = re.search(r"dep(?:ends?|s)?:\s*([^—]+)", extras, re.IGNORECASE)
                if deps_match:
                    dep_text = deps_match.group(1)
                    # Extract numbers
                    deps = [f"task-{d.strip()}" for d in re.findall(r"\d+", dep_text)]

            task = Task(
                id=f"task-{len(plan.tasks) + 1}",
                description=description,
                estimated_complexity=complexity,
                dependencies=deps,
                files_to_touch=files,
            )
            plan.tasks.append(task)

    return plan
