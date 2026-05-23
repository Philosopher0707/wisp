"""Tests for wisp.transport.server — ServerTransport event serialization."""

import pytest
import asyncio
from unittest.mock import AsyncMock

from wisp.transport.server import ServerTransport, PendingApproval
from wisp.core.engine import WispAgentCore
from wisp.core.events import (
    content,
    thinking,
    tool_call,
    tool_result,
    error,
    done,
    system,
    approval_request,
)
from wisp.config import WispConfig


@pytest.fixture
def core():
    config = WispConfig()
    config.model = "test-model"
    config.workspace = "/tmp"
    config.auto_compact = False
    return WispAgentCore(config=config)


@pytest.fixture
def transport(core):
    send_mock = AsyncMock()
    return ServerTransport(core, send_mock)


class TestServerTransportSerialization:

    def test_content_event(self, transport):
        event = content("hello")
        msg = transport._event_to_json(event)
        assert msg == {"type": "token", "text": "hello", "phase": "content"}

    def test_thinking_event(self, transport):
        event = thinking("deep thought")
        msg = transport._event_to_json(event)
        assert msg == {"type": "token", "text": "deep thought", "phase": "thinking"}

    def test_tool_call_event(self, transport):
        event = tool_call("read_file", {"path": "/tmp/test.py"})
        msg = transport._event_to_json(event)
        assert msg["type"] == "tool_call"
        assert msg["name"] == "read_file"
        assert msg["arguments"]["path"] == "/tmp/test.py"

    def test_tool_result_event(self, transport):
        event = tool_result("read_file", "file contents", duration_ms=42.5)
        msg = transport._event_to_json(event)
        assert msg["type"] == "tool_result"
        assert msg["name"] == "read_file"
        assert msg["result"] == "file contents"
        assert msg["duration_ms"] == 42.5

    def test_error_event(self, transport):
        event = error("something broke", recoverable=False)
        msg = transport._event_to_json(event)
        assert msg["type"] == "error"
        assert msg["message"] == "something broke"
        assert msg["recoverable"] is False

    def test_system_event(self, transport):
        event = system("compacted session", "info")
        msg = transport._event_to_json(event)
        assert msg["type"] == "status"
        assert msg["message"] == "compacted session"
        assert msg["level"] == "info"

    def test_approval_request_event(self, transport):
        """Approval requests return None from _event_to_json (handled inline)."""
        event = approval_request("run_bash", {"command": "rm -rf /"}, reason="dangerous")
        msg = transport._event_to_json(event)
        # Approval events are intentionally omitted from the event stream;
        # they are handled by the approval_handler callback.
        assert msg is None

    def test_done_event(self, transport):
        event = done("sid-123", 3)
        msg = transport._event_to_json(event)
        assert msg == {"type": "done", "session_id": "sid-123", "turns": 3, "reason": "natural"}

    def test_unknown_event_returns_none(self, transport):
        from wisp.core.events import AgentEvent
        event = AgentEvent("unknown_type", {})
        assert transport._event_to_json(event) is None


class TestServerTransportApproval:

    @pytest.mark.asyncio
    async def test_approve_tool(self, transport):
        pa = PendingApproval("tc-1", "run_bash", {"command": "ls"})
        transport._pending_approvals["tc-1"] = pa

        result = await transport.approve_tool("tc-1", True)
        assert result is True
        assert pa.approved is True
        assert pa.event.is_set()

    @pytest.mark.asyncio
    async def test_deny_tool(self, transport):
        pa = PendingApproval("tc-1", "run_bash", {"command": "ls"})
        transport._pending_approvals["tc-1"] = pa

        result = await transport.approve_tool("tc-1", False, reason="too risky")
        assert result is True
        assert pa.approved is False
        assert pa.denied_reason == "too risky"

    @pytest.mark.asyncio
    async def test_approve_unknown_call_id(self, transport):
        result = await transport.approve_tool("nonexistent", True)
        assert result is False


class TestServerTransportRun:

    @pytest.mark.asyncio
    async def test_interrupt_stops_run(self, transport):
        transport.interrupt()
        assert transport._interrupted is True
