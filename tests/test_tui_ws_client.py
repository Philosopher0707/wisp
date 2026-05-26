"""Tests for WispWSClient — WebSocket message dispatch, reconnection, edge cases."""

from __future__ import annotations

import asyncio
import json
import pytest

from wisp.tui.data.ws_client import WispWSClient


class FakeWebSocket:
    """Simulates a websockets connection."""

    def __init__(self, messages: list[str] | None = None):
        self.messages = messages or []
        self.sent: list[str] = []
        self._idx = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        if self._idx >= len(self.messages):
            # Simulate waiting for messages indefinitely
            await asyncio.sleep(10)
            raise StopAsyncIteration
        msg = self.messages[self._idx]
        self._idx += 1
        return msg

    async def send(self, data: str):
        self.sent.append(data)

    async def close(self):
        self.closed = True


# ══════════════════════════════════════════════════════════════════════
# Construction
# ══════════════════════════════════════════════════════════════════════

class TestWSClientConstruction:
    def test_constructs_with_url(self):
        client = WispWSClient("http://localhost:8000")
        assert client.server_url == "http://localhost:8000"

    def test_strips_trailing_slash(self):
        client = WispWSClient("http://localhost:8000/")
        assert client.server_url == "http://localhost:8000"

    def test_callbacks_are_none_by_default(self):
        client = WispWSClient("http://localhost:8000")
        assert client.on_token is None
        assert client.on_tool_call is None
        assert client.on_tool_result is None
        assert client.on_approval_request is None
        assert client.on_complete is None
        assert client.on_error is None

    def test_callbacks_settable(self):
        client = WispWSClient("http://localhost:8000")
        async def dummy(*args, **kwargs): pass
        client.on_token = dummy
        client.on_tool_call = dummy
        assert client.on_token is not None
        assert client.on_tool_call is not None


# ══════════════════════════════════════════════════════════════════════
# Message Dispatch
# ══════════════════════════════════════════════════════════════════════

class TestWSClientDispatch:
    """Tests for _dispatch parsing and callback invocation."""

    @pytest.mark.asyncio
    async def test_dispatch_token_thinking(self):
        client = WispWSClient("http://localhost:8000")
        tokens = []
        async def on_token(phase, text):
            tokens.append((phase, text))
        client.on_token = on_token

        await client._dispatch(json.dumps({
            "type": "token", "phase": "thinking", "text": "Let me think..."
        }))
        assert len(tokens) == 1
        assert tokens[0] == ("thinking", "Let me think...")

    @pytest.mark.asyncio
    async def test_dispatch_token_content(self):
        client = WispWSClient("http://localhost:8000")
        tokens = []
        async def on_token(phase, text):
            tokens.append((phase, text))
        client.on_token = on_token

        await client._dispatch(json.dumps({
            "type": "token", "phase": "content", "text": "Here is the code:"
        }))
        assert tokens[0] == ("content", "Here is the code:")

    @pytest.mark.asyncio
    async def test_dispatch_tool_call(self):
        client = WispWSClient("http://localhost:8000")
        calls = []
        async def on_tool_call(name, args):
            calls.append((name, args))
        client.on_tool_call = on_tool_call

        await client._dispatch(json.dumps({
            "type": "tool_call", "name": "run_bash", "arguments": {"command": "ls"}
        }))
        assert len(calls) == 1
        assert calls[0][0] == "run_bash"
        assert calls[0][1] == {"command": "ls"}

    @pytest.mark.asyncio
    async def test_dispatch_tool_result(self):
        client = WispWSClient("http://localhost:8000")
        results = []
        async def on_tool_result(name, result, duration):
            results.append((name, result, duration))
        client.on_tool_result = on_tool_result

        await client._dispatch(json.dumps({
            "type": "tool_result", "name": "run_bash",
            "result": "file1.py\nfile2.py", "duration_ms": 340
        }))
        assert len(results) == 1
        assert results[0][0] == "run_bash"
        assert results[0][2] == 340

    @pytest.mark.asyncio
    async def test_dispatch_approval_request(self):
        client = WispWSClient("http://localhost:8000")
        approvals = []
        async def on_approval_request(call_id, name, args, reason):
            approvals.append((call_id, name, args, reason))
        client.on_approval_request = on_approval_request

        await client._dispatch(json.dumps({
            "type": "tool_approval_request", "call_id": "abc",
            "name": "run_bash", "arguments": {"command": "rm -rf /"},
            "reason": "DANGEROUS: destructive command"
        }))
        assert len(approvals) == 1
        assert approvals[0][0] == "abc"
        assert "DANGEROUS" in approvals[0][3]

    @pytest.mark.asyncio
    async def test_dispatch_complete(self):
        client = WispWSClient("http://localhost:8000")
        completes = []
        async def on_complete(session_id):
            completes.append(session_id)
        client.on_complete = on_complete

        await client._dispatch(json.dumps({
            "type": "done", "session_id": "session-1"
        }))
        assert completes == ["session-1"]

    @pytest.mark.asyncio
    async def test_dispatch_error(self):
        client = WispWSClient("http://localhost:8000")
        errors = []
        async def on_error(message):
            errors.append(message)
        client.on_error = on_error

        await client._dispatch(json.dumps({
            "type": "error", "message": "Ollama not running"
        }))
        assert errors == ["Ollama not running"]

    @pytest.mark.asyncio
    async def test_dispatch_status(self):
        client = WispWSClient("http://localhost:8000")
        statuses = []
        async def on_status(message, level):
            statuses.append((message, level))
        client.on_status = on_status

        await client._dispatch(json.dumps({
            "type": "status", "message": "Session loaded", "level": "info"
        }))
        assert statuses[0] == ("Session loaded", "info")

    @pytest.mark.asyncio
    async def test_dispatch_missing_callback_no_error(self):
        """Should not crash if callback is not set."""
        client = WispWSClient("http://localhost:8000")
        await client._dispatch(json.dumps({
            "type": "token", "phase": "content", "text": "ok"
        }))

    @pytest.mark.asyncio
    async def test_dispatch_invalid_json(self):
        """Should silently ignore malformed JSON."""
        client = WispWSClient("http://localhost:8000")
        await client._dispatch("not valid json{{{")

    @pytest.mark.asyncio
    async def test_dispatch_unknown_type(self):
        """Unknown message types should be silently ignored."""
        client = WispWSClient("http://localhost:8000")
        await client._dispatch(json.dumps({"type": "some_future_event"}))


