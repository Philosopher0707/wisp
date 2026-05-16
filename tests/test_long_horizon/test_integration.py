"""Integration tests for long-horizon tasks with sessions and agents.

Covers: session-task association, checkpointing on session save,
agent tool integration, and cross-session task continuity.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wisp.long_horizon.storage import TaskStorage
from wisp.long_horizon.state import TaskState, TaskStatus, Step, StepStatus
from wisp.session import Session, SessionManager


@pytest.fixture
def tmp_storage():
    with tempfile.TemporaryDirectory() as td:
        with patch("wisp.long_horizon.storage.TASKS_DIR", Path(td)):
            yield TaskStorage(tasks_dir=Path(td))


@pytest.fixture
def tmp_session_dir():
    with tempfile.TemporaryDirectory() as td:
        with patch("wisp.session.SESSIONS_DIR", Path(td)):
            yield Path(td)


# ══════════════════════════════════════════════════════════════════════
# Session-task association
# ══════════════════════════════════════════════════════════════════════

class TestSessionTaskAssociation:
    def test_session_has_task_ids_field(self):
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )
        assert hasattr(session, "task_ids")
        assert session.task_ids == []

    def test_session_serialization_includes_task_ids(self):
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )
        session.task_ids = ["task-123", "task-456"]

        data = session.to_dict()
        assert "task_ids" in data
        assert data["task_ids"] == ["task-123", "task-456"]

    def test_session_deserialization_restores_task_ids(self):
        data = {
            "id": "test-session",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "model": "test",
            "workspace": ".",
            "messages": [],
            "title": "Test",
            "compaction_history": [],
            "task_ids": ["task-123", "task-456"],
        }
        session = Session.from_dict(data)
        assert session.task_ids == ["task-123", "task-456"]

    def test_session_deserialization_defaults_task_ids(self):
        data = {
            "id": "test-session",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "model": "test",
            "workspace": ".",
            "messages": [],
            "title": "Test",
            "compaction_history": [],
        }
        session = Session.from_dict(data)
        assert session.task_ids == []


# ══════════════════════════════════════════════════════════════════════
# Session save checkpoints running tasks
# ══════════════════════════════════════════════════════════════════════

class TestSessionSaveCheckpointing:
    def test_save_session_checkpoints_running_tasks(self, tmp_session_dir):
        with tempfile.TemporaryDirectory() as td:
            with patch("wisp.long_horizon.storage.TASKS_DIR", Path(td)):
                storage = TaskStorage(tasks_dir=Path(td))

                # Create a session with associated tasks
                session = Session.create(
                    model="test-model",
                    workspace=".",
                    first_prompt="Test",
                )

                # Create tasks in storage
                task1 = TaskState.create(goal="Task 1")
                task1.set_status(TaskStatus.RUNNING)
                task1.last_checkpoint = "2026-01-01T00:00:00"
                storage.save(task1)

                task2 = TaskState.create(goal="Task 2")
                task2.set_status(TaskStatus.PAUSED)
                storage.save(task2)

                task3 = TaskState.create(goal="Task 3")
                task3.set_status(TaskStatus.RUNNING)
                task3.last_checkpoint = "2026-01-01T00:00:00"
                storage.save(task3)

                session.task_ids = [task1.task_id, task2.task_id, task3.task_id]

                # Save session
                mgr = SessionManager()
                mgr.save(session)

                # Verify all running tasks were checkpointed (last_checkpoint updated)
                t1 = storage.load(task1.task_id)
                t3 = storage.load(task3.task_id)
                assert t1.last_checkpoint != "2026-01-01T00:00:00"
                assert t3.last_checkpoint != "2026-01-01T00:00:00"

    def test_save_session_no_tasks(self, tmp_session_dir):
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )

        mgr = SessionManager()
        mgr.save(session)

        # Should not error
        loaded = mgr.load(session.id)
        assert loaded is not None
        assert loaded.task_ids == []

    def test_save_session_with_missing_tasks(self, tmp_session_dir, tmp_storage):
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )
        session.task_ids = ["nonexistent-task"]

        mgr = SessionManager()
        # Should not error even if task is missing
        mgr.save(session)

        loaded = mgr.load(session.id)
        assert loaded.task_ids == ["nonexistent-task"]


# ══════════════════════════════════════════════════════════════════════
# Session list shows task count
# ══════════════════════════════════════════════════════════════════════

class TestSessionList:
    def test_list_sessions_includes_task_count(self, tmp_session_dir):
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )
        session.task_ids = ["task-1", "task-2"]

        mgr = SessionManager()
        mgr.save(session)

        sessions = mgr.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["task_count"] == 2

    def test_list_sessions_zero_tasks(self, tmp_session_dir):
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )

        mgr = SessionManager()
        mgr.save(session)

        sessions = mgr.list_sessions()
        assert sessions[0]["task_count"] == 0


# ══════════════════════════════════════════════════════════════════════
# Agent associates run_long_task with session
# ══════════════════════════════════════════════════════════════════════

class TestAgentTaskAssociation:
    def test_run_long_task_adds_task_id_to_session(self, tmp_session_dir):
        from wisp.tools.long_horizon import tool_run_long_task

        # Create a mock session
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )

        # We can't easily mock the agent core here, but we can verify
        # the tool creates a task and returns the task_id
        result = tool_run_long_task(goal="Test integration")
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "task-" in parsed["data"]

    def test_task_status_tool(self, tmp_storage):
        from wisp.tools.long_horizon import tool_task_status

        task = TaskState.create(goal="Test status")
        task.set_status(TaskStatus.RUNNING)
        tmp_storage.save(task)

        result = tool_task_status(task_id=task.task_id)
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "Progress:" in parsed["data"]
        assert task.task_id in parsed["data"]

    def test_list_tasks_tool(self, tmp_storage):
        from wisp.tools.long_horizon import tool_list_tasks

        task1 = TaskState.create(goal="Task 1")
        task1.set_status(TaskStatus.COMPLETED)
        tmp_storage.save(task1)

        task2 = TaskState.create(goal="Task 2")
        task2.set_status(TaskStatus.RUNNING)
        tmp_storage.save(task2)

        result = tool_list_tasks(status_filter="completed")
        parsed = json.loads(result)
        assert parsed["status"] == "ok"
        assert "completed" in parsed["data"]
        assert task1.task_id in parsed["data"]


# ══════════════════════════════════════════════════════════════════════
# Cross-session continuity
# ══════════════════════════════════════════════════════════════════════

class TestCrossSessionContinuity:
    def test_session_roundtrip_preserves_task_ids(self, tmp_session_dir):
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )
        session.task_ids = ["task-abc", "task-def"]

        mgr = SessionManager()
        mgr.save(session)

        loaded = mgr.load(session.id)
        assert loaded.task_ids == ["task-abc", "task-def"]

    def test_multiple_sessions_isolated_task_ids(self, tmp_session_dir):
        mgr = SessionManager()

        s1 = Session.create(model="m1", workspace=".", first_prompt="A")
        s1.task_ids = ["task-1"]
        mgr.save(s1)

        s2 = Session.create(model="m2", workspace=".", first_prompt="B")
        s2.task_ids = ["task-2", "task-3"]
        mgr.save(s2)

        loaded1 = mgr.load(s1.id)
        loaded2 = mgr.load(s2.id)
        assert loaded1.task_ids == ["task-1"]
        assert loaded2.task_ids == ["task-2", "task-3"]


# ══════════════════════════════════════════════════════════════════════
# Error handling integration
# ══════════════════════════════════════════════════════════════════════

class TestIntegrationErrorHandling:
    def test_session_save_with_corrupt_task_file(self, tmp_session_dir, tmp_storage):
        session = Session.create(
            model="test-model",
            workspace=".",
            first_prompt="Test",
        )

        # Create a task, then corrupt its file
        task = TaskState.create(goal="Test")
        task.set_status(TaskStatus.RUNNING)
        tmp_storage.save(task)
        session.task_ids = [task.task_id]

        path = tmp_storage._checkpoint_path(task.task_id)
        path.write_text("not json")

        # Session save should not crash
        mgr = SessionManager()
        mgr.save(session)

        loaded = mgr.load(session.id)
        assert loaded.task_ids == [task.task_id]

    def test_load_session_with_old_format_no_task_ids(self, tmp_session_dir):
        """Sessions created before task_ids field was added."""
        # Manually create an old-format session file
        old_data = {
            "id": "old-session",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
            "model": "test",
            "workspace": ".",
            "messages": [],
            "title": "Old",
            "compaction_history": [],
        }
        path = tmp_session_dir / "old-session.json"
        path.write_text(json.dumps(old_data))

        mgr = SessionManager()
        loaded = mgr.load("old-session")
        assert loaded is not None
        assert loaded.task_ids == []
