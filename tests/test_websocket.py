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
        """approve() sends the CLIENTS' approval frame and blocks until resolved.

        **Contract update, ADR-0061 R1.** This asserted `approval_request` /
        `{approval_id, tool_call}` — the shape *no client reads*. All three shipped
        clients branch on `tool_approval_request` and read `call_id`, `name`,
        `arguments`, `reason`, so the old frame never rendered and every request hit
        the 60 s bound and denied (W1). The update is the fix, not a relaxation.
        """
        mock_ws = AsyncMock()
        transport._current_ws = mock_ws

        # approve() should send the clients' frame and then wait
        approval_task = asyncio.create_task(
            transport.approve({"name": "write_file", "arguments": {"path": "x"}})
        )

        # Give approve() time to send the request
        await asyncio.sleep(0.01)
        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.await_args[0][0]
        assert sent["type"] == "tool_approval_request"
        assert sent["name"] == "write_file"
        assert sent["arguments"] == {"path": "x"}
        assert "call_id" in sent, "the correlation key clients echo is absent"

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
    async def test_approve_denies_when_no_ws(self, transport, monkeypatch):
        """No connected client means no human to approve: deny by default."""
        monkeypatch.delenv("WISP_WS_AUTO_APPROVE", raising=False)
        transport._current_ws = None
        result = await transport.approve({"name": "write_file", "arguments": {}})
        assert result is False

    @pytest.mark.asyncio
    async def test_approve_auto_approves_only_with_explicit_opt_in(self, transport, monkeypatch):
        """WISP_WS_AUTO_APPROVE=true is the explicit headless opt-in."""
        monkeypatch.setenv("WISP_WS_AUTO_APPROVE", "true")
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
    async def test_approval_frame_structure(self, transport):
        """The approval frame carries the four fields the clients read (ADR-0061 R1).

        Renamed from `test_approval_request_event_structure`: it asserted
        `approval_request` / `tool_call`, the shape no client reads.
        """
        mock_ws = AsyncMock()
        transport._current_ws = mock_ws

        # Start approve() in background so we can inspect the sent event
        task = asyncio.create_task(
            transport.approve({"name": "edit", "arguments": {"path": "a.py"}})
        )
        await asyncio.sleep(0.01)

        # Verify the frame structure the clients parse
        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.await_args[0][0]
        assert sent["type"] == "tool_approval_request"
        assert sent["name"] == "edit"
        assert sent["arguments"]["path"] == "a.py"
        assert set(sent) == {"type", "call_id", "name", "arguments", "reason"}, (
            f"the frame's key set moved: {sorted(sent)} — clients read exactly "
            f"call_id/name/arguments/reason"
        )

        # Resolve so the task can complete
        transport.resolve_approval(True)
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result is True


