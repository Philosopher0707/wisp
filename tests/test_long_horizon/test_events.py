"""Tests for task lifecycle events.

Covers: event creation, type constants, data payloads, and event descriptions.
"""

from __future__ import annotations

import pytest

from wisp.core.events import (
    TYPE_TASK_STARTED,
    TYPE_TASK_STEP_STARTED,
    TYPE_TASK_STEP_COMPLETED,
    TYPE_TASK_STEP_FAILED,
    TYPE_TASK_REPLANNING,
    TYPE_TASK_PAUSED,
    TYPE_TASK_RESUMED,
    TYPE_TASK_COMPLETED,
    TYPE_TASK_FAILED,
    TYPE_TASK_PROGRESS,
    TYPE_TASK_ESCALATION,
    task_started,
    task_step_started,
    task_step_completed,
    task_step_failed,
    task_replanning,
    task_paused,
    task_resumed,
    task_completed,
    task_failed,
    task_progress,
    task_escalation,
)


# ══════════════════════════════════════════════════════════════════════
# Event type constants
# ══════════════════════════════════════════════════════════════════════

class TestEventTypes:
    def test_all_types_defined(self):
        assert TYPE_TASK_STARTED == "task_started"
        assert TYPE_TASK_STEP_STARTED == "task_step_started"
        assert TYPE_TASK_STEP_COMPLETED == "task_step_completed"
        assert TYPE_TASK_STEP_FAILED == "task_step_failed"
        assert TYPE_TASK_REPLANNING == "task_replanning"
        assert TYPE_TASK_PAUSED == "task_paused"
        assert TYPE_TASK_RESUMED == "task_resumed"
        assert TYPE_TASK_COMPLETED == "task_completed"
        assert TYPE_TASK_FAILED == "task_failed"
        assert TYPE_TASK_PROGRESS == "task_progress"
        assert TYPE_TASK_ESCALATION == "task_escalation"

    def test_event_descriptions_exist(self):
        from wisp.core.events import _EVENT_DESCRIPTIONS
        assert TYPE_TASK_STARTED in _EVENT_DESCRIPTIONS
        assert TYPE_TASK_COMPLETED in _EVENT_DESCRIPTIONS


# ══════════════════════════════════════════════════════════════════════
# Event factory functions
# ══════════════════════════════════════════════════════════════════════

class TestEventFactories:
    def test_task_started_event(self):
        event = task_started("task-123", "Test goal", 5)
        assert event.type == TYPE_TASK_STARTED
        assert event.data["task_id"] == "task-123"
        assert event.data["goal"] == "Test goal"
        assert event.data["total_steps"] == 5

    def test_task_step_started_event(self):
        event = task_step_started("task-123", "step-1", 0, "Do something")
        assert event.type == TYPE_TASK_STEP_STARTED
        assert event.data["task_id"] == "task-123"
        assert event.data["step_id"] == "step-1"
        assert event.data["step_index"] == 0
        assert event.data["description"] == "Do something"

    def test_task_step_completed_event(self):
        event = task_step_completed("task-123", "step-1", 0, "Result", 1500)
        assert event.type == TYPE_TASK_STEP_COMPLETED
        assert event.data["result"] == "Result"
        assert event.data["duration_ms"] == 1500

    def test_task_step_failed_event(self):
        event = task_step_failed("task-123", "step-1", 0, "Error msg", 2)
        assert event.type == TYPE_TASK_STEP_FAILED
        assert event.data["error"] == "Error msg"
        assert event.data["attempt"] == 2

    def test_task_replanning_event(self):
        event = task_replanning("task-123", "Step failed", 1, 2)
        assert event.type == TYPE_TASK_REPLANNING
        assert event.data["old_version"] == 1
        assert event.data["new_version"] == 2

    def test_task_paused_event(self):
        event = task_paused("task-123", "User requested")
        assert event.type == TYPE_TASK_PAUSED
        assert event.data["reason"] == "User requested"

    def test_task_resumed_event(self):
        event = task_resumed("task-123", 3)
        assert event.type == TYPE_TASK_RESUMED
        assert event.data["step_index"] == 3

    def test_task_completed_event(self):
        event = task_completed("task-123", "Test goal", 5, 5)
        assert event.type == TYPE_TASK_COMPLETED
        assert event.data["completed_steps"] == 5
        assert event.data["total_steps"] == 5

    def test_task_failed_event(self):
        event = task_failed("task-123", "Test goal", "Something broke")
        assert event.type == TYPE_TASK_FAILED
        assert event.data["reason"] == "Something broke"

    def test_task_progress_event(self):
        event = task_progress("task-123", 2, 5, "running")
        assert event.type == TYPE_TASK_PROGRESS
        assert event.data["step_index"] == 2
        assert event.data["total_steps"] == 5
        assert event.data["status"] == "running"

    def test_task_escalation_event(self):
        event = task_escalation("task-123", "step-1", "Needs human", ["continue", "abort"])
        assert event.type == TYPE_TASK_ESCALATION
        assert event.data["options"] == ["continue", "abort"]


# ══════════════════════════════════════════════════════════════════════
# Event data validation
# ══════════════════════════════════════════════════════════════════════

class TestEventData:
    def test_events_have_required_fields(self):
        event = task_started("task-123", "Goal", 3)
        assert "task_id" in event.data
        assert event.timestamp is not None

    def test_event_timestamp_is_iso(self):
        event = task_started("task-123", "Goal", 3)
        ts = event.timestamp
        assert isinstance(ts, (str, float))

    def test_event_str_representation(self):
        event = task_started("task-123", "Goal", 3)
        s = str(event)
        assert "task_started" in s

    def test_event_repr(self):
        event = task_started("task-123", "Goal", 3)
        r = repr(event)
        assert "task_started" in r
        assert "task-123" in r
