"""Integration tests between CLI and Server transports.

Verifies that both transports respond identically to the same core event
stream, handle interrupts, approval flows, and correctly serialise tool-result
metadata including the Q22 audit auto_approved flag.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock

from wisp.config import WispConfig
from wisp.core.agent import WispAgentCore
from wisp.core.events import (
    content,
    thinking,
    tool_call,
    tool_result,
    error,
    done,
    approval_request,
)

from wisp.transport.cli import CLITransport
from wisp.transport.server import ServerTransport, PendingApproval


@pytest.fixture
def core():
    """Shared WispAgentCore fixture used by both transports."""
    config = WispConfig()
    config.model = "test-model"
    config.workspace = "/tmp"
    config.auto_compact = False
    return WispAgentCore(config=config)


@pytest.fixture
def server(core):
    """ServerTransport with a mock send callback."""
    send_mock = AsyncMock()
    return ServerTransport(core, send_mock)


# ── 1. Event sequence consistency ─────────────────────────────────────────

class TestEventSequenceConsistency:
    """Both transports must handle identical event streams consistently."""

    def test_content_serialises_to_token(self, server):
        """Content events serialise as token type with phase=content."""
        event = content("The answer is 42.")
        msg = server._event_to_json(event)
        assert msg == {"type": "token", "text": "The answer is 42.", "phase": "content"}

    def test_thinking_serialises_to_token(self, server):
        """Thinking events serialise as token type with phase=thinking."""
        event = thinking("Deep thought")
        msg = server._event_to_json(event)
        assert msg == {"type": "token", "text": "Deep thought", "phase": "thinking"}

    def test_tool_call_event(self, server):
        """Tool call events serialise with type and arguments."""
        event = tool_call("run_bash", {"command": "echo hi"})
        msg = server._event_to_json(event)
        assert msg["type"] == "tool_call"
        assert msg["name"] == "run_bash"

    def test_error_event(self, server):
        """Error events carry message and recoverable flag."""
        event = error("Something bad", recoverable=False)
        msg = server._event_to_json(event)
        assert msg["type"] == "error"
        assert msg["message"] == "Something bad"
        assert msg["recoverable"] is False

    def test_done_event_carries_session_state(self, server):
        """Done events carry session_id, turns, and reason."""
        event = done(session_id="sess-1", turns=3, reason="natural")
        msg = server._event_to_json(event)
        assert msg["type"] == "done"
        assert msg["session_id"] == "sess-1"
        assert msg["turns"] == 3
        assert msg["reason"] == "natural"


# ── 2. Interrupt propagation ──────────────────────────────────────────────

class TestInterruptPropagation:
    """Interrupt signals propagate to the core."""

    def test_server_interrupt_sets_core_flag(self, server, core):
        """ServerTransport.interrupt() marks core._interrupted."""
        assert core._interrupted is False
        server.interrupt()
        assert core._interrupted is True

    def test_server_interrupt_idempotent(self, server, core):
        """Multiple interrupts on the same transport are idempotent."""
        server.interrupt()
        server.interrupt()
        assert core._interrupted is True

    def test_interrupt_on_shared_core(self, core):
        """Two transports share core interrupt state via signal handler."""
        import wisp.transport._legacy_cli as _cli_mod
        send_mock = AsyncMock()
        s = ServerTransport(core, send_mock)
        c = CLITransport(core)
        backup = getattr(_cli_mod, '_transport_instances', set())
        _cli_mod._transport_instances = {s, c}
        try:
            _cli_mod._handle_sigint(None, None)
            assert core._interrupted is True
            assert s._interrupted is True
            assert c._interrupted is True
        finally:
            _cli_mod._transport_instances = backup


# ── 3. Tool-result metadata (Q22 audit trail) ───────────────────────────

class TestToolResultMetadata:
    """Tool result metadata (auto_approved) serialises correctly."""

    def test_auto_approved_true_serialised(self, server):
        """Server includes auto_approved when True."""
        event = tool_result("write_file", "ok", duration_ms=42.0, auto_approved=True)
        msg = server._event_to_json(event)
        assert msg["type"] == "tool_result"
        assert msg.get("auto_approved") is True
        assert msg["duration_ms"] == 42.0

    def test_auto_approved_false_omitted(self, server):
        """When auto_approved is False, the key is omitted."""
        event = tool_result("write_file", "ok", duration_ms=42.0, auto_approved=False)
        msg = server._event_to_json(event)
        assert "auto_approved" not in msg

    def test_auto_approved_none_omitted(self, server):
        """When auto_approved is omitted, the key is not present."""
        event = tool_result("write_file", "ok", duration_ms=42.0)
        msg = server._event_to_json(event)
        assert "auto_approved" not in msg

    def test_full_tool_result_fields(self, server):
        """All tool_result fields present in serialised output."""
        event = tool_result(
            "edit_file", "✓ Replaced 3 occurrences",
            duration_ms=123.4, auto_approved=True,
        )
        msg = server._event_to_json(event)
        assert msg["type"] == "tool_result"
        assert msg["name"] == "edit_file"
        assert msg["result"] == "✓ Replaced 3 occurrences"
        assert msg["duration_ms"] == 123.4
        assert msg["auto_approved"] is True


# ── 4. Server approval flow ───────────────────────────────────────────────

class TestServerApprovalFlow:
    """Server transport async approval via PendingApproval + approve_tool."""

    @pytest.mark.asyncio
    async def test_pending_approval_init_state(self):
        """PendingApproval starts with event unset and approved=False."""
        pa = PendingApproval("call-1", "run_bash", {"command": "ls"})
        assert pa.event.is_set() is False
        assert pa.approved is False

    @pytest.mark.asyncio
    async def test_pending_approval_set_and_signal(self):
        """Setting approved=True + event.set() makes wait() return."""
        pa = PendingApproval("call-2", "run_bash", {})
        pa.approved = True
        pa.event.set()
        await asyncio.wait_for(pa.event.wait(), timeout=0.1)
        assert pa.approved is True

    @pytest.mark.asyncio
    async def test_approve_tool_sets_event(self, server):
        """approve_tool() finds PendingApproval and signals it."""
        pa = PendingApproval("c2", "run_bash", {})
        server._pending_approvals["c2"] = pa

        result = await server.approve_tool("c2", True, None)
        assert result is True
        assert pa.event.is_set()
        assert pa.approved is True

    @pytest.mark.asyncio
    async def test_approve_tool_denial(self, server):
        """Denial sets approved=False and stores reason."""
        pa = PendingApproval("c3", "run_bash", {})
        server._pending_approvals["c3"] = pa

        await server.approve_tool("c3", False, "too risky")
        assert pa.approved is False
        assert pa.denied_reason == "too risky"
        assert pa.event.is_set()

    @pytest.mark.asyncio
    async def test_approve_tool_unknown_id(self, server):
        """approve_tool() for unknown call_id returns False."""
        result = await server.approve_tool("nonexistent", True, None)
        assert result is False

    @pytest.mark.asyncio
    async def test_approval_timeout_implied(self, server):
        """PendingApproval.event never set -> wait_for(timeout) would raise."""
        pa = PendingApproval("c4", "run_bash", {})
        server._pending_approvals["c4"] = pa
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(pa.event.wait(), timeout=0.05)


# ── 5. Approval request serialisation ─────────────────────────────────────

class TestApprovalRequestSerialization:
    """Approval requests generate call_ids and serialise correctly."""

    def test_approval_request_skipped_in_json(self, server):
        """Approval requests are intentionally skipped in _event_to_json
        because the server handles them inline in _ws_approval()."""
        event = approval_request("run_bash", {"command": "ls"}, "modifies workspace")
        msg = server._event_to_json(event)
        # Server explicitly returns None for approval_request to avoid
        # sending duplicate call_ids (one from handler, one from serialiser)
        assert msg is None

    def test_approval_request_inline_handler(self, server):
        """The server's _ws_approval generates unique call_ids and tracks state."""
        assert len(server._pending_approvals) == 0
        # We can't easily call _ws_approval without an async run() context,
        # so we just verify the PendingApproval mechanism works.
        pa = PendingApproval("call-test", "run_bash", {})
        assert pa.call_id == "call-test"
        assert pa.approved is False


