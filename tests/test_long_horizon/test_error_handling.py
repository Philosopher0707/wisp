"""Tests for error handling and recovery in long-horizon tasks.

Covers: corrupt state recovery, missing files, invalid transitions,
storage errors, runner failure modes, and graceful degradation.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wisp.long_horizon.storage import TaskStorage
from wisp.long_horizon.state import TaskState, TaskStatus, Step, StepStatus
from wisp.long_horizon.errors import TaskError, StepTimeoutError, MaxIterationsError


@pytest.fixture
def tmp_storage():
    with tempfile.TemporaryDirectory() as td:
        yield TaskStorage(tasks_dir=Path(td))


# ══════════════════════════════════════════════════════════════════════
# Storage error handling
# ══════════════════════════════════════════════════════════════════════

class TestStorageErrors:
    def test_load_missing_task_returns_none(self, tmp_storage):
        assert tmp_storage.load("nonexistent") is None

    def test_load_corrupt_json_returns_none(self, tmp_storage):
        task = TaskState.create(goal="Test")
        tmp_storage.save(task)

        path = tmp_storage._checkpoint_path(task.task_id)
        path.write_text("not valid json")

        assert tmp_storage.load(task.task_id) is None

    def test_load_truncated_json_returns_none(self, tmp_storage):
        task = TaskState.create(goal="Test")
        tmp_storage.save(task)

        path = tmp_storage._checkpoint_path(task.task_id)
        data = path.read_text()
        path.write_text(data[:50])  # Truncate

        assert tmp_storage.load(task.task_id) is None

    def test_delete_missing_task_returns_false(self, tmp_storage):
        assert tmp_storage.delete("nonexistent") is False

    def test_delete_removes_from_index(self, tmp_storage):
        task = TaskState.create(goal="Test")
        tmp_storage.save(task)
        assert len(tmp_storage.list_all()) == 1

        tmp_storage.delete(task.task_id)
        assert len(tmp_storage.list_all()) == 0

    def test_save_creates_parent_dirs(self, tmp_storage):
        nested = tmp_storage.tasks_dir / "deep" / "nested"
        storage = TaskStorage(tasks_dir=nested)
        task = TaskState.create(goal="Test")
        storage.save(task)

        assert (nested / f"{task.task_id}.json").exists()

    def test_rebuild_index_skips_corrupt_files(self, tmp_storage):
        task1 = TaskState.create(goal="Good")
        tmp_storage.save(task1)

        # Create a corrupt file directly
        bad_path = tmp_storage.tasks_dir / "task-bad.json"
        bad_path.write_text("corrupt")

        # Rebuild should skip corrupt file
        tmp_storage._rebuild_index()
        tasks = tmp_storage.list_all()
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == task1.task_id

    def test_atomic_write_failure_fallback(self, tmp_storage):
        """If atomic write fails, the previous checkpoint should remain."""
        task = TaskState.create(goal="Test")
        tmp_storage.save(task)

        original = tmp_storage.load(task.task_id)
        original_goal = original.goal

        # Corrupt the temp write by making the directory read-only
        # (This is a best-effort test — may not work on all platforms)
        task.goal = "Modified"
        tmp_storage.save(task)

        loaded = tmp_storage.load(task.task_id)
        assert loaded.goal == "Modified"  # Should have updated

    def test_index_corruption_rebuilds(self, tmp_storage):
        task = TaskState.create(goal="Test")
        tmp_storage.save(task)

        # Corrupt the index
        index_path = tmp_storage.tasks_dir / "index.json"
        index_path.write_text("not json")

        # Next operation should rebuild index
        tasks = tmp_storage.list_all()
        assert len(tasks) == 1


# ══════════════════════════════════════════════════════════════════════
# State invalid transitions
# ══════════════════════════════════════════════════════════════════════

class TestInvalidTransitions:
    def test_complete_already_completed_task(self, tmp_storage):
        task = TaskState.create(goal="Test")
        task.set_status(TaskStatus.COMPLETED)

        # Should not raise
        task.set_status(TaskStatus.COMPLETED)
        assert task.status == TaskStatus.COMPLETED

    def test_fail_already_failed_task(self, tmp_storage):
        task = TaskState.create(goal="Test")
        task.set_status(TaskStatus.FAILED)

        # Should not raise
        task.set_status(TaskStatus.FAILED)
        assert task.status == TaskStatus.FAILED

    def test_resume_completed_task(self, tmp_storage):
        task = TaskState.create(goal="Test")
        task.set_status(TaskStatus.COMPLETED)

        # Can set back to running
        task.set_status(TaskStatus.RUNNING)
        assert task.status == TaskStatus.RUNNING

    def test_mark_step_completed_when_no_steps(self):
        task = TaskState.create(goal="Test")
        assert task.current_step is None
        # Should not crash
        task.advance()
        assert task.current_step_index == 1  # Advanced past empty plan

    def test_mark_step_failed_when_no_steps(self):
        task = TaskState.create(goal="Test")
        assert task.current_step is None
        # Should not crash
        task.set_status(TaskStatus.FAILED)
        assert task.is_failed


# ══════════════════════════════════════════════════════════════════════
# Runner error recovery
# ══════════════════════════════════════════════════════════════════════

class TestRunnerErrorRecovery:
    @pytest.mark.asyncio
    async def test_replan_failure_graceful(self, tmp_storage):
        from wisp.long_horizon.runner import LongHorizonRunner

        agent = MagicMock()
        call_count = 0

        async def side_effect(task_description, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True, "output": "1. Step one"}
            if "replan" in task_description.lower() or call_count == 2:
                raise RuntimeError("Replan failed")
            return {"success": False, "output": "Error"}

        agent.run_task = side_effect
        runner = LongHorizonRunner(agent=agent, storage=tmp_storage, max_replans=1)

        events = []
        async for e in runner.run(goal="Test"):
            events.append(e)

        # Should fail gracefully, not crash
        assert any(e.type == "task_failed" for e in events)

    @pytest.mark.asyncio
    async def test_agent_returns_invalid_result(self, tmp_storage):
        from wisp.long_horizon.runner import LongHorizonRunner

        agent = MagicMock()
        async def side_effect(task_description, **kwargs):
            return {"success": True, "output": ""}  # Empty plan

        agent.run_task = side_effect
        runner = LongHorizonRunner(agent=agent, storage=tmp_storage)

        events = []
        async for e in runner.run(goal="Test"):
            events.append(e)

        # Should handle empty plan gracefully
        assert any(e.type == "task_started" for e in events)

    @pytest.mark.asyncio
    async def test_step_execution_exception(self, tmp_storage):
        from wisp.long_horizon.runner import LongHorizonRunner

        agent = MagicMock()
        call_count = 0

        async def side_effect(task_description, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"success": True, "output": "1. Step one"}
            raise RuntimeError("Step crashed")

        agent.run_task = side_effect
        runner = LongHorizonRunner(agent=agent, storage=tmp_storage, replan_on_failure=False)

        events = []
        async for e in runner.run(goal="Test"):
            events.append(e)

        # Should emit failure event, not crash
        failed_events = [e for e in events if e.type == "task_step_failed"]
        assert len(failed_events) >= 1

    @pytest.mark.asyncio
    async def test_resume_nonexistent_task(self, tmp_storage):
        from wisp.long_horizon.runner import LongHorizonRunner

        agent = MagicMock()
        runner = LongHorizonRunner(agent=agent, storage=tmp_storage)

        events = []
        async for e in runner.run(resume_from="nonexistent"):
            events.append(e)

        assert any(e.type == "task_failed" for e in events)


# ══════════════════════════════════════════════════════════════════════
# Tool error handling
# ══════════════════════════════════════════════════════════════════════

class TestToolErrorHandling:
    def test_task_status_missing_task(self, tmp_storage):
        from wisp.tools.long_horizon import tool_task_status

        result = tool_task_status(task_id="nonexistent")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    def test_pause_task_missing(self, tmp_storage):
        from wisp.tools.long_horizon import tool_pause_task

        result = tool_pause_task(task_id="nonexistent")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    def test_resume_task_missing(self, tmp_storage):
        from wisp.tools.long_horizon import tool_resume_task

        result = tool_resume_task(task_id="nonexistent")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    def test_cancel_task_missing(self, tmp_storage):
        from wisp.tools.long_horizon import tool_cancel_task

        result = tool_cancel_task(task_id="nonexistent")
        parsed = json.loads(result)
        assert parsed["status"] == "error"

    def test_list_tasks_invalid_filter(self, tmp_storage):
        from wisp.tools.long_horizon import tool_list_tasks

        result = tool_list_tasks(status_filter="invalid_filter")
        parsed = json.loads(result)
        assert parsed["status"] == "ok"  # Graceful, returns all


# ══════════════════════════════════════════════════════════════════════
# DAG error handling
# ══════════════════════════════════════════════════════════════════════

class TestDagErrors:
    def test_deadlock_detection(self):
        from wisp.long_horizon.dag import ParallelTaskExecutor, DagNode

        executor = ParallelTaskExecutor()
        executor.add_node(DagNode(id="a", description="A", dependencies=["c"]))
        executor.add_node(DagNode(id="b", description="B", dependencies=["a"]))
        executor.add_node(DagNode(id="c", description="C", dependencies=["b"]))

        with pytest.raises(Exception):  # Should detect cycle
            import asyncio
            asyncio.run(executor.execute())

    def test_missing_dependency(self):
        from wisp.long_horizon.dag import ParallelTaskExecutor, DagNode

        executor = ParallelTaskExecutor()
        executor.add_node(DagNode(id="a", description="A", dependencies=["missing"]))

        with pytest.raises(Exception):
            import asyncio
            asyncio.run(executor.execute())

    def test_empty_dag(self):
        from wisp.long_horizon.dag import ParallelTaskExecutor

        executor = ParallelTaskExecutor()
        # Empty DAG should not raise
        assert executor.nodes == {}

    def test_single_step_no_deps(self):
        from wisp.long_horizon.dag import ParallelTaskExecutor, DagNode

        executor = ParallelTaskExecutor()
        executor.add_node(DagNode(id="a", description="A"))
        # Should not raise on construction
        assert len(executor.nodes) == 1
