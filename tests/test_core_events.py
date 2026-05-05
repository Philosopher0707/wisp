"""Tests for wisp.core.events — event dataclasses and EventBus."""

import pytest
from wisp.core.events import (
    AgentEvent,
    EventBus,
    TYPE_THINKING,
    TYPE_TOOL_CALL,
    TYPE_TOOL_RESULT,
    TYPE_CONTENT,
    TYPE_ERROR,
    TYPE_DONE,
    TYPE_SYSTEM,
    TYPE_APPROVAL_REQUEST,
    thinking,
    tool_call,
    tool_result,
    content,
    error,
    done,
    system,
    approval_request,
    describe_event_type,
)


class TestAgentEvent:

    def test_basic_creation(self):
        e = AgentEvent(TYPE_CONTENT, {"text": "hello"})
        assert e.type == TYPE_CONTENT
        assert e.data["text"] == "hello"
        assert e.timestamp > 0

    def test_text_property(self):
        e = thinking("reasoning...")
        assert e.text == "reasoning..."

    def test_tool_name_property(self):
        e = tool_call("read_file", {"path": "/tmp/x"})
        assert e.tool_name == "read_file"

    def test_is_final(self):
        assert content("hi").is_final is False
        assert thinking("...").is_final is False
        assert error("oops").is_final is True
        assert done("sid").is_final is True

    def test_immutable(self):
        e = content("hello")
        with pytest.raises(AttributeError):
            e.type = TYPE_ERROR


class TestEventFactories:

    def test_thinking(self):
        e = thinking("step 1")
        assert e.type == TYPE_THINKING
        assert e.data["text"] == "step 1"

    def test_tool_call(self):
        e = tool_call("run_bash", {"command": "ls"})
        assert e.type == TYPE_TOOL_CALL
        assert e.data["name"] == "run_bash"
        assert e.data["arguments"]["command"] == "ls"

    def test_tool_result(self):
        e = tool_result("run_bash", "file.txt", duration_ms=42.0)
        assert e.type == TYPE_TOOL_RESULT
        assert e.data["result"] == "file.txt"
        assert e.data["duration_ms"] == 42.0

    def test_tool_result_without_duration(self):
        e = tool_result("run_bash", "ok")
        assert "duration_ms" not in e.data

    def test_content(self):
        e = content("hello world")
        assert e.type == TYPE_CONTENT
        assert e.data["text"] == "hello world"

    def test_error(self):
        e = error("connection failed", recoverable=False)
        assert e.type == TYPE_ERROR
        assert e.data["message"] == "connection failed"
        assert e.data["recoverable"] is False

    def test_done(self):
        e = done("sid-123", turns=3, summary="fixed auth")
        assert e.type == TYPE_DONE
        assert e.data["session_id"] == "sid-123"
        assert e.data["turns"] == 3
        assert e.data["summary"] == "fixed auth"

    def test_system(self):
        e = system("compacted", level="info")
        assert e.type == TYPE_SYSTEM
        assert e.data["message"] == "compacted"
        assert e.data["level"] == "info"

    def test_approval_request(self):
        e = approval_request("run_bash", {"command": "rm x"}, reason="destructive")
        assert e.type == TYPE_APPROVAL_REQUEST
        assert e.data["name"] == "run_bash"
        assert e.data["reason"] == "destructive"


class TestDescribeEventType:

    def test_known_types(self):
        assert "reasoning" in describe_event_type(TYPE_THINKING).lower()
        assert "complete" in describe_event_type(TYPE_DONE).lower()

    def test_unknown_type(self):
        assert describe_event_type("nope") == "Unknown event"


class TestEventBus:

    def test_subscribe_and_emit(self):
        bus = EventBus()
        received: list[AgentEvent] = []
        bus.subscribe(lambda e: received.append(e))
        bus.emit(content("hello"))
        assert len(received) == 1
        assert received[0].text == "hello"

    def test_multiple_subscribers(self):
        bus = EventBus()
        a, b = [], []
        bus.subscribe(lambda e: a.append(e))
        bus.subscribe(lambda e: b.append(e))
        bus.emit(content("x"))
        assert len(a) == len(b) == 1

    def test_unsubscribe(self):
        bus = EventBus()
        received = []
        handler = lambda e: received.append(e)
        bus.subscribe(handler)
        bus.emit(content("a"))
        bus.unsubscribe(handler)
        bus.emit(content("b"))
        assert len(received) == 1
        assert received[0].text == "a"

    def test_handler_exception_isolated(self):
        bus = EventBus()
        good = []
        bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
        bus.subscribe(lambda e: good.append(e))
        bus.emit(content("safe"))
        assert len(good) == 1  # second handler still ran

    def test_clear(self):
        bus = EventBus()
        received = []
        bus.subscribe(lambda e: received.append(e))
        bus.clear()
        bus.emit(content("x"))
        assert len(received) == 0
