"""Tests for wisp.long_horizon.storage — checkpoint I/O, atomic writes, index."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from wisp.long_horizon.state import TaskState, Step, TaskStatus, StepStatus
from wisp.long_horizon.storage import TaskStorage, _atomic_write


@pytest.fixture
def tmp_storage():
    """Provide a TaskStorage backed by a temp directory."""
    with tempfile.TemporaryDirectory() as td:
        yield TaskStorage(tasks_dir=Path(td))


# ══════════════════════════════════════════════════════════════════════
# CRUD
# ══════════════════════════════════════════════════════════════════════

class TestCrud:
    def test_save_and_load(self, tmp_storage: TaskStorage):
        state = TaskState.create(
            goal="Test save/load",
            plan_steps=[Step(id="s1", description="A")],
        )
        path = tmp_storage.save(state)
        assert path.exists()

        loaded = tmp_storage.load(state.task_id)
        assert loaded is not None
        assert loaded.task_id == state.task_id
        assert loaded.goal == "Test save/load"
        assert loaded.total_steps == 1

    def test_load_missing_returns_none(self, tmp_storage: TaskStorage):
        assert tmp_storage.load("nonexistent") is None

    def test_exists(self, tmp_storage: TaskStorage):
        state = TaskState.create(goal="G")
        assert not tmp_storage.exists(state.task_id)
        tmp_storage.save(state)
        assert tmp_storage.exists(state.task_id)

    def test_delete(self, tmp_storage: TaskStorage):
        state = TaskState.create(goal="G")
        tmp_storage.save(state)
        assert tmp_storage.delete(state.task_id) is True
        assert not tmp_storage.exists(state.task_id)
        assert tmp_storage.delete(state.task_id) is False

    def test_save_updates_last_checkpoint(self, tmp_storage: TaskStorage):
        state = TaskState.create(goal="G")
        assert state.last_checkpoint == ""
        tmp_storage.save(state)
        assert state.last_checkpoint != ""
        assert "T" in state.last_checkpoint


# ══════════════════════════════════════════════════════════════════════
# Atomic writes
# ══════════════════════════════════════════════════════════════════════

class TestAtomicWrite:
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.txt"
            _atomic_write(path, "hello")
            assert path.read_text() == "hello"

    def test_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.txt"
            path.write_text("old")
            _atomic_write(path, "new")
            assert path.read_text() == "new"

    def test_no_temp_left_behind_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.txt"
            _atomic_write(path, "data")
            temps = list(Path(td).glob("*.tmp"))
            assert len(temps) == 0

    def test_creates_parent_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a" / "b" / "c.txt"
            _atomic_write(path, "nested")
            assert path.read_text() == "nested"


# ══════════════════════════════════════════════════════════════════════
# Index registry
# ══════════════════════════════════════════════════════════════════════

class TestIndex:
    def test_index_created_on_save(self, tmp_storage: TaskStorage):
        state = TaskState.create(goal="Indexed task")
        tmp_storage.save(state)
        index = tmp_storage.list_all()
        assert len(index) == 1
        assert index[0]["task_id"] == state.task_id
        assert index[0]["goal"] == "Indexed task"
        assert index[0]["status"] == "pending"

    def test_index_updated_on_resave(self, tmp_storage: TaskStorage):
        state = TaskState.create(goal="First")
        tmp_storage.save(state)
        state.set_status(TaskStatus.RUNNING)
        tmp_storage.save(state)

        index = tmp_storage.list_all()
        assert len(index) == 1
        assert index[0]["status"] == "running"

    def test_index_removed_on_delete(self, tmp_storage: TaskStorage):
        state = TaskState.create(goal="To delete")
        tmp_storage.save(state)
        tmp_storage.delete(state.task_id)
        assert tmp_storage.list_all() == []

    def test_list_by_status(self, tmp_storage: TaskStorage):
        s1 = TaskState.create(goal="Running")
        s1.set_status(TaskStatus.RUNNING)
        s2 = TaskState.create(goal="Completed")
        s2.set_status(TaskStatus.COMPLETED)
        tmp_storage.save(s1)
        tmp_storage.save(s2)

        running = tmp_storage.list_by_status("running")
        assert len(running) == 1
        assert running[0]["goal"] == "Running"

    def test_list_running(self, tmp_storage: TaskStorage):
        s = TaskState.create(goal="R")
        s.set_status(TaskStatus.RUNNING)
        tmp_storage.save(s)
        assert len(tmp_storage.list_running()) == 1

    def test_rebuild_index_from_checkpoints(self, tmp_storage: TaskStorage):
        # Save directly, then corrupt index
        state = TaskState.create(goal="Rebuild me")
        tmp_storage.save(state)
        index_path = tmp_storage.tasks_dir / "index.json"
        index_path.write_text("corrupt json {{{")

        # list_all should rebuild
        tasks = tmp_storage.list_all()
        assert len(tasks) == 1
        assert tasks[0]["goal"] == "Rebuild me"


# ══════════════════════════════════════════════════════════════════════
# Corruption handling
# ══════════════════════════════════════════════════════════════════════

class TestCorruption:
    def test_load_corrupt_checkpoint_returns_none(self, tmp_storage: TaskStorage):
        state = TaskState.create(goal="G")
        tmp_storage.save(state)
        path = tmp_storage._checkpoint_path(state.task_id)
        path.write_text("not json")
        assert tmp_storage.load(state.task_id) is None

    def test_rebuild_skips_corrupt_checkpoints(self, tmp_storage: TaskStorage):
        good = TaskState.create(goal="Good")
        tmp_storage.save(good)

        # Create a corrupt checkpoint file manually
        bad_path = tmp_storage.tasks_dir / "task-bad-123.json"
        bad_path.write_text("corrupt")

        tasks = tmp_storage.list_all()
        assert len(tasks) == 1
        assert tasks[0]["goal"] == "Good"


# ══════════════════════════════════════════════════════════════════════
# Full workflow
# ══════════════════════════════════════════════════════════════════════

class TestWorkflow:
    def test_task_lifecycle(self, tmp_storage: TaskStorage):
        # Create
        state = TaskState.create(
            goal="Migrate auth",
            plan_steps=[
                Step(id="s1", description="Audit"),
                Step(id="s2", description="Migrate"),
            ],
        )
        tmp_storage.save(state)

        # Execute step 1
        loaded = tmp_storage.load(state.task_id)
        assert loaded is not None
        loaded.set_status(TaskStatus.RUNNING)
        loaded.mark_step_completed("Found 3 auth methods")
        tmp_storage.save(loaded)

        # Verify
        final = tmp_storage.load(state.task_id)
        assert final is not None
        assert final.status == TaskStatus.RUNNING
        assert final.current_step_index == 1
        assert final.completed_count == 1
        assert final.plan.steps[0].status == StepStatus.COMPLETED

        # Index reflects progress
        index = tmp_storage.list_all()
        assert index[0]["current_step"] == 1
        assert index[0]["total_steps"] == 2
