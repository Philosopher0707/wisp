"""Data model for long-horizon task execution.

Defines TaskState, Step, Plan, and status enums.
All dataclasses support JSON serialization for checkpointing.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ── Helpers (must be defined before dataclasses that use them) ─────────

def _now_iso() -> str:
    """Current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _generate_task_id() -> str:
    """Generate a unique, sortable task ID."""
    now = datetime.now(timezone.utc)
    return f"task-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"


# ── Enums ──────────────────────────────────────────────────────────────

class TaskStatus(Enum):
    """Lifecycle states for a long-horizon task."""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    REPLANNING = "replanning"


class StepStatus(Enum):
    """Lifecycle states for an individual step."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Step ─────────────────────────────────────────────────────────────

@dataclass
class Step:
    """Atomic unit of work within a long-horizon task.

    Attributes:
        id: Unique identifier (e.g. "step-1", "step-1a").
        description: Natural language instruction for the step.
        status: Current execution state.
        tool_calls: Record of tools invoked during execution.
        result: Output or summary from successful execution.
        error: Failure reason if the step failed.
        duration_ms: Wall-clock time spent executing.
        dependencies: Step IDs that must complete before this one.
        parallel_group: Optional group name for batched execution.
        attempt_count: How many times this step has been attempted.
        max_attempts: Maximum retry attempts (default 3).
    """
    id: str
    description: str
    status: StepStatus = StepStatus.PENDING
    tool_calls: list[dict] = field(default_factory=list)
    result: str = ""
    error: str = ""
    duration_ms: int = 0
    dependencies: list[str] = field(default_factory=list)
    parallel_group: str | None = None
    attempt_count: int = 0
    max_attempts: int = 3

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (JSON-compatible)."""
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "tool_calls": self.tool_calls,
            "result": self.result,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "dependencies": self.dependencies,
            "parallel_group": self.parallel_group,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Step:
        """Deserialize from a plain dict."""
        return cls(
            id=data["id"],
            description=data["description"],
            status=StepStatus(data.get("status", "pending")),
            tool_calls=data.get("tool_calls", []),
            result=data.get("result", ""),
            error=data.get("error", ""),
            duration_ms=data.get("duration_ms", 0),
            dependencies=data.get("dependencies", []),
            parallel_group=data.get("parallel_group"),
            attempt_count=data.get("attempt_count", 0),
            max_attempts=data.get("max_attempts", 3),
        )


# ── Plan ─────────────────────────────────────────────────────────────

@dataclass
class Plan:
    """A versioned plan containing an ordered list of steps.

    Attributes:
        version: Incremented on each replan (1 = initial).
        steps: Ordered list of steps to execute.
        created_at: ISO-8601 timestamp of plan creation.
        reason: Why this plan was created ("initial", "replan_after_step_4_failed").
    """
    version: int
    steps: list[Step]
    created_at: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        return cls(
            version=data["version"],
            steps=[Step.from_dict(s) for s in data.get("steps", [])],
            created_at=data.get("created_at", _now_iso()),
            reason=data.get("reason", "initial"),
        )


# ── StepResult / StepFailure ─────────────────────────────────────────

@dataclass
class StepResult:
    """Record of a successfully completed step."""
    step_id: str
    result: str
    duration_ms: int = 0
    completed_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "result": self.result,
            "duration_ms": self.duration_ms,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepResult:
        return cls(
            step_id=data["step_id"],
            result=data["result"],
            duration_ms=data.get("duration_ms", 0),
            completed_at=data.get("completed_at", _now_iso()),
        )


@dataclass
class StepFailure:
    """Record of a failed step attempt."""
    step_id: str
    error: str
    attempt: int
    failed_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "error": self.error,
            "attempt": self.attempt,
            "failed_at": self.failed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StepFailure:
        return cls(
            step_id=data["step_id"],
            error=data["error"],
            attempt=data.get("attempt", 1),
            failed_at=data.get("failed_at", _now_iso()),
        )


# ── TaskState ────────────────────────────────────────────────────────

