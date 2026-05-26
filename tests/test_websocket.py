"""TDD for WebSocketTransport bidirectional approval flow."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from wisp.transport.websocket import WebSocketTransport


@pytest.fixture
def mock_runtime():
    return MagicMock()


@pytest.fixture
def transport(mock_runtime):
    return WebSocketTransport(mock_runtime)


class TestWebSocketTransportApproval:
    """Issue 8: WebSocketTransport must implement bidirectional approval."""

    @pytest.mark.asyncio
    async def test_approve_sends_request_and_waits_for_response(self, transport):
        """approve() sends an approval_request event and blocks until resolved."""
        mock_ws = AsyncMock()
        transport._current_ws = mock_ws

        # approve() should send approval_request and then wait
        approval_task = asyncio.create_task(
            transport.approve({"name": "write_file", "arguments": {"path": "x"}})
        )

        # Give approve() time to send the request
        await asyncio.sleep(0.01)
        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.await_args[0][0]
        assert sent["type"] == "approval_request"
        assert sent["tool_call"]["name"] == "write_file"

        # Resolve the approval
        transport.resolve_approval(True)

        result = await asyncio.wait_for(approval_task, timeout=1.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_approve_times_out_when_no_response(self, transport):
        """approve() returns False if no response within timeout."""
        mock_ws = AsyncMock()
        transport._current_ws = mock_ws

        # Patch timeout to 0.01s for fast test
        import wisp.transport.websocket as mod
        old_timeout = getattr(mod, "_APPROVAL_TIMEOUT", None)
        try:
            mod._APPROVAL_TIMEOUT = 0.01
            result = await transport.approve({"name": "bash", "arguments": {}})
        finally:
            if old_timeout is not None:
                mod._APPROVAL_TIMEOUT = old_timeout
            else:
                delattr(mod, "_APPROVAL_TIMEOUT")

        assert result is False

    @pytest.mark.asyncio
    async def test_approve_auto_approves_when_no_ws(self, transport):
        """approve() returns True (fallback) when no active WebSocket."""
        transport._current_ws = None
        result = await transport.approve({"name": "write_file", "arguments": {}})
        assert result is True

    @pytest.mark.asyncio
    async def test_resolve_approval_does_nothing_when_no_pending(self, transport):
        """resolve_approval() is a no-op when there is no pending approval."""
        # Should not raise
        transport.resolve_approval(True)

    @pytest.mark.asyncio
    async def test_receive_message_passes_approval_handler_to_runtime(self, transport):
        """receive_message() must pass approval_handler=transport.approve to run_turn."""
        mock_ws = AsyncMock()
        mock_ws._wisp_conn_id = 1

        session = {"id": "s1", "model": "m", "workspace": "/tmp", "messages": []}
        transport._connections[1] = {"ws": mock_ws, "session": session}
        transport._current_ws = mock_ws

        async def fake_run_turn(session, prompt, approval_handler=None):
            assert approval_handler is transport.approve
            yield {"type": "done"}

        transport.runtime.run_turn = fake_run_turn

        await transport.receive_message(mock_ws, {"type": "user", "text": "hello"})

    @pytest.mark.asyncio
    async def test_approval_request_event_structure(self, transport):
        """The approval_request event must contain the tool_call dict."""
        mock_ws = AsyncMock()
        transport._current_ws = mock_ws

        # Start approve() in background so we can inspect the sent event
        task = asyncio.create_task(
            transport.approve({"name": "edit", "arguments": {"path": "a.py"}})
        )
        await asyncio.sleep(0.01)

        # Verify the approval_request event structure
        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.await_args[0][0]
        assert sent["type"] == "approval_request"
        assert sent["tool_call"]["name"] == "edit"
        assert sent["tool_call"]["arguments"]["path"] == "a.py"

        # Resolve so the task can complete
        transport.resolve_approval(True)
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result is True
