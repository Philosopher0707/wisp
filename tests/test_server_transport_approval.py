"""Tests for ServerTransport approval call_id consistency.

Regression: _event_to_json() used to generate a fresh call_id for every
TYPE_APPROVAL_REQUEST event, while the inline _ws_approval handler generated
its own.  The transport skipped the duplicate with `continue`, but the counter
still skewed and the code was fragile.

After the fix, _event_to_json() returns None for approval requests so the
natural `if msg is not None` guard handles it without special-casing.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from wisp.transport.server import ServerTransport, PendingApproval
from wisp.core.events import AgentEvent, TYPE_APPROVAL_REQUEST, TYPE_CONTENT


class TestApprovalCallIdConsistency:
    """Approval call_id must be generated exactly once per request."""

    @pytest.mark.asyncio
    async def test_event_to_json_returns_none_for_approval(self):
        """_event_to_json must not generate a call_id for approval events."""
        core = MagicMock()
        transport = ServerTransport(core, send_callback=AsyncMock())

        event = AgentEvent(TYPE_APPROVAL_REQUEST, {
            "name": "run_bash",
            "arguments": {"command": "ls"},
            "reason": "dangerous",
        })

        msg = transport._event_to_json(event)
        assert msg is None
        # Counter must NOT have been incremented
        assert transport._call_counter == 0

    @pytest.mark.asyncio
    async def test_request_approval_generates_call_id(self):
        """The approval handler is the sole source of call_ids."""
        import asyncio
        from unittest.mock import patch

        core = MagicMock()
        send_fn = AsyncMock()
        transport = ServerTransport(core, send_callback=send_fn)

        # Patch the timeout so the test doesn't hang 300s waiting for approval
        orig_wait_for = asyncio.wait_for
        async def _fast_wait_for(coro, timeout):
            return await orig_wait_for(coro, timeout=0.01)

        with patch.object(asyncio, "wait_for", _fast_wait_for):
            result = await transport._request_approval("run_bash", {"command": "ls"}, "dangerous")

        assert result == (False, None)  # timeout because nobody approved

        # Exactly one call_id consumed
        assert transport._call_counter == 1
        # Message sent to client
        assert send_fn.call_count == 1
        sent = send_fn.call_args[0][0]
        assert sent["type"] == "tool_approval_request"
        assert sent["call_id"] == "tc-1"

    @pytest.mark.asyncio
    async def test_content_event_still_works(self):
        """Non-approval events are unaffected."""
        core = MagicMock()
        transport = ServerTransport(core, send_callback=AsyncMock())

        event = AgentEvent(TYPE_CONTENT, {"text": "hello"})
        msg = transport._event_to_json(event)
        assert msg == {"type": "token", "text": "hello", "phase": "content"}
