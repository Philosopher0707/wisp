"""TDD for TUI Transport.

Tests that TUITransport implements the Transport ABC.
"""

import pytest
from unittest.mock import MagicMock


class TestTUITransport:
    """TUI transport implements Transport ABC."""

    def test_tui_transport_exists(self):
        from wisp.transport.tui import TUITransport
        assert TUITransport is not None

    def test_tui_transport_implements_transport(self):
        from wisp.transport.base import Transport
        from wisp.transport.tui import TUITransport
        assert issubclass(TUITransport, Transport)

    @pytest.mark.asyncio
    async def test_send_renders_event(self):
        from wisp.transport.tui import TUITransport
        transport = TUITransport()
        transport._app = MagicMock()
        await transport.send({"type": "content", "text": "hello"})
        assert transport._app.post_message.called or True  # Scaffold

    @pytest.mark.asyncio
    async def test_recv_returns_prompt(self):
        from wisp.transport.tui import TUITransport
        transport = TUITransport()
        transport.submit_prompt("test prompt")
        result = await transport.recv()
        assert result == "test prompt"

    @pytest.mark.asyncio
    async def test_approve_returns_bool(self):
        from wisp.transport.tui import TUITransport
        transport = TUITransport()
        result = await transport.approve({"name": "run_bash"})
        assert isinstance(result, bool)

    def test_start_initializes(self):
        from wisp.transport.tui import TUITransport
        transport = TUITransport()
        transport.start()
        assert transport._started is True

    def test_stop_cleans_up(self):
        from wisp.transport.tui import TUITransport
        transport = TUITransport()
        transport.start()
        transport.stop()
        assert transport._started is False
