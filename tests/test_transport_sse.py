"""TDD for SSE transport (queue-based, StreamingResponse-compatible).

The SSE transport is now queue-based and designed to be consumed by
FastAPI's StreamingResponse. It does not send raw HTTP headers.
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

    async def run_turn(self, session: dict, prompt: str) -> AsyncIterator[dict]:
        self.turns.append((session["id"], prompt))
        yield {"type": "content", "text": f"echo: {prompt}"}
        yield {"type": "done"}


# ═══════════════════════════════════════════════════════════════════
# 1. Connection handling
# ═══════════════════════════════════════════════════════════════════

class TestConnectionHandling:
    """SSE transport manages connections and sessions."""

    @pytest.mark.asyncio
    async def test_handle_turn_creates_session(self):
        from wisp.transport.sse import SSETransport
        runtime = _MockRuntime()
        transport = SSETransport(runtime)
        transport.start()

        await transport.handle_turn(
            session_id="sess-1", model="qwen", workspace="/tmp", prompt="hello"
        )

        assert "sess-1" in runtime.sessions
        transport.stop()

    @pytest.mark.asyncio
    async def test_event_stream_yields_events(self):
        from wisp.transport.sse import SSETransport
        runtime = _MockRuntime()
        transport = SSETransport(runtime)
        transport.start()

        await transport.handle_turn(
            session_id="sess-1", model="qwen", workspace="/tmp", prompt="hello"
        )

        events = []
        async for event in transport.event_stream():
            if event.get("type") == "_keepalive":
                continue
            events.append(event)
            if len(events) >= 3:  # ready + content + done
                break

        assert any(e.get("type") == "ready" for e in events)
        assert any("echo: hello" in e.get("text", "") for e in events)
        transport.stop()


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
        transport.start()

        await transport.handle_turn(
            session_id="sess-1", model="qwen", workspace="/tmp", prompt="hello"
        )

        assert len(runtime.turns) == 1
        assert runtime.turns[0] == ("sess-1", "hello")
        transport.stop()

    @pytest.mark.asyncio
    async def test_events_formatted_as_sse(self):
        from wisp.transport.sse import SSETransport
        runtime = _MockRuntime()
        transport = SSETransport(runtime)
        transport.start()

        await transport.handle_turn(
            session_id="sess-1", model="qwen", workspace="/tmp", prompt="hello"
        )

        events = []
        async for event in transport.event_stream():
            if event.get("type") == "_keepalive":
                continue
            events.append(event)
            if len(events) >= 3:
                break

        sse_lines = [transport.format_sse(e) for e in events]
        assert any("data:" in line for line in sse_lines)
        assert any("echo: hello" in line for line in sse_lines)
        transport.stop()


# ═══════════════════════════════════════════════════════════════════
# 3. Error handling
# ═══════════════════════════════════════════════════════════════════

class TestErrorHandling:
    """Errors are queued, not crashed."""

    @pytest.mark.asyncio
    async def test_runtime_error_queued_as_error_event(self):
        from wisp.transport.sse import SSETransport

        class _BrokenRuntime:
            async def get_or_create_session(self, **kwargs):
                return {"id": "s1"}
            async def run_turn(self, session, prompt):
                raise RuntimeError("boom")
                yield

        runtime = _BrokenRuntime()
        transport = SSETransport(runtime)
        transport.start()

        await transport.handle_turn(
            session_id="sess-1", model="qwen", workspace="/tmp", prompt="hello"
        )

        events = []
        async for event in transport.event_stream():
            if event.get("type") == "_keepalive":
                continue
            events.append(event)
            if len(events) >= 2:
                break

        assert any(e.get("type") == "error" for e in events)
        assert any("boom" in e.get("message", "") for e in events)
        transport.stop()
