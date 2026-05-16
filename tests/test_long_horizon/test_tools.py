"""Tests for long-horizon tool integration."""

from __future__ import annotations

import json
import uuid

import pytest

from wisp.tools import execute_tool
from wisp.long_horizon.storage import TaskStorage


class TestRunLongTask:
    def test_creates_task(self):
        result = execute_tool(
            "run_long_task",
            {"goal": f"Migrate Flask to FastAPI {uuid.uuid4().hex[:8]}"},
            ".",
        )
        outer = json.loads(result)
        assert outer["status"] == "ok"
        inner = json.loads(outer["data"])
        assert inner["status"] == "ok"
        assert "task_id" in inner["metadata"]

    def test_task_persisted(self):
        goal = f"Test persistence {uuid.uuid4().hex[:8]}"
        result = execute_tool("run_long_task", {"goal": goal}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        task_id = inner["metadata"]["task_id"]

        # Verify in default storage
        storage = TaskStorage()
        state = storage.load(task_id)
        assert state is not None
        assert state.goal == goal


class TestTaskStatus:
    def test_existing_task(self):
        result = execute_tool("run_long_task", {"goal": f"Status test {uuid.uuid4().hex[:8]}"}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        task_id = inner["metadata"]["task_id"]

        result = execute_tool("task_status", {"task_id": task_id}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert inner["status"] == "ok"
        assert "Progress:" in inner["data"]

    def test_missing_task(self):
        result = execute_tool("task_status", {"task_id": "nonexistent-task-12345"}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert inner["status"] == "error"


class TestListTasks:
    def test_with_tasks(self):
        # Create a couple tasks
        execute_tool("run_long_task", {"goal": f"Task A {uuid.uuid4().hex[:8]}"}, ".")
        execute_tool("run_long_task", {"goal": f"Task B {uuid.uuid4().hex[:8]}"}, ".")

        result = execute_tool("list_tasks", {"status_filter": "all"}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert inner["status"] == "ok"
        assert inner["metadata"]["count"] >= 2


class TestPauseResumeCancel:
    def test_pause(self):
        result = execute_tool("run_long_task", {"goal": f"Pause test {uuid.uuid4().hex[:8]}"}, ".")
        task_id = json.loads(json.loads(result)["data"])["metadata"]["task_id"]

        result = execute_tool("pause_task", {"task_id": task_id}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert inner["status"] == "ok"

    def test_resume(self):
        result = execute_tool("run_long_task", {"goal": f"Resume test {uuid.uuid4().hex[:8]}"}, ".")
        task_id = json.loads(json.loads(result)["data"])["metadata"]["task_id"]

        result = execute_tool("resume_task", {"task_id": task_id}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert inner["status"] == "ok"

    def test_cancel(self):
        result = execute_tool("run_long_task", {"goal": f"Cancel test {uuid.uuid4().hex[:8]}"}, ".")
        task_id = json.loads(json.loads(result)["data"])["metadata"]["task_id"]

        result = execute_tool("cancel_task", {"task_id": task_id}, ".")
        outer = json.loads(result)
        inner = json.loads(outer["data"])
        assert inner["status"] == "ok"

        # Verify status changed
        storage = TaskStorage()
        state = storage.load(task_id)
        assert state.status.value == "failed"
