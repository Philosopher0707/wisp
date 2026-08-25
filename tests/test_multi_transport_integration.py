"""Integration test: MultiTransport + AgentRuntime.

Verifies that MultiTransport can broadcast events to multiple
outputs (CLI + Headless + File) from AgentRuntime.run_turn().
"""

import json
import os
import pytest
import tempfile
from unittest.mock import MagicMock, AsyncMock

from wisp.transport.multi import MultiTransport
from wisp.transport.headless import HeadlessTransport
from wisp.transport.file import FileTransport
from wisp.core.runtime import AgentRuntime


class TestMultiTransportIntegration:
    """MultiTransport broadcasts AgentRuntime events to multiple outputs."""

    @pytest.mark.asyncio
    async def test_multi_collects_and_logs(self):
        headless = HeadlessTransport()
        headless.start()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl") as f:
            log_path = f.name

        try:
            file_transport = FileTransport(log_path, mode="w")
            file_transport.start()

            multi = MultiTransport([headless, file_transport])

            mock_core = MagicMock()
            async def _mock_turn(session, prompt, approval_handler=None, steering_drain=None):
                yield {"type": "content", "text": "Hello"}
                yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "/tmp/test"}}
                yield {"type": "tool_result", "name": "read_file", "result": "content", "duration_ms": 42}
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
                await multi.send(event)

            # Verify headless collected events
            result = headless.collect_result()
            assert result["content"] == "Hello"
            assert len(result["tool_calls"]) == 1
            assert result["iterations"] == 1

            # Verify file logged events
            file_transport.stop()
            with open(log_path) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            event_lines = [l for l in lines if l["type"] not in ("file_transport_start", "file_transport_stop")]
            assert len(event_lines) == 4
            assert event_lines[0]["type"] == "content"
            assert event_lines[0]["text"] == "Hello"

        finally:
            os.unlink(log_path)

    @pytest.mark.asyncio
    async def test_multi_approve_unanimous(self):
        t1 = MagicMock()
        t1.approve = AsyncMock(return_value=False)
        t2 = MagicMock()
        t2.approve = AsyncMock(return_value=True)

        multi = MultiTransport([t1, t2])
        result = await multi.approve({"name": "run_bash"})
        assert result is False

    @pytest.mark.asyncio
    async def test_multi_recv_first(self):
        t1 = MagicMock()
        t1.recv = AsyncMock(return_value=None)
        t2 = MagicMock()
        t2.recv = AsyncMock(return_value="hello")

        multi = MultiTransport([t1, t2])
        result = await multi.recv()
        assert result == "hello"
