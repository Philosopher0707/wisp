"""Tests for wisp.long_horizon.state — dataclasses, enums, serialization."""

from __future__ import annotations

import json
import pytest

from wisp.long_horizon.state import (
    TaskState,
    Step,
    Plan,
    TaskStatus,
    StepStatus,
    StepResult,
    StepFailure,
    _generate_task_id,
    _now_iso,
)


# ══════════════════════════════════════════════════════════════════════
# Step
# ══════════════════════════════════════════════════════════════════════

class TestStep:
    def test_defaults(self):
        s = Step(id="step-1", description="Do something")
        assert s.status == StepStatus.PENDING
        assert s.attempt_count == 0
        assert s.max_attempts == 3
        assert s.dependencies == []

    def test_to_dict_roundtrip(self):
        s = Step(
            id="step-1",
            description="Test",
            status=StepStatus.RUNNING,
            result="done",
            duration_ms=1500,
            dependencies=["step-0"],
            parallel_group="group-a",
            attempt_count=1,
        )
        d = s.to_dict()
        assert d["status"] == "running"
        assert d["result"] == "done"
        restored = Step.from_dict(d)
        assert restored.id == "step-1"
        assert restored.status == StepStatus.RUNNING
        assert restored.dependencies == ["step-0"]
        assert restored.parallel_group == "group-a"

    def test_from_dict_defaults(self):
        s = Step.from_dict({"id": "x", "description": "y"})
        assert s.status == StepStatus.PENDING
        assert s.max_attempts == 3


# ══════════════════════════════════════════════════════════════════════
# Plan
# ══════════════════════════════════════════════════════════════════════

class TestPlan:
    def test_to_dict_roundtrip(self):
        plan = Plan(
            version=2,
            steps=[Step(id="s1", description="A"), Step(id="s2", description="B")],
            created_at="2025-01-01T00:00:00+00:00",
            reason="replan_after_step_1_failed",
        )
        d = plan.to_dict()
        assert d["version"] == 2
        assert len(d["steps"]) == 2
        restored = Plan.from_dict(d)
        assert restored.version == 2
        assert restored.steps[0].id == "s1"


# ══════════════════════════════════════════════════════════════════════
# TaskState
# ══════════════════════════════════════════════════════════════════════

class TestTaskStateCreate:
    def test_minimal(self):
        state = TaskState.create(goal="Refactor auth")
        assert state.goal == "Refactor auth"
        assert state.status == TaskStatus.PENDING
        assert state.plan_version == 1
        assert state.total_steps == 0
        assert state.current_step_index == 0
        assert state.task_id.startswith("task-")

    def test_with_steps(self):
        steps = [
            Step(id="s1", description="Audit"),
            Step(id="s2", description="Migrate"),
        ]
        state = TaskState.create(goal="Migrate", plan_steps=steps)
        assert state.total_steps == 2
        assert state.current_step.id == "s1"

    def test_custom_config(self):
        state = TaskState.create(
            goal="X",
            max_iterations=50,
            step_timeout=600.0,
            replan_on_failure=False,
            max_replans=5,
        )
        assert state.max_iterations == 50
        assert state.step_timeout == 600.0
        assert state.replan_on_failure is False
        assert state.max_replans == 5


class TestTaskStateProperties:
    def test_progress_pct(self):
        state = TaskState.create(
            goal="G",
            plan_steps=[Step(id="s1", description="A"), Step(id="s2", description="B")],
        )
        assert state.progress_pct == 0.0
        state.current_step_index = 1
        assert state.progress_pct == 50.0

    def test_current_step_none_when_done(self):
        state = TaskState.create(goal="G", plan_steps=[Step(id="s1", description="A")])
        state.current_step_index = 1
        assert state.current_step is None

    def test_is_complete_and_failed(self):
        state = TaskState.create(goal="G")
        assert not state.is_complete
        assert not state.is_failed
        state.status = TaskStatus.COMPLETED
        assert state.is_complete
        state.status = TaskStatus.FAILED
        assert state.is_failed


class TestTaskStateMutation:
    def test_mark_step_completed(self):
        state = TaskState.create(
            goal="G",
            plan_steps=[Step(id="s1", description="A"), Step(id="s2", description="B")],
        )
        state.mark_step_completed("Done with A", duration_ms=1200)
        assert state.current_step_index == 1
        assert state.completed_count == 1
        assert state.completed_steps[0].step_id == "s1"
        assert state.completed_steps[0].duration_ms == 1200
        assert state.plan.steps[0].status == StepStatus.COMPLETED

    def test_mark_step_failed(self):
        state = TaskState.create(
            goal="G",
            plan_steps=[Step(id="s1", description="A")],
        )
        state.mark_step_failed("Timeout")
        assert state.failed_count == 1
        assert state.plan.steps[0].status == StepStatus.FAILED
        assert state.plan.steps[0].attempt_count == 1
        assert state.plan.steps[0].error == "Timeout"

    def test_set_plan_replanning(self):
        state = TaskState.create(
            goal="G",
            plan_steps=[Step(id="s1", description="A")],
        )
        state.mark_step_completed("Done")
        new_plan = Plan(
            version=2,
            steps=[Step(id="s2", description="B")],
            created_at=_now_iso(),
            reason="replan",
        )
        state.set_plan(new_plan)
        assert state.plan_version == 2
        assert state.current_step_index == 0
        assert len(state.replan_history) == 1
        assert state.replan_history[0].version == 1

    def test_set_status(self):
        state = TaskState.create(goal="G")
        state.set_status(TaskStatus.RUNNING)
        assert state.status == TaskStatus.RUNNING
        assert state.updated_at > state.created_at


class TestTaskStateSerialization:
    def test_to_json_roundtrip(self):
        state = TaskState.create(
            goal="Migrate Flask to FastAPI",
            plan_steps=[
                Step(id="s1", description="Audit imports", status=StepStatus.COMPLETED),
                Step(id="s2", description="Migrate routes"),
            ],
        )
        state.mark_step_completed("Found 12 imports")
        state.set_status(TaskStatus.RUNNING)

        json_text = state.to_json()
        restored = TaskState.from_json(json_text)

        assert restored.task_id == state.task_id
        assert restored.goal == state.goal
        assert restored.status == TaskStatus.RUNNING
        assert restored.current_step_index == 1
        assert restored.completed_count == 1
        assert restored.plan.steps[0].status == StepStatus.COMPLETED

    def test_from_dict_handles_missing_fields(self):
        """Backward compat: loading old checkpoints with missing fields."""
        minimal = {
            "task_id": "task-123",
            "goal": "Test",
            "plan": {"version": 1, "steps": [], "created_at": _now_iso(), "reason": "initial"},
        }
        state = TaskState.from_dict(minimal)
        assert state.status == TaskStatus.PENDING
        assert state.max_iterations == 100
        assert state.accumulated_context == ""

    def test_json_is_valid(self):
        state = TaskState.create(goal="G", plan_steps=[Step(id="s1", description="A")])
        text = state.to_json()
        parsed = json.loads(text)
        assert parsed["task_id"].startswith("task-")
        assert parsed["plan"]["version"] == 1


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

class TestHelpers:
    def test_generate_task_id_format(self):
        tid = _generate_task_id()
        assert tid.startswith("task-")
        parts = tid.split("-")
        assert len(parts) == 4  # task, YYYYMMDD, HHMMSS, uuid

    def test_now_iso(self):
        ts = _now_iso()
        assert "T" in ts
        assert ts.endswith("+00:00")
