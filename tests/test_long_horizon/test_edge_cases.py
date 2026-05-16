"""Edge case and boundary tests for long-horizon tasks.

Covers: empty plans, max iterations, unicode goals, very long goals,
concurrent access, storage limits, and boundary conditions.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wisp.long_horizon.storage import TaskStorage
from wisp.long_horizon.state import TaskState, TaskStatus, Step, StepStatus


@pytest.fixture
def tmp_storage():
    with tempfile.TemporaryDirectory() as td:
        yield TaskStorage(tasks_dir=Path(td))


# ══════════════════════════════════════════════════════════════════════
# Empty and minimal plans
# ══════════════════════════════════════════════════════════════════════

class TestEmptyPlans:
    def test_task_with_no_steps(self, tmp_storage):
        task = TaskState.create(goal="No steps needed")
        assert task.total_steps == 0
        # No steps = 0% progress until marked complete
        assert task.progress_pct == 0.0
        task.set_status(TaskStatus.COMPLETED)
        assert task.progress_pct == 100.0
        assert task.is_complete is True

    def test_task_with_empty_plan(self, tmp_storage):
        task = TaskState.create(goal="Test")
        task.plan.steps = []
        assert task.current_step is None
        assert task.total_steps == 0

    def test_advance_past_end_of_plan(self):
        task = TaskState.create(goal="Test")
        task.add_step("Only step")
        task.advance()
        task.advance()  # Past end
        assert task.current_step_index == 2
        assert task.current_step is None


# ══════════════════════════════════════════════════════════════════════
# Unicode and special characters
# ══════════════════════════════════════════════════════════════════════

class TestUnicode:
    def test_unicode_goal(self, tmp_storage):
        goal = "迁移 Flask 到 FastAPI 🚀"
        task = TaskState.create(goal=goal)
        tmp_storage.save(task)

        loaded = tmp_storage.load(task.task_id)
        assert loaded.goal == goal

    def test_unicode_step_description(self, tmp_storage):
        task = TaskState.create(goal="Test")
        task.add_step("步骤一：配置环境 🛠️")
        tmp_storage.save(task)

        loaded = tmp_storage.load(task.task_id)
        assert loaded.plan.steps[0].description == "步骤一：配置环境 🛠️"

    def test_emoji_in_goal(self, tmp_storage):
        task = TaskState.create(goal="Fix bugs 🐛🔧")
        tmp_storage.save(task)

        loaded = tmp_storage.load(task.task_id)
        assert "🐛" in loaded.goal

    def test_newlines_in_goal(self, tmp_storage):
        goal = "Line 1\nLine 2\nLine 3"
        task = TaskState.create(goal=goal)
        tmp_storage.save(task)

        loaded = tmp_storage.load(task.task_id)
        assert loaded.goal == goal

    def test_quotes_in_goal(self, tmp_storage):
        goal = 'Say "hello" and \'goodbye\''
        task = TaskState.create(goal=goal)
        tmp_storage.save(task)

        loaded = tmp_storage.load(task.task_id)
        assert loaded.goal == goal


# ══════════════════════════════════════════════════════════════════════
# Very long content
# ══════════════════════════════════════════════════════════════════════

class TestLongContent:
    def test_very_long_goal(self, tmp_storage):
        goal = "A" * 10000
        task = TaskState.create(goal=goal)
        tmp_storage.save(task)

        loaded = tmp_storage.load(task.task_id)
        assert len(loaded.goal) == 10000

    def test_many_steps(self, tmp_storage):
        task = TaskState.create(goal="Test")
        for i in range(500):
            task.add_step(f"Step {i}")

        tmp_storage.save(task)
        loaded = tmp_storage.load(task.task_id)
        assert len(loaded.plan.steps) == 500

    def test_long_step_description(self, tmp_storage):
        task = TaskState.create(goal="Test")
        desc = "B" * 5000
        task.add_step(desc)
        tmp_storage.save(task)

        loaded = tmp_storage.load(task.task_id)
        assert len(loaded.plan.steps[0].description) == 5000


# ══════════════════════════════════════════════════════════════════════
# Max iterations and limits
# ══════════════════════════════════════════════════════════════════════

class TestLimits:
    def test_max_iterations_zero(self):
        task = TaskState.create(goal="Test", max_iterations=0)
        assert task.max_iterations == 0

    def test_max_iterations_boundary(self):
        task = TaskState.create(goal="Test", max_iterations=1)
        task.add_step("Step 1")
        task.add_step("Step 2")
        assert task.max_iterations == 1

    def test_step_timeout_zero(self):
        task = TaskState.create(goal="Test", step_timeout=0)
        assert task.step_timeout == 0

    def test_max_replans_zero(self):
        task = TaskState.create(goal="Test", max_replans=0)
        assert task.max_replans == 0
        # replan_on_failure is independent of max_replans


# ══════════════════════════════════════════════════════════════════════
# Concurrent access
# ══════════════════════════════════════════════════════════════════════

class TestConcurrentAccess:
    def test_concurrent_saves(self, tmp_storage):
        import threading

        task = TaskState.create(goal="Concurrent test")
        errors = []

        def save_task():
            try:
                tmp_storage.save(task)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=save_task) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        loaded = tmp_storage.load(task.task_id)
        assert loaded is not None

    def test_save_while_loading(self, tmp_storage):
        task = TaskState.create(goal="Test")
        tmp_storage.save(task)

        # Load and save concurrently
        loaded1 = tmp_storage.load(task.task_id)
        loaded1.goal = "Modified"
        tmp_storage.save(loaded1)

        loaded2 = tmp_storage.load(task.task_id)
        assert loaded2.goal == "Modified"


# ══════════════════════════════════════════════════════════════════════
# Progress calculation edge cases
# ══════════════════════════════════════════════════════════════════════

class TestProgress:
    def test_progress_zero_steps(self):
        task = TaskState.create(goal="Test")
        assert task.progress_pct == 0.0
        task.set_status(TaskStatus.COMPLETED)
        assert task.progress_pct == 100.0

    def test_progress_half_complete(self):
        task = TaskState.create(goal="Test")
        task.add_step("S1")
        task.add_step("S2")
        task.mark_step_completed("Done")
        assert task.progress_pct == 50.0

    def test_progress_all_complete(self):
        task = TaskState.create(goal="Test")
        task.add_step("S1")
        task.mark_step_completed("Done")
        assert task.progress_pct == 100.0

    def test_progress_with_failed_steps(self):
        task = TaskState.create(goal="Test")
        task.add_step("S1")
        task.add_step("S2")
        task.mark_step_failed("Error")
        # Failed step doesn't count as completed
        assert task.progress_pct == 0.0
        assert task.failed_count == 1


# ══════════════════════════════════════════════════════════════════════
# Task ID format
# ══════════════════════════════════════════════════════════════════════

class TestTaskId:
    def test_task_id_format(self):
        task = TaskState.create(goal="Test")
        assert task.task_id.startswith("task-")
        parts = task.task_id.split("-")
        assert len(parts) == 4  # task-YYYYMMDD-HHMMSS-XXXXXX

    def test_unique_task_ids(self):
        ids = {TaskState.create(goal="Test").task_id for _ in range(100)}
        assert len(ids) == 100


# ══════════════════════════════════════════════════════════════════════
# Storage boundary conditions
# ══════════════════════════════════════════════════════════════════════

class TestStorageBoundaries:
    def test_list_all_empty_storage(self, tmp_storage):
        assert tmp_storage.list_all() == []

    def test_list_by_status_empty(self, tmp_storage):
        assert tmp_storage.list_by_status("running") == []

    def test_list_by_status_invalid(self, tmp_storage):
        # Should gracefully handle invalid status
        result = tmp_storage.list_by_status("not_a_status")
        assert result == []

    def test_many_tasks_listing(self, tmp_storage):
        for i in range(100):
            task = TaskState.create(goal=f"Task {i}")
            tmp_storage.save(task)

        tasks = tmp_storage.list_all()
        assert len(tasks) == 100

    def test_index_with_many_tasks(self, tmp_storage):
        for i in range(50):
            task = TaskState.create(goal=f"Task {i}")
            task.set_status(TaskStatus.RUNNING if i % 2 == 0 else TaskStatus.COMPLETED)
            tmp_storage.save(task)

        running = tmp_storage.list_by_status("running")
        completed = tmp_storage.list_by_status("completed")
        assert len(running) == 25
        assert len(completed) == 25
