"""TDD for SSE transport.

Replaces: ad-hoc SSE handling in server.py.
Clean separation: transport owns the wire protocol, runtime owns the logic.
"""

import pytest
from typing import Any, AsyncIterator


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

    async def run_turn(self, session: dict, prompt: str) -> AsyncIterator[dict]:
        self.turns.append((session["id"], prompt))
        yield {"type": "content", "text": f"echo: {prompt}"}
        yield {"type": "done"}


# ── Minimal mock response for testing ──────────────────────────────

class _MockResponse:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, data: str):
        self.sent.append(data)

    async def close(self):
        self.closed = True


# ═══════════════════════════════════════════════════════════════════
# 1. Connection handling
# ═══════════════════════════════════════════════════════════════════

class TestConnectionHandling:
    """SSE transport manages connections and sessions."""

    @pytest.mark.asyncio
    async def test_connect_creates_session(self):
        from wisp.transport.sse import SSETransport
        runtime = _MockRuntime()
        transport = SSETransport(runtime)
        response = _MockResponse()

        await transport.connect(response, session_id="sess-1", model="qwen", workspace="/tmp")

        assert "sess-1" in runtime.sessions

    @pytest.mark.asyncio
    async def test_connect_sends_headers(self):
        from wisp.transport.sse import SSETransport
        runtime = _MockRuntime()
        transport = SSETransport(runtime)
        response = _MockResponse()

        await transport.connect(response, session_id="sess-1", model="qwen", workspace="/tmp")

        assert any("text/event-stream" in s for s in response.sent)


# ═══════════════════════════════════════════════════════════════════
# 2. Message routing
# ═══════════════════════════════════════════════════════════════════

class TestMessageRouting:
    """Incoming messages are routed to the runtime."""

    @pytest.mark.asyncio
    async def test_user_message_triggers_turn(self):
        from wisp.transport.sse import SSETransport
        runtime = _MockRuntime()
        transport = SSETransport(runtime)
        response = _MockResponse()

        await transport.connect(response, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.receive_message(response, {"type": "user", "text": "hello"})

        assert len(runtime.turns) == 1
        assert runtime.turns[0] == ("sess-1", "hello")

    @pytest.mark.asyncio
    async def test_events_streamed_as_sse(self):
        from wisp.transport.sse import SSETransport
        runtime = _MockRuntime()
        transport = SSETransport(runtime)
        response = _MockResponse()

        await transport.connect(response, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.receive_message(response, {"type": "user", "text": "hello"})

        # Check SSE format: data: {...}\n\n
        sse_data = [s for s in response.sent if s.startswith("data:")]
        assert len(sse_data) >= 2  # ready + content
        assert any("echo: hello" in s for s in sse_data)


# ═══════════════════════════════════════════════════════════════════
# 3. Reconnection
# ═══════════════════════════════════════════════════════════════════

class TestReconnection:
    """Clients can reconnect and resume."""

    @pytest.mark.asyncio
    async def test_reconnect_sends_missed_events(self):
        from wisp.transport.sse import SSETransport
        runtime = _MockRuntime()
        transport = SSETransport(runtime)
        response = _MockResponse()

        await transport.connect(response, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.receive_message(response, {"type": "user", "text": "hello"})

        # Simulate reconnection with last-event-id
        response2 = _MockResponse()
        await transport.reconnect(response2, session_id="sess-1", last_event_id="0")

        # Should send buffered events
        sse_data = [s for s in response2.sent if "data:" in s]
        assert len(sse_data) >= 1


# ═══════════════════════════════════════════════════════════════════
# 4. Error handling
# ═══════════════════════════════════════════════════════════════════

class TestErrorHandling:
    """Errors are sent to the client, not crashed."""

    @pytest.mark.asyncio
    async def test_runtime_error_sent_as_sse(self):
        from wisp.transport.sse import SSETransport

        class _BrokenRuntime:
            async def get_or_create_session(self, **kwargs):
                return {"id": "s1"}
            async def run_turn(self, session, prompt):
                raise RuntimeError("boom")
                yield

        runtime = _BrokenRuntime()
        transport = SSETransport(runtime)
        response = _MockResponse()

        await transport.connect(response, session_id="sess-1", model="qwen", workspace="/tmp")
        await transport.receive_message(response, {"type": "user", "text": "hello"})

        error_data = [s for s in response.sent if "error" in s]
        assert len(error_data) >= 1
        assert "boom" in error_data[0]