class TestWebSocketPerKeyApprovals:
    """Issue 3: per-key approval slots with session routing + disconnect resolution."""

    @pytest.mark.asyncio
    async def test_concurrent_distinct_approvals_both_resolve_by_id(self, transport):
        """Two concurrent approvals with different ids must NOT second-deny."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        async def run(sid, ws, tool_call):
            with transport._turn_context(sid, ws):
                return await transport.approve(tool_call)

        task1 = asyncio.create_task(
            run("s1", ws1, {"name": "write_file", "id": "call-1", "arguments": {}})
        )
        task2 = asyncio.create_task(
            run("s2", ws2, {"name": "bash", "id": "call-2", "arguments": {}})
        )
        await asyncio.sleep(0.05)
        # Neither approval may have been denied while waiting for its own id.
        assert not task1.done()
        assert not task2.done()
        sent1 = ws1.send_json.await_args[0][0]
        sent2 = ws2.send_json.await_args[0][0]
        assert sent1["type"] == "tool_approval_request"
        assert sent2["type"] == "tool_approval_request"
        # ADR-0061 R2: the correlation key is `call_id` — what the clients echo as
        # `id` and what the route resolves on. It IS the `_approvals` key.
        assert sent1["call_id"] != sent2["call_id"]

        transport.resolve_approval(True, approval_id=sent1["call_id"])
        transport.resolve_approval(False, approval_id=sent2["call_id"])

        assert await asyncio.wait_for(task1, timeout=1.0) is True
        assert await asyncio.wait_for(task2, timeout=1.0) is False

    @pytest.mark.asyncio
    async def test_same_approval_id_reentrant_denied(self, transport):
        """Same approval id twice while unresolved denies the second (per-key guard)."""
        mock_ws = AsyncMock()
        transport._current_ws = mock_ws
        tool_call = {"name": "bash", "id": "call-same", "arguments": {}}

        first = asyncio.create_task(transport.approve(tool_call))
        await asyncio.sleep(0.01)
        second_result = await transport.approve(dict(tool_call))
        assert second_result is False

        transport.resolve_approval(True)
        assert await asyncio.wait_for(first, timeout=1.0) is True

    @pytest.mark.asyncio
    async def test_disconnect_resolves_pending_approval_false_fast(self, transport):
        """disconnect() must fail-closed pending approvals immediately (no 60s hang)."""
        import time

        mock_ws = AsyncMock()
        mock_ws.closed = False
        transport._current_ws = mock_ws

        task = asyncio.create_task(
            transport.approve({"name": "write_file", "arguments": {}})
        )
        await asyncio.sleep(0.01)
        assert not task.done()

        start = time.monotonic()
        await transport.disconnect(mock_ws)
        result = await asyncio.wait_for(task, timeout=5.0)
        elapsed = time.monotonic() - start

        assert result is False
        assert elapsed < 5.0

    @pytest.mark.asyncio
    async def test_approval_frame_carries_the_correlation_key(self, transport):
        """The frame carries `call_id`; resolving by it completes the call (ADR-0061 R2).

        Renamed from `test_approval_request_carries_approval_id`: the key moved from
        `approval_id` to `call_id`, which is what the clients echo back as `id`.
        """
        mock_ws = AsyncMock()
        transport._current_ws = mock_ws

        task = asyncio.create_task(
            transport.approve({"name": "edit", "id": "call-9", "arguments": {"path": "a.py"}})
        )
        await asyncio.sleep(0.01)
        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.await_args[0][0]
        assert sent["type"] == "tool_approval_request"
        assert sent["name"] == "edit"
        assert "call_id" in sent
        assert "approval_id" not in sent, (
            "the old key returned — no client echoes `approval_id`, so the round-trip "
            "would miss and fall back to 'single pending'"
        )

        transport.resolve_approval(True, approval_id=sent["call_id"])
        assert await asyncio.wait_for(task, timeout=1.0) is True


class TestResolveApprovalUnknownIdFallback:
    """Old-protocol back-compat: unknown id falls back to single pending."""

    @pytest.mark.asyncio
    async def test_unknown_id_resolves_single_pending(self, transport):
        """Single pending + resolve_approval(True, 'x') resolves it."""
        mock_ws = AsyncMock()
        transport._current_ws = mock_ws

        task = asyncio.create_task(
            transport.approve({"name": "bash", "arguments": {}})
        )
        await asyncio.sleep(0.01)
        assert not task.done()

        assert transport.resolve_approval(True, approval_id="x") is True
        assert await asyncio.wait_for(task, timeout=1.0) is True

    @pytest.mark.asyncio
    async def test_unknown_id_with_zero_pending_returns_false(self, transport):
        """Unknown id with nothing pending is a no-op returning False."""
        assert transport.resolve_approval(True, approval_id="x") is False

    @pytest.mark.asyncio
    async def test_unknown_id_with_two_pending_noops(self, transport):
        """Unknown id with two unresolved entries must NOT resolve either."""
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        async def run(sid, ws, tool_call):
            with transport._turn_context(sid, ws):
                return await transport.approve(tool_call)

        task1 = asyncio.create_task(
            run("s1", ws1, {"name": "bash", "id": "call-1", "arguments": {}})
        )
        task2 = asyncio.create_task(
            run("s2", ws2, {"name": "bash", "id": "call-2", "arguments": {}})
        )
        await asyncio.sleep(0.05)
        assert not task1.done()
        assert not task2.done()

        assert transport.resolve_approval(True, approval_id="x") is False
        assert not task1.done()
        assert not task2.done()

        # Cleanup: resolve both by real id so tasks do not hang.
        sent1 = ws1.send_json.await_args[0][0]
        sent2 = ws2.send_json.await_args[0][0]
        assert transport.resolve_approval(True, approval_id=sent1["call_id"]) is True
        assert transport.resolve_approval(True, approval_id=sent2["call_id"]) is True
        assert await asyncio.wait_for(task1, timeout=1.0) is True
        assert await asyncio.wait_for(task2, timeout=1.0) is True
