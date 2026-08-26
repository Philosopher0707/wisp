"""TDD for HeadlessTransport.

Tests that HeadlessTransport implements Transport ABC and correctly
collects events into a result dict.
"""

import pytest
from wisp.transport.headless import HeadlessTransport
from wisp.transport.base import Transport


class TestHeadlessTransport:
    """HeadlessTransport collects events without I/O."""

    def test_implements_transport(self):
        assert issubclass(HeadlessTransport, Transport)

    def test_start_clears_events(self):
        transport = HeadlessTransport()
        transport.events = [{"type": "old"}]
        transport.start()
        assert transport.events == []

    def test_stop_sets_flag(self):
        transport = HeadlessTransport()
        transport.start()
        transport.stop()
        assert transport._started is False

    @pytest.mark.asyncio
    async def test_send_stores_event(self):
        transport = HeadlessTransport()
        transport.start()
        await transport.send({"type": "content", "text": "hello"})
        assert len(transport.events) == 1
        assert transport.events[0]["type"] == "content"

    @pytest.mark.asyncio
    async def test_recv_returns_none(self):
        transport = HeadlessTransport()
        result = await transport.recv()
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_returns_true(self):
        transport = HeadlessTransport(auto_approve=True)
        result = await transport.approve({"name": "run_bash"})
        assert result is True

    def test_collect_result_empty(self):
        transport = HeadlessTransport()
        transport.start()
        result = transport.collect_result()
        assert result["ok"] is True
        assert result["content"] == ""
        assert result["tool_calls"] == []

    def test_collect_result_content(self):
        transport = HeadlessTransport()
        transport.start()
        transport.events = [
            {"type": "content", "text": "Hello"},
            {"type": "content", "text": " world"},
            {"type": "done", "turns": 1},
        ]
        result = transport.collect_result()
        assert result["content"] == "Hello world"
        assert result["iterations"] == 1

    def test_collect_result_tool_calls(self):
        transport = HeadlessTransport()
        transport.start()
        transport.events = [
            {"type": "tool_call", "name": "read_file", "arguments": {"path": "/tmp/test"}},
            {"type": "tool_result", "name": "read_file", "result": "content", "duration_ms": 42},
            {"type": "done"},
        ]
        result = transport.collect_result()
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "read_file"
        assert result["tool_calls"][0]["result"] == "content"
        assert result["tool_calls"][0]["duration_ms"] == 42

    def test_collect_result_errors(self):
        transport = HeadlessTransport()
        transport.start()
        transport.events = [
            {"type": "content", "text": "oops"},
            {"type": "error", "message": "failed", "recoverable": False},
        ]
        result = transport.collect_result()
        assert result["ok"] is False
        assert len(result["errors"]) == 1
        assert result["errors"][0]["message"] == "failed"

    def test_collect_result_thinking(self):
        transport = HeadlessTransport()
        transport.start()
        transport.events = [
            {"type": "thinking", "text": "Let me think..."},
            {"type": "content", "text": "Done"},
        ]
        result = transport.collect_result()
        assert result["thinking"] == "Let me think..."


def test_per_character_deltas_concatenate_without_newlines():
    """stealth/ox-alpha streams one char per delta; joining with '\n'
    exploded headless content into one char per line (live E2E)."""
    import asyncio

    transport = HeadlessTransport()

    async def drive():
        for ch in "E2E-OK-42":
            await transport.send({"type": "content", "text": ch})
        await transport.send({"type": "done", "turns": 1})

    asyncio.run(drive())
    result = transport.collect_result()
    assert result["content"] == "E2E-OK-42"
