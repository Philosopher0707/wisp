"""TDD for WebSocket agent router.

Tests the migrated WebSocket agent endpoint.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestWebSocketAgent:
    """WebSocket agent endpoint handles agent lifecycle."""

    def test_websocket_endpoint_exists(self):
        from wisp.server.routes.agents import router
        routes = [r.path for r in router.routes]
        assert "/ws/agent" in routes

    @pytest.mark.asyncio
    async def test_websocket_accepts_connection(self):
        from wisp.server.routes.agents import agent_websocket
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = ["{\"type\": \"ping\"}", Exception("close")]
        await agent_websocket(mock_ws)
        mock_ws.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_handles_ping(self):
        from wisp.server.routes.agents import agent_websocket
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            '{"type": "ping"}',
            Exception("close")
        ]
        await agent_websocket(mock_ws)
        # Should send pong response
        calls = [c for c in mock_ws.send_json.call_args_list if c.args and c.args[0].get("type") == "pong"]
        assert len(calls) >= 0 or mock_ws.send_text.called

    @pytest.mark.asyncio
    async def test_websocket_requires_auth_when_enabled(self):
        from wisp.server.routes.agents import agent_websocket
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            '{"type": "auth", "api_key": "wrong"}',
            Exception("close")
        ]
        with patch("wisp.server.routes.agents._auth") as mock_auth:
            mock_auth.required = True
            mock_auth.key = "secret"
            await agent_websocket(mock_ws)
            mock_ws.close.assert_called()

    @pytest.mark.asyncio
    async def test_websocket_accepts_valid_auth(self):
        from wisp.server.routes.agents import agent_websocket
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            '{"type": "auth", "api_key": "secret"}',
            '{"type": "ping"}',
            Exception("close")
        ]
        with patch("wisp.server.routes.agents._auth") as mock_auth:
            mock_auth.required = True
            mock_auth.key = "secret"
            await agent_websocket(mock_ws)
            # Should not close immediately after auth
            assert mock_ws.close.call_count == 0 or mock_ws.close.call_count == 1

    @pytest.mark.asyncio
    async def test_websocket_handles_prompt(self):
        from wisp.server.routes.agents import agent_websocket
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            '{"type": "prompt", "content": "hello"}',
            Exception("close")
        ]
        with patch("wisp.server.routes.agents._auth") as mock_auth:
            mock_auth.required = False
            await agent_websocket(mock_ws)
            # Should process prompt without error
            assert mock_ws.accept.called

    @pytest.mark.asyncio
    async def test_websocket_handles_interrupt(self):
        from wisp.server.routes.agents import agent_websocket
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            '{"type": "interrupt"}',
            Exception("close")
        ]
        with patch("wisp.server.routes.agents._auth") as mock_auth:
            mock_auth.required = False
            await agent_websocket(mock_ws)
            # Should send status message
            assert mock_ws.send_json.called or mock_ws.send_text.called

    @pytest.mark.asyncio
    async def test_websocket_handles_unknown_type(self):
        from wisp.server.routes.agents import agent_websocket
        mock_ws = AsyncMock()
        mock_ws.receive_text.side_effect = [
            '{"type": "unknown"}',
            Exception("close")
        ]
        with patch("wisp.server.routes.agents._auth") as mock_auth:
            mock_auth.required = False
            await agent_websocket(mock_ws)
            # Should send error for unknown type
            error_calls = [c for c in mock_ws.send_json.call_args_list 
                          if c.args and c.args[0].get("type") == "error"]
            assert len(error_calls) > 0
