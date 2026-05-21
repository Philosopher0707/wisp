"""TDD for legacy transport adapters.

Tests that old transports can be wrapped to implement Transport ABC.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestCLITransportAdapter:
    """CLITransportAdapter wraps old CLITransport for Transport ABC."""

    def test_adapter_exists(self):
        from wisp.transport.adapters import CLITransportAdapter
        assert CLITransportAdapter is not None

    def test_adapter_implements_transport(self):
        from wisp.transport.base import Transport
        from wisp.transport.adapters import CLITransportAdapter
        assert issubclass(CLITransportAdapter, Transport)

    def test_start_stops_old_transport(self):
        from wisp.transport.adapters import CLITransportAdapter
        mock_core = MagicMock()
        adapter = CLITransportAdapter(mock_core)
        adapter.start()
        assert adapter._started is True

    def test_stop_cleans_up(self):
        from wisp.transport.adapters import CLITransportAdapter
        mock_core = MagicMock()
        adapter = CLITransportAdapter(mock_core)
        adapter.start()
        adapter.stop()
        assert adapter._started is False

    @pytest.mark.asyncio
    async def test_send_ignores_events(self):
        from wisp.transport.adapters import CLITransportAdapter
        mock_core = MagicMock()
        adapter = CLITransportAdapter(mock_core)
        await adapter.send({"type": "content", "text": "hello"})

    @pytest.mark.asyncio
    async def test_recv_returns_none(self):
        from wisp.transport.adapters import CLITransportAdapter
        mock_core = MagicMock()
        adapter = CLITransportAdapter(mock_core)
        result = await adapter.recv()
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_returns_false(self):
        from wisp.transport.adapters import CLITransportAdapter
        mock_core = MagicMock()
        adapter = CLITransportAdapter(mock_core)
        result = await adapter.approve({"name": "run_bash"})
        assert result is False


class TestServerTransportAdapter:
    """ServerTransportAdapter wraps old ServerTransport for Transport ABC."""

    def test_adapter_exists(self):
        from wisp.transport.adapters import ServerTransportAdapter
        assert ServerTransportAdapter is not None

    def test_adapter_implements_transport(self):
        from wisp.transport.base import Transport
        from wisp.transport.adapters import ServerTransportAdapter
        assert issubclass(ServerTransportAdapter, Transport)

    def test_start_stops_old_transport(self):
        from wisp.transport.adapters import ServerTransportAdapter
        mock_core = MagicMock()
        mock_send = MagicMock()
        adapter = ServerTransportAdapter(mock_core, mock_send)
        adapter.start()
        assert adapter._started is True

    def test_stop_cleans_up(self):
        from wisp.transport.adapters import ServerTransportAdapter
        mock_core = MagicMock()
        mock_send = MagicMock()
        adapter = ServerTransportAdapter(mock_core, mock_send)
        adapter.start()
        adapter.stop()
        assert adapter._started is False

    @pytest.mark.asyncio
    async def test_send_forwards_to_callback(self):
        from wisp.transport.adapters import ServerTransportAdapter
        mock_core = MagicMock()
        mock_send = MagicMock()
        adapter = ServerTransportAdapter(mock_core, mock_send)
        await adapter.send({"type": "content", "text": "hello"})
        mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_recv_returns_none(self):
        from wisp.transport.adapters import ServerTransportAdapter
        mock_core = MagicMock()
        mock_send = MagicMock()
        adapter = ServerTransportAdapter(mock_core, mock_send)
        result = await adapter.recv()
        assert result is None

    @pytest.mark.asyncio
    async def test_approve_returns_false(self):
        from wisp.transport.adapters import ServerTransportAdapter
        mock_core = MagicMock()
        mock_send = MagicMock()
        adapter = ServerTransportAdapter(mock_core, mock_send)
        result = await adapter.approve({"name": "run_bash"})
        assert result is False
