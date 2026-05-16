"""Tests for wisp.long_horizon.runner — sequential execution, replanning, events."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from wisp.core.events import (
    TYPE_TASK_STARTED,
    TYPE_TASK_STEP_STARTED,
    TYPE_TASK_STEP_COMPLETED,
    TYPE_TASK_STEP_FAILED,
    TYPE_TASK_REPLANNING,
    TYPE_TASK_COMPLETED,
    TYPE_TASK_FAILED,
    TYPE_TASK_PROGRESS,
    TYPE_TASK_RESUMED,
)
from wisp.long_horizon.runner import LongHorizonRunner
from wisp.long_horizon.state import TaskState, Step, Plan, TaskStatus, StepStatus
from wisp.long_horizon.storage import TaskStorage
from wisp.long_horizon.errors import ReplanError


@pytest.fixture
def tmp_storage():
    with tempfile.TemporaryDirectory() as td:
        yield TaskStorage(tasks_dir=Path(td))


@pytest.fixture
def mock_agent():
    """Return a mock WispAgentCore that generates plans and executes steps."""
    agent = MagicMock()
    call_count = 0

    async def run_task_side_effect(task_description, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call is plan generation
        if call_count == 1:
            return {
                "success": True,
                "output": "1. First step\n2. Second step\n3. Third step"
            }
        # Subsequent calls are step execution
        return {"success": True, "output": f"Step result {call_count}"}

    agent.run_task = run_task_side_effect
    return agent


async def _collect_events(runner, **kwargs) -> list:
    """Helper: collect all events from runner.run()."""
    events = []
    async for event in runner.run(**kwargs):
        events.append(event)
    return events


# ══════════════════════════════════════════════════════════════════════
# Basic execution
# ══════════════════════════════════════════════════════════════════════

class TestBasicExecution:
    @pytest.mark.asyncio
    async def test_runs_all_steps(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, goal="Test task")

        # Should have: started, step_started, step_completed (x3), progress, completed
        types = [e.type for e in events]
        assert TYPE_TASK_STARTED in types
        assert TYPE_TASK_COMPLETED in types
        assert types.count(TYPE_TASK_STEP_COMPLETED) == 3  # 3 mock steps

    @pytest.mark.asyncio
    async def test_step_results_persist(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, goal="Test")

        # Find the task_id from the started event
        started = next(e for e in events if e.type == TYPE_TASK_STARTED)
        task_id = started.data["task_id"]

        # Load from storage and verify
        state = tmp_storage.load(task_id)
        assert state is not None
        assert state.status == TaskStatus.COMPLETED
        assert state.completed_count == 3

    @pytest.mark.asyncio
    async def test_progress_callback(self, mock_agent, tmp_storage):
        progress_states = []
        runner = LongHorizonRunner(
            agent=mock_agent,
            storage=tmp_storage,
            progress_callback=lambda s: progress_states.append(s.current_step_index),
        )
        await _collect_events(runner, goal="Test")
        assert len(progress_states) == 3
        assert progress_states == [0, 1, 2]


# ══════════════════════════════════════════════════════════════════════
# Resume
# ══════════════════════════════════════════════════════════════════════

class TestResume:
    @pytest.mark.asyncio
    async def test_resume_from_checkpoint(self, mock_agent, tmp_storage):
        # Create a task with 1 step completed, 2 remaining
        state = TaskState.create(
            goal="Test resume",
            plan_steps=[
                Step(id="s1", description="First", status=StepStatus.COMPLETED),
                Step(id="s2", description="Second"),
                Step(id="s3", description="Third"),
            ],
        )
        state.mark_step_completed("Done with first")
        state.set_status(TaskStatus.RUNNING)
        tmp_storage.save(state)
        task_id = state.task_id

        # Resume
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, resume_from=task_id)

        resumed = next(e for e in events if e.type == TYPE_TASK_RESUMED)
        assert resumed.data["step_index"] == 1  # Resumed at step 1

        # Should complete remaining 2 steps
        assert sum(1 for e in events if e.type == TYPE_TASK_STEP_COMPLETED) == 2

    @pytest.mark.asyncio
    async def test_resume_missing_task(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, resume_from="nonexistent")
        assert any(e.type == TYPE_TASK_FAILED for e in events)

    @pytest.mark.asyncio
    async def test_resume_completed_task(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, goal="Test")
        task_id = next(e for e in events if e.type == TYPE_TASK_STARTED).data["task_id"]

        # Resume completed task
        events2 = await _collect_events(runner, resume_from=task_id)
        # Should not re-execute, just emit progress
        assert not any(e.type == TYPE_TASK_STEP_STARTED for e in events2)


# ══════════════════════════════════════════════════════════════════════
# Failure handling
# ══════════════════════════════════════════════════════════════════════

class TestFailureHandling:
    @pytest.mark.asyncio
    async def test_retry_then_succeed(self, mock_agent, tmp_storage):
        """Step fails twice, succeeds on third attempt."""
        call_count = 0
        async def side_effect(task_description, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True, "output": "1. Step one\n2. Step two\n3. Step three"}
            if call_count <= 3:  # First 2 step executions fail
                return {"success": False, "output": "Error"}
            return {"success": True, "output": "Fixed"}

        mock_agent.run_task = side_effect
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, goal="Test")

        # Should have failed events for retries
        failed_events = [e for e in events if e.type == TYPE_TASK_STEP_FAILED]
        assert len(failed_events) == 2

        # Should eventually complete
        assert any(e.type == TYPE_TASK_COMPLETED for e in events)

    @pytest.mark.asyncio
    async def test_replan_after_max_retries(self, mock_agent, tmp_storage):
        """Step fails max_attempts times, triggers replanning."""
        call_count = 0
        async def side_effect(task_description, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True, "output": "1. Step one\n2. Step two"}
            if call_count == 2:
                # First replan call
                return {"success": True, "output": "1. New step one\n2. New step two"}
            return {"success": False, "output": "Persistent error"}

        mock_agent.run_task = side_effect
        runner = LongHorizonRunner(
            agent=mock_agent,
            storage=tmp_storage,
            max_replans=1,
        )
        events = await _collect_events(runner, goal="Test")

        # Should see replanning event
        assert any(e.type == TYPE_TASK_REPLANNING for e in events)

    @pytest.mark.asyncio
    async def test_escalation_pattern(self, mock_agent, tmp_storage):
        call_count = 0
        async def side_effect(task_description, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True, "output": "1. Step one\n2. Step two"}
            return {"success": False, "output": "git push failed: permission denied"}

        mock_agent.run_task = side_effect
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, goal="Test")

        assert any(e.type == TYPE_TASK_FAILED for e in events)
        state = tmp_storage.list_all()[0]
        assert state["status"] == "failed"

    @pytest.mark.asyncio
    async def test_max_iterations(self, mock_agent, tmp_storage):
        call_count = 0
        async def side_effect(task_description, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True, "output": "1. A\n2. B\n3. C\n4. D\n5. E"}
            return {"success": True, "output": "done"}

        mock_agent.run_task = side_effect
        runner = LongHorizonRunner(
            agent=mock_agent,
            storage=tmp_storage,
            max_iterations=2,
        )
        events = await _collect_events(runner, goal="Test")

        assert any(e.type == TYPE_TASK_FAILED for e in events)
        failed = next(e for e in events if e.type == TYPE_TASK_FAILED)
        assert "Max iterations" in failed.data["reason"]


# ══════════════════════════════════════════════════════════════════════
# Pause
# ══════════════════════════════════════════════════════════════════════

class TestPause:
    @pytest.mark.asyncio
    async def test_pause_updates_status(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, goal="Test")
        task_id = next(e for e in events if e.type == TYPE_TASK_STARTED).data["task_id"]

        # Pause
        assert await runner.pause(task_id) is True
        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.PAUSED

    @pytest.mark.asyncio
    async def test_pause_missing(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        assert await runner.pause("nonexistent") is False


# ══════════════════════════════════════════════════════════════════════
# Plan parsing
# ══════════════════════════════════════════════════════════════════════

class TestPlanParsing:
    def test_numbered_list(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        text = "1. First step\n2. Second step\n3. Third step"
        steps = runner._parse_plan(text)
        assert len(steps) == 3
        assert steps[0].description == "First step"
        assert steps[1].description == "Second step"

    def test_bullet_list(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        text = "- Step A\n* Step B\n- Step C"
        steps = runner._parse_plan(text)
        assert len(steps) == 3
        assert steps[0].description == "Step A"

    def test_mixed_formats(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        text = "1. First\n- Second\n2) Third\n* Fourth"
        steps = runner._parse_plan(text)
        assert len(steps) == 4

    def test_ignores_empty_lines(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        text = "1. First\n\n\n2. Second"
        steps = runner._parse_plan(text)
        assert len(steps) == 2

    def test_ignores_non_matching_lines(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        text = "Some intro\n1. First\nMore text\n2. Second"
        steps = runner._parse_plan(text)
        assert len(steps) == 2


# ══════════════════════════════════════════════════════════════════════
# Context building
# ══════════════════════════════════════════════════════════════════════

class TestContextBuilding:
    def test_basic_context(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        state = TaskState.create(
            goal="Migrate auth",
            plan_steps=[
                Step(id="s1", description="Audit"),
                Step(id="s2", description="Migrate"),
            ],
        )
        state.mark_step_completed("Found 3 methods")
        ctx = runner._build_step_context(state.plan.steps[1], state)
        assert "Migrate auth" in ctx
        assert "Current step (2/2): Migrate" in ctx
        assert "Found 3 methods" in ctx

    def test_with_dependencies(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        state = TaskState.create(
            goal="Build API",
            plan_steps=[
                Step(id="s1", description="Models"),
                Step(id="s2", description="Routes", dependencies=["s1"]),
            ],
        )
        state.mark_step_completed("User model done")
        ctx = runner._build_step_context(state.plan.steps[1], state)
        assert "depends on: s1" in ctx
        assert "User model done" in ctx


# ══════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_goal_no_resume(self, mock_agent, tmp_storage):
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, goal="")
        assert any(e.type == TYPE_TASK_FAILED for e in events)

    @pytest.mark.asyncio
    async def test_pause_during_run(self, mock_agent, tmp_storage):
        """Test that pause correctly updates task status."""
        runner = LongHorizonRunner(agent=mock_agent, storage=tmp_storage)
        events = await _collect_events(runner, goal="Test")
        task_id = next(e for e in events if e.type == TYPE_TASK_STARTED).data["task_id"]

        # Pause the completed task
        assert await runner.pause(task_id) is True
        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.PAUSED
