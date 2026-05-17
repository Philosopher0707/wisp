"""Tests for EventType StrEnum — type-safe event type constants.

Verifies that EventType enum values are backward-compatible with
existing TYPE_* string constants.
"""

import pytest
from enum import StrEnum


class TestEventTypeEnum:

    def test_event_type_is_strenum(self):
        from wisp.core.events import EventType
        assert issubclass(EventType, StrEnum)

    def test_all_type_constants_have_enum_equivalent(self):
        from wisp.core.events import (
            EventType,
            TYPE_THINKING,
            TYPE_TOOL_CALL,
            TYPE_TOOL_RESULT,
            TYPE_CONTENT,
            TYPE_ERROR,
            TYPE_DONE,
            TYPE_SYSTEM,
            TYPE_APPROVAL_REQUEST,
            TYPE_STEERING_PAUSED,
            TYPE_STEERING_INJECT,
            TYPE_STEERING_RESUMED,
        )
        assert EventType.THINKING == TYPE_THINKING
        assert EventType.TOOL_CALL == TYPE_TOOL_CALL
        assert EventType.TOOL_RESULT == TYPE_TOOL_RESULT
        assert EventType.CONTENT == TYPE_CONTENT
        assert EventType.ERROR == TYPE_ERROR
        assert EventType.DONE == TYPE_DONE
        assert EventType.SYSTEM == TYPE_SYSTEM
        assert EventType.APPROVAL_REQUEST == TYPE_APPROVAL_REQUEST
        assert EventType.STEERING_PAUSED == TYPE_STEERING_PAUSED
        assert EventType.STEERING_INJECT == TYPE_STEERING_INJECT
        assert EventType.STEERING_RESUMED == TYPE_STEERING_RESUMED

    def test_enum_values_are_strings(self):
        from wisp.core.events import EventType
        assert str(EventType.CONTENT) == "content"
        assert EventType.CONTENT == "content"
        assert "content" == EventType.CONTENT

    def test_agent_event_accepts_enum(self):
        from wisp.core.events import AgentEvent, EventType
        event = AgentEvent(type=EventType.CONTENT, data={"text": "hello"})
        assert event.type == "content"
        assert event.type == EventType.CONTENT

    def test_agent_event_is_final_with_enum(self):
        from wisp.core.events import AgentEvent, EventType
        done_event = AgentEvent(type=EventType.DONE, data={})
        error_event = AgentEvent(type=EventType.ERROR, data={})
        content_event = AgentEvent(type=EventType.CONTENT, data={})
        assert done_event.is_final is True
        assert error_event.is_final is True
        assert content_event.is_final is False

    def test_event_type_membership(self):
        from wisp.core.events import EventType
        # Check by value
        assert EventType("content") == EventType.CONTENT
        # Check that invalid values raise ValueError
        with pytest.raises(ValueError):
            EventType("nonexistent")

    def test_describe_event_type_with_enum(self):
        from wisp.core.events import EventType, describe_event_type
        assert describe_event_type(EventType.TOOL_CALL) == "Tool invocation"
        assert describe_event_type(EventType.CONTENT) == "Assistant text response"

    def test_event_type_roundtrip_json(self):
        from wisp.core.events import AgentEvent, EventType
        event = AgentEvent(type=EventType.TOOL_RESULT, data={"name": "read_file"})
        d = event.to_dict()
        assert d["type"] == "tool_result"
        restored = AgentEvent.from_dict(d)
        assert restored.type == EventType.TOOL_RESULT

    def test_event_type_set_operations(self):
        from wisp.core.events import EventType
        write_tools = {EventType.TOOL_CALL, EventType.TOOL_RESULT}
        assert EventType.TOOL_CALL in write_tools
        assert EventType.CONTENT not in write_tools
