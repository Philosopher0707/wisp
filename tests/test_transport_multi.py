"""TDD for MultiTransport.

Tests that MultiTransport broadcasts to child transports correctly.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock

from wisp.transport.multi import MultiTransport
from wisp.transport.base import Transport


class TestMultiTransport:
    """MultiTransport forwards to child transports."""

    def test_implements_transport(self):
        assert issubclass(MultiTransport, Transport)

    def test_start_starts_all_children(self):
        t1 = MagicMock()
        t2 = MagicMock()
        multi = MultiTransport([t1, t2])
        multi.start()
        t1.start.assert_called_once()
        t2.start.assert_called_once()

    def test_stop_stops_all_children(self):
        t1 = MagicMock()
        t2 = MagicMock()
        multi = MultiTransport([t1, t2])
        multi.stop()
        t1.stop.assert_called_once()
        t2.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_broadcasts_to_all(self):
        t1 = MagicMock()
        t1.send = AsyncMock()
        t2 = MagicMock()
        t2.send = AsyncMock()
        multi = MultiTransport([t1, t2])
        await multi.send({"type": "content", "text": "hello"})
        t1.send.assert_called_once_with({"type": "content", "text": "hello"})
        t2.send.assert_called_once_with({"type": "content", "text": "hello"})

    @pytest.mark.asyncio
    async def test_send_continues_on_failure(self):
        t1 = MagicMock()
        t1.send = AsyncMock(side_effect=Exception("boom"))
        t2 = MagicMock()
        t2.send = AsyncMock()
        multi = MultiTransport([t1, t2])
        await multi.send({"type": "content"})
        t1.send.assert_called_once()
        t2.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_recv_returns_first_non_none(self):
        t1 = MagicMock()
        t1.recv = AsyncMock(return_value=None)
        t2 = MagicMock()
        t2.recv = AsyncMock(return_value="hello")
        multi = MultiTransport([t1, t2])
        result = await multi.recv()
        assert result == "hello"

    @pytest.mark.asyncio
    async def test_recv_returns_none_when_all_none(self):
        t1 = MagicMock()
        t1.recv = AsyncMock(return_value=None)
        t2 = MagicMock()
        t2.recv = AsyncMock(return_value=None)
        multi = MultiTransport([t1, t2])
        result = await multi.recv()
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_returns_false_if_not_all_approve(self):
        t1 = MagicMock()
        t1.approve = AsyncMock(return_value=False)
        t2 = MagicMock()
        t2.approve = AsyncMock(return_value=True)
        multi = MultiTransport([t1, t2])
        result = await multi.approve({"name": "run_bash"})
        assert result is False

    @pytest.mark.asyncio
    async def test_approve_returns_false_if_none_approve(self):
        t1 = MagicMock()
        t1.approve = AsyncMock(return_value=False)
        t2 = MagicMock()
        t2.approve = AsyncMock(return_value=False)
        multi = MultiTransport([t1, t2])
        result = await multi.approve({"name": "run_bash"})
        assert result is False