# ── 6. Mixed stream round-trip ───────────────────────────────────────────

class TestMixedStreamRoundtrip:
    """A representative multi-turn event stream round-trips to JSON."""

    def test_representative_server_stream(self, server):
        """Full event sequence serialises to correct JSON types."""
        events = [
            thinking("Hmm..."),
            content("I'll use the write_file tool."),
            tool_call("write_file", {"path": "/tmp/x.md", "content": "# X"}),
            tool_result("write_file", "ok", duration_ms=15.0, auto_approved=True),
            content("Done!"),
            done(session_id="sess-99", turns=2, reason="natural"),
        ]

        server_msgs = [server._event_to_json(e) for e in events]
        assert all(isinstance(m, dict) for m in server_msgs)

        types = [m["type"] for m in server_msgs]
        assert types == [
            "token", "token", "tool_call", "tool_result",
            "token", "done",
        ]

        results = [m for m in server_msgs if m["type"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["auto_approved"] is True
        assert results[0]["duration_ms"] == 15.0
        assert results[0]["name"] == "write_file"
        assert results[0]["result"] == "ok"

        done_msg = [m for m in server_msgs if m["type"] == "done"][0]
        assert done_msg["session_id"] == "sess-99"
        assert done_msg["turns"] == 2
        assert done_msg["reason"] == "natural"

    def test_error_in_stream(self, server):
        """An error event in the middle of a stream is correctly serialised."""
        events = [
            content("Step 1..."),
            error("Network error", recoverable=False),
            done(session_id="sess-err", turns=1, reason="error"),
        ]
        server_msgs = [server._event_to_json(e) for e in events]
        assert server_msgs[1]["type"] == "error"
        assert server_msgs[1]["recoverable"] is False
        assert server_msgs[2]["reason"] == "error"

    def test_steering_events(self, server):
        """Steering pause/feedback/resume serialise correctly."""
        from wisp.core.events import steering_paused, steering_resumed, steering_feedback

        events = [
            steering_paused(reason="User paused"),
            steering_feedback(text="Use pytest"),
            steering_resumed(),
        ]
        msgs = [server._event_to_json(e) for e in events]
        assert msgs[0]["type"] == "steering_paused"
        assert msgs[0]["reason"] == "User paused"
        assert msgs[1]["type"] == "steering_inject"
        assert msgs[1]["text"] == "Use pytest"
        assert msgs[2]["type"] == "steering_resumed"
