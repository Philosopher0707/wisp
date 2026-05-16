"""Tests for wisp.long_horizon.manager — TaskManager lifecycle and control.

Covers: start, pause, resume, cancel, status queries, error paths,
background task management, and concurrent operations.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from wisp.long_horizon.manager import TaskManager
from wisp.long_horizon.state import TaskState, TaskStatus, Step, StepStatus
from wisp.long_horizon.storage import TaskStorage


@pytest.fixture
def tmp_storage():
    with tempfile.TemporaryDirectory() as td:
        yield TaskStorage(tasks_dir=Path(td))


@pytest.fixture
def mock_agent():
    """Return a mock WispAgentCore with controllable step execution.

    Steps block on step_event until the test calls step_event.set().
    This eliminates race conditions in async tests.
    """
    agent = MagicMock()
    step_event = asyncio.Event()
    call_count = 0

    async def run_task_side_effect(task_description, **kwargs):
        nonlocal call_count
        call_count += 1
        # Plan generation: don't block
        if "plan" in task_description.lower() or call_count == 1:
            return {
                "success": True,
                "output": "1. First step\n2. Second step\n3. Third step"
            }
        # Step execution: block until test signals
        try:
            await asyncio.wait_for(step_event.wait(), timeout=2.0)
            step_event.clear()
        except asyncio.TimeoutError:
            raise asyncio.CancelledError()
        return {"success": True, "output": f"Result {call_count}"}

    agent.run_task = run_task_side_effect
    agent.step_event = step_event
    return agent


def _release_steps(agent, count: int = 3):
    """Release N blocked steps by setting the event."""
    for _ in range(count):
        agent.step_event.set()


async def _release_steps_async(agent, count: int = 3):
    """Async helper to release steps with small yields."""
    for _ in range(count):
        agent.step_event.set()
        await asyncio.sleep(0.01)


# ══════════════════════════════════════════════════════════════════════
# Lifecycle: start
# ══════════════════════════════════════════════════════════════════════

class TestStart:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal", workspace=".")

        assert task_id.startswith("task-")
        state = tmp_storage.load(task_id)
        assert state is not None
        assert state.goal == "Test goal"
        # Steps are blocked, so status should be RUNNING
        assert state.status == TaskStatus.RUNNING

        await _release_steps_async(mock_agent)

    @pytest.mark.asyncio
    async def test_start_generates_plan(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")

        state = tmp_storage.load(task_id)
        assert len(state.plan.steps) == 3
        assert state.plan.steps[0].description == "First step"

        await _release_steps_async(mock_agent)

    @pytest.mark.asyncio
    async def test_start_tracks_in_running(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")

        # Steps blocked, so running
        assert manager.is_running(task_id) is True

        await _release_steps_async(mock_agent)
        # Give background task time to finish
        await asyncio.sleep(0.05)
        assert manager.is_running(task_id) is False

    @pytest.mark.asyncio
    async def test_start_with_empty_goal(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("")

        state = tmp_storage.load(task_id)
        assert state is not None
        assert state.goal == ""

        await _release_steps_async(mock_agent)

    @pytest.mark.asyncio
    async def test_start_persists_immediately(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")

        tasks = tmp_storage.list_all()
        assert any(t["task_id"] == task_id for t in tasks)

        await _release_steps_async(mock_agent)


# ══════════════════════════════════════════════════════════════════════
# Lifecycle: pause
# ══════════════════════════════════════════════════════════════════════

class TestPause:
    @pytest.mark.asyncio
    async def test_pause_running_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")

        assert manager.is_running(task_id) is True
        result = await manager.pause(task_id)
        assert result is True

        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.PAUSED
        assert manager.is_running(task_id) is False

    @pytest.mark.asyncio
    async def test_pause_missing_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        result = await manager.pause("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_pause_already_paused(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")
        await manager.pause(task_id)

        result = await manager.pause(task_id)
        assert result is True
        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.PAUSED

    @pytest.mark.asyncio
    async def test_pause_cancels_background_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")

        bg_task = manager._running.get(task_id)
        assert bg_task is not None
        assert manager.is_running(task_id) is True

        await manager.pause(task_id)
        assert manager.is_running(task_id) is False


# ══════════════════════════════════════════════════════════════════════
# Lifecycle: resume
# ══════════════════════════════════════════════════════════════════════

class TestResume:
    @pytest.mark.asyncio
    async def test_resume_paused_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")
        await manager.pause(task_id)

        result = await manager.resume(task_id)
        assert result == task_id
        assert manager.is_running(task_id) is True

        await _release_steps_async(mock_agent)

    @pytest.mark.asyncio
    async def test_resume_missing_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        with pytest.raises(ValueError, match="Task not found"):
            await manager.resume("nonexistent")

    @pytest.mark.asyncio
    async def test_resume_sets_status_running(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")
        await manager.pause(task_id)

        await manager.resume(task_id)
        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.RUNNING

        await _release_steps_async(mock_agent)


# ══════════════════════════════════════════════════════════════════════
# Lifecycle: cancel
# ══════════════════════════════════════════════════════════════════════

class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_running_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")

        assert manager.is_running(task_id) is True
        result = await manager.cancel(task_id)
        assert result is True

        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.FAILED
        assert manager.is_running(task_id) is False

    @pytest.mark.asyncio
    async def test_cancel_missing_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        result = await manager.cancel("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_paused_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")
        await manager.pause(task_id)

        result = await manager.cancel(task_id)
        assert result is True
        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_cancel_completed_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")
        await _release_steps_async(mock_agent)

        result = await manager.cancel(task_id)
        assert result is True
        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.FAILED


# ══════════════════════════════════════════════════════════════════════
# Status queries
# ══════════════════════════════════════════════════════════════════════

class TestStatus:
    @pytest.mark.asyncio
    async def test_status_existing_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")

        status = manager.status(task_id)
        assert status["task_id"] == task_id
        assert status["goal"] == "Test goal"
        assert status["status"] == "running"
        assert status["is_running"] is True
        assert "progress_pct" in status

        await _release_steps_async(mock_agent)

    def test_status_missing_task(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        status = manager.status("nonexistent")
        assert "error" in status

    @pytest.mark.asyncio
    async def test_status_reflects_progress(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")
        await _release_steps_async(mock_agent)

        status = manager.status(task_id)
        assert status["completed_steps"] == 3
        assert status["total_steps"] == 3
        assert status["progress_pct"] == 100.0

    @pytest.mark.asyncio
    async def test_list_tasks_all(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        await manager.start("Task A")
        await manager.start("Task B")
        await _release_steps_async(mock_agent, count=6)

        tasks = manager.list_tasks("all")
        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_list_tasks_by_status(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Task A")
        await _release_steps_async(mock_agent)

        completed = manager.list_tasks("completed")
        assert len(completed) == 1
        assert completed[0]["task_id"] == task_id

        running = manager.list_tasks("running")
        assert len(running) == 0

    def test_list_tasks_empty(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        tasks = manager.list_tasks("all")
        assert tasks == []

    @pytest.mark.asyncio
    async def test_is_running_reflects_state(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test goal")

        assert manager.is_running(task_id) is True
        await manager.pause(task_id)
        assert manager.is_running(task_id) is False


# ══════════════════════════════════════════════════════════════════════
# Concurrent operations
# ══════════════════════════════════════════════════════════════════════

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_multiple_tasks_run_concurrently(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)

        task_ids = []
        for i in range(3):
            tid = await manager.start(f"Task {i}")
            task_ids.append(tid)

        # All running (steps blocked)
        for tid in task_ids:
            assert manager.is_running(tid) is True

        # Release all steps
        await _release_steps_async(mock_agent, count=9)

        for tid in task_ids:
            assert manager.is_running(tid) is False
            state = tmp_storage.load(tid)
            assert state.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_pause_one_does_not_affect_others(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)

        tid1 = await manager.start("Task 1")
        tid2 = await manager.start("Task 2")

        assert manager.is_running(tid1) is True
        assert manager.is_running(tid2) is True

        await manager.pause(tid1)

        state1 = tmp_storage.load(tid1)
        state2 = tmp_storage.load(tid2)
        assert state1.status == TaskStatus.PAUSED
        assert manager.is_running(tid2) is True

        await _release_steps_async(mock_agent, count=6)

    @pytest.mark.asyncio
    async def test_concurrent_start_and_pause(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)

        tid = await manager.start("Test")
        await manager.pause(tid)
        await manager.resume(tid)
        await manager.pause(tid)

        state = tmp_storage.load(tid)
        assert state.status == TaskStatus.PAUSED

        await _release_steps_async(mock_agent)


# ══════════════════════════════════════════════════════════════════════
# Error handling
# ══════════════════════════════════════════════════════════════════════

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_start_with_failing_agent(self, tmp_storage):
        agent = MagicMock()
        agent.run_task = AsyncMock(side_effect=RuntimeError("Model error"))

        manager = TaskManager(agent=agent, storage=tmp_storage)
        with pytest.raises(RuntimeError):
            await manager.start("Test goal")

    @pytest.mark.asyncio
    async def test_resume_with_corrupt_state(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        with pytest.raises(ValueError, match="Task not found"):
            await manager.resume("corrupt-task-id")

    @pytest.mark.asyncio
    async def test_status_after_storage_error(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test")

        # Corrupt the checkpoint file
        path = tmp_storage._checkpoint_path(task_id)
        path.write_text("not json")

        status = manager.status(task_id)
        assert "error" in status

        # Clean up: release blocked steps
        await _release_steps_async(mock_agent)

    @pytest.mark.asyncio
    async def test_background_task_exception(self, mock_agent, tmp_storage):
        agent = MagicMock()
        call_count = 0

        async def failing_run_task(task_description, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True, "output": "1. Step one"}
            raise RuntimeError("Step execution failed")

        agent.run_task = failing_run_task

        manager = TaskManager(agent=agent, storage=tmp_storage)
        task_id = await manager.start("Test")
        await asyncio.sleep(0.05)

        assert manager.is_running(task_id) is False
        state = tmp_storage.load(task_id)
        assert state is not None

    @pytest.mark.asyncio
    async def test_cancel_during_execution(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage)
        task_id = await manager.start("Test")

        assert manager.is_running(task_id) is True
        await manager.cancel(task_id)

        state = tmp_storage.load(task_id)
        assert state.status == TaskStatus.FAILED
        assert manager.is_running(task_id) is False


# ══════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════

class TestConfiguration:
    def test_custom_max_parallel(self, mock_agent, tmp_storage):
        manager = TaskManager(agent=mock_agent, storage=tmp_storage, max_parallel=8)
        assert manager.max_parallel == 8

    def test_custom_storage(self, mock_agent, tmp_storage):
        custom_storage = TaskStorage(tasks_dir=tmp_storage.tasks_dir)
        manager = TaskManager(agent=mock_agent, storage=custom_storage)
        assert manager.storage is custom_storage

    def test_default_storage(self, mock_agent):
        manager = TaskManager(agent=mock_agent)
        assert manager.storage is not None
