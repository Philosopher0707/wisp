"""TDD for unified message format.

Replaces: ad-hoc event dictionaries scattered across transports.
One schema for all events, validated and typed.
"""

import pytest
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# 1. Event creation
# ═══════════════════════════════════════════════════════════════════

class TestEventCreation:
    """Events are created with required fields."""

    def test_content_event(self):
        from wisp.transport.protocol import Event
        event = Event.content(text="hello")
        assert event.type == "content"
        assert event.text == "hello"

    def test_tool_call_event(self):
        from wisp.transport.protocol import Event
        event = Event.tool_call(name="read_file", arguments={"path": "test.py"})
        assert event.type == "tool_call"
        assert event.name == "read_file"
        assert event.arguments == {"path": "test.py"}

    def test_tool_result_event(self):
        from wisp.transport.protocol import Event
        event = Event.tool_result(name="read_file", result="content")
        assert event.type == "tool_result"
        assert event.result == "content"

    def test_error_event(self):
        from wisp.transport.protocol import Event
        event = Event.error(message="boom", recoverable=True)
        assert event.type == "error"
        assert event.message == "boom"
        assert event.recoverable is True

    def test_done_event(self):
        from wisp.transport.protocol import Event
        event = Event.done()
        assert event.type == "done"

    def test_ready_event(self):
        from wisp.transport.protocol import Event
        event = Event.ready(session_id="sess-1")
        assert event.type == "ready"
        assert event.session_id == "sess-1"


# ═══════════════════════════════════════════════════════════════════
# 2. Serialization
# ═══════════════════════════════════════════════════════════════════

class TestSerialization:
    """Events serialize to JSON for wire transport."""

    def test_content_to_json(self):
        from wisp.transport.protocol import Event
        event = Event.content(text="hello")
        data = event.to_dict()
        assert data == {"type": "content", "text": "hello"}

    def test_tool_call_to_json(self):
        from wisp.transport.protocol import Event
        event = Event.tool_call(name="read_file", arguments={"path": "test.py"})
        data = event.to_dict()
        assert data == {"type": "tool_call", "name": "read_file", "arguments": {"path": "test.py"}}

    def test_json_roundtrip(self):
        from wisp.transport.protocol import Event
        original = Event.error(message="boom", recoverable=False)
        data = original.to_dict()
        restored = Event.from_dict(data)
        assert restored.type == "error"
        assert restored.message == "boom"
        assert restored.recoverable is False


# ═══════════════════════════════════════════════════════════════════
# 3. Validation
# ═══════════════════════════════════════════════════════════════════

class TestValidation:
    """Invalid events are rejected."""

    def test_missing_type_raises(self):
        from wisp.transport.protocol import Event
        with pytest.raises(ValueError):
            Event.from_dict({"text": "hello"})

    def test_unknown_type_raises(self):
        from wisp.transport.protocol import Event
        with pytest.raises(ValueError):
            Event.from_dict({"type": "unknown"})

    def test_content_missing_text_raises(self):
        from wisp.transport.protocol import Event
        with pytest.raises(ValueError):
            Event.from_dict({"type": "content"})

    def test_tool_call_missing_name_raises(self):
        from wisp.transport.protocol import Event
        with pytest.raises(ValueError):
            Event.from_dict({"type": "tool_call", "arguments": {}})


# ═══════════════════════════════════════════════════════════════════
# 4. Transport agnostic
# ═══════════════════════════════════════════════════════════════════

class TestTransportAgnostic:
    """Same events work across all transports."""

    def test_ws_format(self):
        from wisp.transport.protocol import Event
        event = Event.content(text="hello")
        assert event.to_ws() == {"type": "content", "text": "hello"}

    def test_sse_format(self):
        from wisp.transport.protocol import Event
        event = Event.content(text="hello")
        assert event.to_sse() == 'data: {"type": "content", "text": "hello"}\n\n'

    def test_cli_format(self):
        from wisp.transport.protocol import Event
        event = Event.content(text="hello")
        assert event.to_cli() == "hello"

    def test_error_cli_format(self):
        from wisp.transport.protocol import Event
        event = Event.error(message="boom")
        assert event.to_cli() == "Error: boom"
