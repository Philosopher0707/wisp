"""M3 steering: mid-turn user corrections injected at tool boundaries."""

import io as _io
from unittest.mock import patch

import pytest

from wisp.transport import cli as _cli
from wisp.transport import progress as _progress


def _steering_core(calls):
    """Core whose provider demands one tool round, then answers content."""
    from wisp.core.engine import WispAgentCore
    from wisp.infra.security import PermissionMode, SecurityPolicy
    from wisp.infra.extensions import ExtensionHost

    class StatefulProvider:
        def __init__(self):
            self.call_count = 0

        def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
            self.call_count += 1
            calls.append(list(messages))
            if self.call_count == 1:
                yield {"type": "tool_call", "name": "read_file", "arguments": {"path": "a.py"}, "id": "c1"}
                yield {"type": "done"}
            else:
                yield {"type": "token", "text": "adjusted", "phase": "content"}
                yield {"type": "done"}

    return WispAgentCore(
        provider=StatefulProvider(),
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
    )


@pytest.mark.asyncio
async def test_steering_note_reaches_next_provider_round():
    calls = []
    core = _steering_core(calls)

    async def fake_execute(name, args, workspace=None, **kw):
        return ("file body", 1.0)

    drained = []
    with patch("wisp.tools.registry.execute_tool", side_effect=fake_execute):
        events = []
        async for ev in core.turn(
            {"id": "s1", "messages": [], "model": "qwen", "workspace": "/tmp"},
            "read a.py",
            steering_drain=lambda: (drained.append(1) or ["focus on the auth part"]),
        ):
            events.append(ev)

    assert drained, "boundary drain never fired"
    assert "steering_inject" in [e.get("type") for e in events]

    # The note must be visible to the SECOND provider round-trip.
    assert len(calls) >= 2
    steering_msgs = [
        m["content"] for m in calls[1]
        if m["role"] == "user" and m.get("content") == "[steering] focus on the auth part"
    ]
    assert steering_msgs, f"steering missing from round 2: {calls[1]!r}"


@pytest.mark.asyncio
async def test_no_boundary_no_drain():
    from wisp.core.engine import WispAgentCore
    from wisp.infra.security import PermissionMode, SecurityPolicy
    from wisp.infra.extensions import ExtensionHost

    class PlainProvider:
        def generate_stream_events(self, system_prompt, messages, tools=None, checkpoint_every=50):
            yield {"type": "token", "text": "hi", "phase": "content"}
            yield {"type": "done"}

    core = WispAgentCore(
        provider=PlainProvider(),
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
    )
    calls = {"n": 0}

    def drain():
        calls["n"] += 1
        return []

    events = [e async for e in core.turn(
        {"id": "s1", "messages": [], "model": "qwen", "workspace": "/tmp"},
        "just answer",
        steering_drain=drain,
    )]

    assert calls["n"] == 0, "drain fired without any tool boundary"
    assert not [e for e in events if e.get("type") == "steering_inject"]


class TestRuntimeSteeringInbox:
    def test_inject_then_drain_roundtrip(self):
        from wisp.core.runtime import AgentRuntime
        rt = object.__new__(AgentRuntime)
        rt._steering_inbox = {}
        rt.inject_steering("s1", "  focus on auth ")
        rt.inject_steering("s1", "")
        assert rt.drain_steering("s1") == ["focus on auth"]
        assert rt.drain_steering("s1") == []
        assert rt.drain_steering("other") == []

    def test_clear_steering(self):
        from wisp.core.runtime import AgentRuntime
        rt = object.__new__(AgentRuntime)
        rt._steering_inbox = {}
        rt.inject_steering("s1", "x")
        rt.clear_steering("s1")
        assert rt.drain_steering("s1") == []


class TestTypeAheadOnLine:
    def test_on_line_fires_per_complete_line(self, monkeypatch):
        from wisp.transport import typeahead as ta

        seen = []
        chunks = [b"one\ntwo\n"]
        monkeypatch.setattr(ta.select, "select", lambda *a, **k: ([0], [], []))
        monkeypatch.setattr(ta.os, "read", lambda fd, n: (chunks.pop(0) if chunks else b""))

        class Tty:
            def isatty(self): return True
            def fileno(self): return 0

        monkeypatch.setattr("wisp.transport.typeahead.sys.stdin", Tty())
        buf = ta.TypeAheadBuffer(on_line=seen.append)
        buf.start()
        buf.drain(timeout=2.0)
        assert seen == ["one", "two"]

    def test_steered_lines_skip_replay_queue(self, monkeypatch):
        from wisp.transport import typeahead as ta

        chunks = [b"injected line\n"]
        monkeypatch.setattr(ta.select, "select", lambda *a, **k: ([0], [], []))
        monkeypatch.setattr(ta.os, "read", lambda fd, n: (chunks.pop(0) if chunks else b""))

        class Tty:
            def isatty(self): return True
            def fileno(self): return 0

        monkeypatch.setattr("wisp.transport.typeahead.sys.stdin", Tty())
        got = []
        buf = ta.TypeAheadBuffer(on_line=got.append)
        buf.start()
        lines, partial = buf.drain(timeout=2.0)
        assert got == ["injected line"]
        assert lines == [], "steered lines must not double-replay"


def _render(etype, data, mode):
    from wisp.terminal_width import set_output_mode as som
    old = som(mode)
    try:
        t = object.__new__(_cli.CLITransport)
        t._progress = _progress.ProgressTracker()
        t._spinner = None
        t._thinking_buffer = []
        t._content_buffer = []
        t._in_thinking = False
        t._in_content = False
        t.show_tool_output = True
        t._turn_number = 1
        t._last_block_was_tool = False
        t._phase = "understand"
        out = _io.StringIO()
        out.isatty = lambda: False
        t._render_event(out, {"type": etype, **data})
        return out.getvalue()
    finally:
        som(old)


def test_unicode_mode_renders_steer_line():
    out = _render("steering_inject", {"text": "focus on auth"}, "unicode")
    assert "steering" in out and "focus on auth" in out


def test_accessible_mode_spells_steer():
    out = _render("steering_inject", {"text": "focus on auth"}, "accessible")
    assert "[STEER]" in out and "focus on auth" in out


def test_minimal_mode_silent():
    out = _render("steering_inject", {"text": "focus on auth"}, "minimal")
    assert "focus on auth" not in out