# ══════════════════════════════════════════════════════════════════════
# Send methods
# ══════════════════════════════════════════════════════════════════════

class TestWSClientSend:
    @pytest.mark.asyncio
    async def test_send_prompt_sends_json(self):
        client = WispWSClient("http://localhost:8000")
        ws = FakeWebSocket()
        client._ws = ws

        await client.send_prompt("refactor this", session_id="s1", model="llama")
        assert len(ws.sent) == 1
        data = json.loads(ws.sent[0])
        assert data["type"] == "prompt"
        assert data["content"] == "refactor this"
        assert data["session_id"] == "s1"
        assert data["model"] == "llama"

    @pytest.mark.asyncio
    async def test_send_prompt_without_session(self):
        client = WispWSClient("http://localhost:8000")
        ws = FakeWebSocket()
        client._ws = ws

        await client.send_prompt("hello")
        data = json.loads(ws.sent[0])
        assert data["session_id"] is None

    @pytest.mark.asyncio
    async def test_approve_tool(self):
        client = WispWSClient("http://localhost:8000")
        ws = FakeWebSocket()
        client._ws = ws

        await client.approve_tool("call-1", approved=True)
        data = json.loads(ws.sent[0])
        assert data["type"] == "tool_approval"
        assert data["id"] == "call-1"
        assert data["approved"] is True

    @pytest.mark.asyncio
    async def test_deny_tool(self):
        client = WispWSClient("http://localhost:8000")
        ws = FakeWebSocket()
        client._ws = ws

        await client.approve_tool("call-2", approved=False)
        data = json.loads(ws.sent[0])
        assert data["approved"] is False

    @pytest.mark.asyncio
    async def test_interrupt(self):
        client = WispWSClient("http://localhost:8000")
        ws = FakeWebSocket()
        client._ws = ws

        await client.interrupt()
        data = json.loads(ws.sent[0])
        assert data["type"] == "interrupt"

    @pytest.mark.asyncio
    async def test_new_session(self):
        client = WispWSClient("http://localhost:8000")
        ws = FakeWebSocket()
        client._ws = ws

        await client.new_session()
        data = json.loads(ws.sent[0])
        assert data["type"] == "new_session"


# ══════════════════════════════════════════════════════════════════════
# Connection lifecycle
# ══════════════════════════════════════════════════════════════════════

class TestWSClientLifecycle:
    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        client = WispWSClient("http://localhost:8000")
        ws = FakeWebSocket()
        client._ws = ws
        client._running = True

        await client.disconnect()
        assert client._running is False
        assert ws.closed is True

    @pytest.mark.asyncio
    async def test_disconnect_when_not_running(self):
        client = WispWSClient("http://localhost:8000")
        client._running = False
        await client.disconnect()
        assert client._running is False
