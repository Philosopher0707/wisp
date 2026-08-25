"""Integration test: HeadlessTransport + AgentRuntime.

Verifies that HeadlessTransport can collect events from AgentRuntime.run_turn().
"""

import pytest
from unittest.mock import MagicMock

from wisp.transport.headless import HeadlessTransport
from wisp.core.runtime import AgentRuntime


class TestHeadlessRuntimeIntegration:
    """HeadlessTransport collects events from AgentRuntime."""

    @pytest.mark.asyncio
    async def test_runtime_yields_events_to_headless(self):
        transport = HeadlessTransport()
        transport.start()

        # Mock core that yields events
        mock_core = MagicMock()
        async def _mock_turn(session, prompt, approval_handler=None, steering_drain=None):
            yield {"type": "content", "text": "Hello"}
            yield {"type": "done", "turns": 1}

        mock_core.turn = _mock_turn

        runtime = AgentRuntime(
            store=MagicMock(),
            security=MagicMock(),
            extensions=MagicMock(),
            telemetry=MagicMock(),
            core_factory=lambda: mock_core,
        )

        session = {"id": "test", "messages": [], "model": "test", "workspace": "/tmp"}

        async for event in runtime.run_turn(session, "hi"):
            await transport.send(event)

        result = transport.collect_result()
        assert result["content"] == "Hello"
        assert result["iterations"] == 1
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_runtime_tool_calls_collected(self):
        transport = HeadlessTransport()
        transport.start()

        mock_core = MagicMock()
        async def _mock_turn(session, prompt, approval_handler=None, steering_drain=None):
            yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "/tmp/test"}}
            yield {"type": "tool_result", "name": "read_file", "result": "content", "duration_ms": 42}
            yield {"type": "done"}

        mock_core.turn = _mock_turn

        runtime = AgentRuntime(
            store=MagicMock(),
            security=MagicMock(),
            extensions=MagicMock(),
            telemetry=MagicMock(),
            core_factory=lambda: mock_core,
        )

        session = {"id": "test", "messages": [], "model": "test", "workspace": "/tmp"}

        async for event in runtime.run_turn(session, "read file"):
            await transport.send(event)

        result = transport.collect_result()
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["name"] == "read_file"
        assert result["tool_calls"][0]["result"] == "content"
