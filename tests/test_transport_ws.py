"""TDD for WebSocket transport.

Replaces: the ad-hoc WebSocket handling in server.py.
Clean separation: transport owns the wire protocol, runtime owns the logic.
"""

import pytest
from typing import AsyncIterator


# ── Minimal mock runtime for testing ───────────────────────────────

class _MockRuntime:
    def __init__(self):
        self.sessions = {}
        self.turns = []

    async def get_or_create_session(self, session_id: str, model: str, workspace: str):
        if session_id not in self.sessions:
            self.sessions[session_id] = {
                "id": session_id,
                "model": model,
                "workspace": workspace,
                "messages": [],
            }
        return self.sessions[session_id]

    async def run_turn(self, session: dict, prompt: str, **kwargs) -> AsyncIterator[dict]:
        self.turns.append((session["id"], prompt))
        yield {"type": "content", "text": f"echo: {prompt}"}
        yield {"type": "done"}


# ── Minimal mock WebSocket for testing ────────────────────────────

class _MockWebSocket:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.close_code = None

    async def send_json(self, data: dict):
        self.sent.append(data)

    async def close(self, code: int = 1000):
        self.closed = True
        self.close_code = code


# ═══════════════════════════════════════════════════════════════════
# 1. Connection handling
# ═══════════════════════════════════════════════════════════════════

class TestConnectionHandling:
    """WebSocket transport manages connections and sessions."""

    @pytest.mark.asyncio
    async def test_connect_creates_session(self):
        from wisp.transport.websocket import WebSocketTransport
        runtime = _MockRuntime()
        transport = WebSocketTransport(runtime)
        ws = _MockWebSocket()

        await transport.handle(ws, session_id="sess-1", model="qwen", workspace="/tmp")

        assert "sess-1" in runtime.sessions
        assert runtime.sessions["sess-1"]["model"] == "qwen"

    @pytest.mark.asyncio
    async def test_connect_sends_ready_event(self):
        from wisp.transport.websocket import WebSocketTransport
        runtime = _MockRuntime()
        transport = WebSocketTransport(runtime)
        ws = _MockWebSocket()

        await transport.handle(ws, session_id="sess-1", model="qwen", workspace="/tmp")

        assert len(ws.sent) >= 1
        assert ws.sent[0]["type"] == "ready"


# ═══════════════════════════════════════════════════════════════════
# 2. Message routing
# ═══════════════════════════════════════════════════════════════════

class TestMessageRouting:
    """Incoming messages are routed to the runtime."""

    @pytest.mark.asyncio
    async def test_user_message_triggers_turn(self):
        from wisp.transport.websocket import WebSocketTransport
        runtime = _MockRuntime()
        transport = WebSocketTransport(runtime)
        ws = _MockWebSocket()

        await transport.handle(ws, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.receive_message(ws, {"type": "user", "text": "hello"})

        assert len(runtime.turns) == 1
        assert runtime.turns[0] == ("sess-1", "hello")

    @pytest.mark.asyncio
    async def test_events_streamed_back(self):
        from wisp.transport.websocket import WebSocketTransport
        runtime = _MockRuntime()
        transport = WebSocketTransport(runtime)
        ws = _MockWebSocket()

        await transport.handle(ws, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.receive_message(ws, {"type": "user", "text": "hello"})

        content_events = [e for e in ws.sent if e.get("type") == "content"]
        assert len(content_events) == 1
        assert content_events[0]["text"] == "echo: hello"


# ═══════════════════════════════════════════════════════════════════
# 3. Disconnection handling
# ═══════════════════════════════════════════════════════════════════

class TestDisconnectionHandling:
    """Disconnections are handled gracefully."""

    @pytest.mark.asyncio
    async def test_disconnect_closes_websocket(self):
        from wisp.transport.websocket import WebSocketTransport
        runtime = _MockRuntime()
        transport = WebSocketTransport(runtime)
        ws = _MockWebSocket()

        await transport.handle(ws, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.disconnect(ws)

        assert ws.closed

    @pytest.mark.asyncio
    async def test_disconnect_removes_session(self):
        from wisp.transport.websocket import WebSocketTransport
        runtime = _MockRuntime()
        transport = WebSocketTransport(runtime)
        ws = _MockWebSocket()

        await transport.handle(ws, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.disconnect(ws)

        assert "sess-1" not in transport._connections


# ═══════════════════════════════════════════════════════════════════
# 4. Multiple sessions
# ═══════════════════════════════════════════════════════════════════

class TestMultipleSessions:
    """Multiple concurrent sessions are isolated."""

    @pytest.mark.asyncio
    async def test_sessions_are_isolated(self):
        from wisp.transport.websocket import WebSocketTransport
        runtime = _MockRuntime()
        transport = WebSocketTransport(runtime)
        ws1 = _MockWebSocket()
        ws2 = _MockWebSocket()

        await transport.handle(ws1, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.handle(ws2, session_id="sess-2", model="qwen", workspace="/tmp")

        await transport.receive_message(ws1, {"type": "user", "text": "msg1"})
        await transport.receive_message(ws2, {"type": "user", "text": "msg2"})

        assert runtime.turns[0] == ("sess-1", "msg1")
        assert runtime.turns[1] == ("sess-2", "msg2")


# ═══════════════════════════════════════════════════════════════════
# 5. Error handling
# ═══════════════════════════════════════════════════════════════════

class TestErrorHandling:
    """Errors are sent to the client, not crashed."""

    @pytest.mark.asyncio
    async def test_runtime_error_sent_to_client(self):
        from wisp.transport.websocket import WebSocketTransport

        class _BrokenRuntime:
            async def get_or_create_session(self, **kwargs):
                return {"id": "s1"}
            async def run_turn(self, session, prompt, **kwargs):
                raise RuntimeError("boom")
                yield  # make it an async generator

        runtime = _BrokenRuntime()
        transport = WebSocketTransport(runtime)
        ws = _MockWebSocket()

        await transport.handle(ws, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.receive_message(ws, {"type": "user", "text": "hello"})

        error_events = [e for e in ws.sent if e.get("type") == "error"]
        assert len(error_events) >= 1
        assert "boom" in error_events[0].get("message", "")
