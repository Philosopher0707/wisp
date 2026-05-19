"""Test HeadlessTransport with AgentEvent objects."""

import pytest
import asyncio
from wisp.transport.headless import HeadlessTransport
from wisp.core.events import AgentEvent, TYPE_CONTENT, TYPE_TOOL_CALL, TYPE_TOOL_RESULT, TYPE_ERROR, TYPE_DONE


class TestHeadlessAgentEvent:
    """HeadlessTransport accepts AgentEvent objects."""

    @pytest.mark.asyncio
    async def test_send_agent_event_content(self):
        transport = HeadlessTransport()
        transport.start()
        event = AgentEvent(TYPE_CONTENT, {"text": "Hello"})
        await transport.send(event)
        result = transport.collect_result()
        assert result["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_send_agent_event_tool_call(self):
        transport = HeadlessTransport()
        transport.start()
        await transport.send(AgentEvent(TYPE_TOOL_CALL, {"name": "read_file", "arguments": {"path": "/tmp/test"}}))
        await transport.send(AgentEvent(TYPE_TOOL_RESULT, {"name": "read_file", "result": "content", "duration_ms": 42}))
        result = transport.collect_result()
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "read_file"
        assert result["tool_calls"][0]["result"] == "content"

    @pytest.mark.asyncio
    async def test_send_agent_event_error(self):
        transport = HeadlessTransport()
        transport.start()
        await transport.send(AgentEvent(TYPE_ERROR, {"message": "failed", "recoverable": False}))
        result = transport.collect_result()
        assert result["ok"] is False
        assert result["errors"][0]["message"] == "failed"

    @pytest.mark.asyncio
    async def test_send_agent_event_done(self):
        transport = HeadlessTransport()
        transport.start()
        await transport.send(AgentEvent(TYPE_CONTENT, {"text": "Done"}))
        await transport.send(AgentEvent(TYPE_DONE, {"turns": 3}))
        result = transport.collect_result()
        assert result["iterations"] == 3
