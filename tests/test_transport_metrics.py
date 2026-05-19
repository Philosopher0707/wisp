"""TDD for MetricsTransport.

Tests that MetricsTransport collects performance metrics from events.
"""

import pytest
from wisp.transport.metrics import MetricsTransport
from wisp.transport.base import Transport


class TestMetricsTransport:
    """MetricsTransport collects counters from events."""

    def test_implements_transport(self):
        assert issubclass(MetricsTransport, Transport)

    def test_start_resets_counters(self):
        transport = MetricsTransport()
        transport.turns = 5
        transport.start()
        assert transport.turns == 0
        assert transport.tool_calls == 0

    def test_stop_sets_flag(self):
        transport = MetricsTransport()
        transport.start()
        transport.stop()
        assert transport._started is False

    @pytest.mark.asyncio
    async def test_send_counts_content_chars(self):
        transport = MetricsTransport()
        transport.start()
        await transport.send({"type": "content", "text": "Hello world"})
        assert transport.content_chars == 11

    @pytest.mark.asyncio
    async def test_send_counts_thinking_chars(self):
        transport = MetricsTransport()
        transport.start()
        await transport.send({"type": "thinking", "text": "Let me think"})
        assert transport.thinking_chars == 12

    @pytest.mark.asyncio
    async def test_send_counts_tool_calls(self):
        transport = MetricsTransport()
        transport.start()
        await transport.send({"type": "tool_call", "name": "read_file"})
        await transport.send({"type": "tool_call", "name": "write_file"})
        assert transport.tool_calls == 2

    @pytest.mark.asyncio
    async def test_send_counts_errors(self):
        transport = MetricsTransport()
        transport.start()
        await transport.send({"type": "error", "message": "failed"})
        assert transport.errors == 1

    @pytest.mark.asyncio
    async def test_send_counts_done_as_turn(self):
        transport = MetricsTransport()
        transport.start()
        await transport.send({"type": "done", "turns": 1})
        assert transport.turns == 1

    @pytest.mark.asyncio
    async def test_recv_returns_none(self):
        transport = MetricsTransport()
        result = await transport.recv()
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_returns_true(self):
        transport = MetricsTransport()
        result = await transport.approve({"name": "run_bash"})
        assert result is True

    def test_snapshot_empty(self):
        transport = MetricsTransport()
        transport.start()
        snapshot = transport.snapshot()
        assert snapshot["turns"] == 0
        assert snapshot["tool_success_rate"] == 100.0
        assert snapshot["avg_latency_ms"] == 0.0

    @pytest.mark.asyncio
    async def test_snapshot_with_data(self):
        transport = MetricsTransport()
        transport.start()
        await transport.send({"type": "content", "text": "Hello"})
        await transport.send({"type": "tool_call", "name": "read_file"})
        await transport.send({"type": "tool_result", "name": "read_file", "result": "ok"})
        await transport.send({"type": "done", "turns": 1})
        snapshot = transport.snapshot()
        assert snapshot["turns"] == 1
        assert snapshot["tool_calls"] == 1
        assert snapshot["tool_errors"] == 0
        assert snapshot["tool_success_rate"] == 100.0
        assert snapshot["content_chars"] == 5

    def test_reset_clears_all(self):
        transport = MetricsTransport()
        transport.start()
        transport.turns = 5
        transport.tool_calls = 3
        transport.reset()
        assert transport.turns == 0
        assert transport.tool_calls == 0