@dataclass
class TaskState:
    """Canonical checkpoint for a long-horizon task.

    This is the full serializable state of an in-flight or completed task.
    Saved to disk after every step and on every state transition.

    Attributes:
        task_id: Unique identifier (auto-generated if not provided).
        goal: Original user prompt / task description.
        plan: Current plan with ordered steps.
        current_step_index: Index of the active step in plan.steps.
        plan_version: Incremented on each replan.
        completed_steps: History of successful completions.
        failed_steps: History of failures (for learning / retry patterns).
        replan_history: All previous plan versions for audit.
        status: Current lifecycle state.
        created_at: ISO-8601 timestamp of task creation.
        updated_at: ISO-8601 timestamp of last modification.
        last_checkpoint: ISO-8601 timestamp of last disk save.
        max_iterations: Maximum total steps before forced failure.
        step_timeout: Per-step timeout in seconds.
        replan_on_failure: Whether to replan when a step fails.
        max_replans: Maximum number of replanning cycles.
        accumulated_context: Summarized results for context window management.
        context_token_count: Approximate token count of accumulated_context.
    """
    task_id: str
    goal: str
    plan: Plan
    current_step_index: int = 0
    plan_version: int = 1
    completed_steps: list[StepResult] = field(default_factory=list)
    failed_steps: list[StepFailure] = field(default_factory=list)
    replan_history: list[Plan] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    last_checkpoint: str = ""
    max_iterations: int = 100
    step_timeout: float = 300.0
    replan_on_failure: bool = True
    max_replans: int = 3
    accumulated_context: str = ""
    context_token_count: int = 0

    @classmethod
    def create(
        cls,
        goal: str,
        plan_steps: list[Step] | None = None,
        task_id: str | None = None,
        max_iterations: int = 100,
        step_timeout: float = 300.0,
        replan_on_failure: bool = True,
        max_replans: int = 3,
    ) -> TaskState:
        """Factory: create a new TaskState from a goal and optional steps."""
        now = _now_iso()
        tid = task_id or _generate_task_id()
        steps = plan_steps or []
        plan = Plan(version=1, steps=steps, created_at=now, reason="initial")
        return cls(
            task_id=tid,
            goal=goal,
            plan=plan,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            max_iterations=max_iterations,
            step_timeout=step_timeout,
            replan_on_failure=replan_on_failure,
            max_replans=max_replans,
        )

    # ── Derived properties ──────────────────────────────────────────

    @property
    def total_steps(self) -> int:
        return len(self.plan.steps)

    @property
    def completed_count(self) -> int:
        return len(self.completed_steps)

    @property
    def failed_count(self) -> int:
        return len(self.failed_steps)

    @property
    def current_step(self) -> Step | None:
        if 0 <= self.current_step_index < len(self.plan.steps):
            return self.plan.steps[self.current_step_index]
        return None

    @property
    def is_complete(self) -> bool:
        return self.status == TaskStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self.status == TaskStatus.FAILED

    @property
    def progress_pct(self) -> float:
        if not self.plan.steps:
            return 0.0
        return (self.current_step_index / len(self.plan.steps)) * 100

    # ── Serialization ─────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (JSON-compatible)."""
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "plan": self.plan.to_dict(),
            "current_step_index": self.current_step_index,
            "plan_version": self.plan_version,
            "completed_steps": [r.to_dict() for r in self.completed_steps],
            "failed_steps": [f.to_dict() for f in self.failed_steps],
            "replan_history": [p.to_dict() for p in self.replan_history],
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_checkpoint": self.last_checkpoint,
            "max_iterations": self.max_iterations,
            "step_timeout": self.step_timeout,
            "replan_on_failure": self.replan_on_failure,
            "max_replans": self.max_replans,
            "accumulated_context": self.accumulated_context,
            "context_token_count": self.context_token_count,
        }

    def to_json(self, indent: int = 2) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskState:
        """Deserialize from a plain dict."""
        return cls(
            task_id=data["task_id"],
            goal=data["goal"],
            plan=Plan.from_dict(data.get("plan", {"version": 1, "steps": [], "created_at": _now_iso(), "reason": "initial"})),
            current_step_index=data.get("current_step_index", 0),
            plan_version=data.get("plan_version", 1),
            completed_steps=[StepResult.from_dict(r) for r in data.get("completed_steps", [])],
            failed_steps=[StepFailure.from_dict(f) for f in data.get("failed_steps", [])],
            replan_history=[Plan.from_dict(p) for p in data.get("replan_history", [])],
            status=TaskStatus(data.get("status", "pending")),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            last_checkpoint=data.get("last_checkpoint", ""),
            max_iterations=data.get("max_iterations", 100),
            step_timeout=data.get("step_timeout", 300.0),
            replan_on_failure=data.get("replan_on_failure", True),
            max_replans=data.get("max_replans", 3),
            accumulated_context=data.get("accumulated_context", ""),
            context_token_count=data.get("context_token_count", 0),
        )

    @classmethod
    def from_json(cls, text: str) -> TaskState:
        """Deserialize from a JSON string."""
        return cls.from_dict(json.loads(text))

    # ── Mutation helpers ────────────────────────────────────────────

    def touch(self) -> None:
        """Update the updated_at timestamp."""
        self.updated_at = _now_iso()

    def advance(self) -> None:
        """Move to the next step."""
        self.current_step_index += 1
        self.touch()

    def mark_step_completed(self, result: str, duration_ms: int = 0) -> None:
        """Mark the current step as completed and advance."""
        step = self.current_step
        if step:
            step.status = StepStatus.COMPLETED
            step.result = result
            step.duration_ms = duration_ms
            self.completed_steps.append(StepResult(
                step_id=step.id,
                result=result,
                duration_ms=duration_ms,
            ))
        self.advance()

    def mark_step_failed(self, error: str) -> None:
        """Mark the current step as failed, record failure."""
        step = self.current_step
        if step:
            step.status = StepStatus.FAILED
            step.error = error
            step.attempt_count += 1
            self.failed_steps.append(StepFailure(
                step_id=step.id,
                error=error,
                attempt=step.attempt_count,
            ))
        self.touch()

    def set_plan(self, plan: Plan) -> None:
        """Set a new plan (used during replanning)."""
        self.replan_history.append(self.plan)
        self.plan = plan
        self.plan_version = plan.version
        self.current_step_index = 0
        self.touch()

    def set_status(self, status: TaskStatus) -> None:
        """Update task status."""
        self.status = status
        self.touch()
