"""TDD for CLI transport — error detection, phase tracking, adapter, approval state.

The REPL loop is driven by entry._run_repl, not CLITransport.run().
Tests here validate component behavior in isolation.
"""
import io

import asyncio


from wisp.transport import cli as cli_mod
from wisp.transport.cli import CLITransport, AgentAdapter, ApprovalSessionState
from wisp.transport.progress import ProgressTracker
from wisp.core.events import AgentEvent, EventType


class _MockRuntime:
    def __init__(self):
        self.sessions = {}
        self.turns = []


# ═══════════════════════════════════════════════════════════════════
# 1. Error result detection
# ═══════════════════════════════════════════════════════════════════

class TestCLIErrorDetection:
    """_is_error_result correctly identifies error results."""

    def test_dict_error_status(self):
        assert CLITransport._is_error_result({"status": "error"}) is True

    def test_dict_non_error_status(self):
        assert CLITransport._is_error_result({"status": "ok"}) is False

    def test_string_starts_with_error(self):
        assert CLITransport._is_error_result("Error: something failed") is True

    def test_string_starts_with_bracket_error(self):
        assert CLITransport._is_error_result("[Error] bad thing") is True

    def test_json_array_is_not_error(self):
        """JSON arrays should NOT be falsely flagged as errors."""
        assert CLITransport._is_error_result('["file1.py", "file2.py"]') is False

    def test_plain_string_not_error(self):
        assert CLITransport._is_error_result("file content here") is False

    def test_number_not_error(self):
        assert CLITransport._is_error_result(42) is False


# ═══════════════════════════════════════════════════════════════════
# 2. Phase tracking
# ═══════════════════════════════════════════════════════════════════

class TestCLIPhaseTracking:
    """Phase detection and progress tracking."""

    def test_phase_starts_at_understand(self):
        tracker = ProgressTracker()
        assert tracker.progress.phase == "understand"

    def test_phase_advances_on_write_tool(self):
        """Write tools should advance phase to execute."""
        tracker = ProgressTracker()
        event = AgentEvent(type=EventType.TOOL_CALL, data={"name": "write_file", "arguments": {"path": "x.py"}})
        tracker.on_event(event)
        assert tracker.progress.phase == "execute"

    def test_tool_count_increments(self):
        tracker = ProgressTracker()
        tracker.on_event(AgentEvent(type=EventType.TOOL_CALL, data={"name": "read_file"}))
        tracker.on_event(AgentEvent(type=EventType.TOOL_RESULT, data={"name": "read_file", "duration_ms": 5.0}))
        stats = tracker.on_done()
        assert stats.get("tools_run") == 1

    def test_file_tracking(self):
        tracker = ProgressTracker()
        tracker.on_event(AgentEvent(type=EventType.TOOL_CALL, data={"name": "edit_file", "arguments": {"path": "src/main.py"}}))
        tracker.on_event(AgentEvent(type=EventType.TOOL_RESULT, data={"name": "edit_file", "result": "ok", "duration_ms": 5.0}))
        stats = tracker.on_done()
        assert "src/main.py" in stats.get("files_changed", [])

    def test_turn_number_starts_zero(self):
        transport = CLITransport(_MockRuntime())
        assert transport._turn_number == 0


# ═══════════════════════════════════════════════════════════════════
# 3. AgentAdapter
# ═══════════════════════════════════════════════════════════════════

class TestAgentAdapter:
    """AgentAdapter correctly wraps runtime+session."""

    def test_adapter_creates_session_adapter(self):
        session = {"id": "s1", "messages": []}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        assert adapter.session is not None
        assert adapter.messages == []

    def test_adapter_add_message(self):
        session = {"id": "s1", "messages": []}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        adapter._add_message("user", "hello")
        assert len(adapter.messages) == 1
        assert adapter.messages[0]["role"] == "user"
        assert adapter.messages[0]["content"] == "hello"

    def test_adapter_metrics_persists(self):
        session = {"id": "s1", "messages": []}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        m1 = adapter.metrics
        m2 = adapter.metrics
        assert m1 is m2  # Same object — not recreated each access

    def test_adapter_loop_parameter(self):
        import asyncio
        session = {"id": "s1", "messages": []}
        loop = asyncio.new_event_loop()
        try:
            adapter = AgentAdapter(_MockRuntime(), None, session, loop=loop)
            assert adapter._loop is loop
        finally:
            loop.close()

    def test_adapter_expand_continuation(self):
        session = {"id": "s1", "messages": [
            {"role": "assistant", "content": "Here is the implementation of the auth module..."},
        ]}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        expanded = adapter._expand_continuation("continue")
        assert "continue" in expanded.lower()
        assert "Context" in expanded or "context" in expanded

    def test_adapter_no_expand_non_continuation(self):
        session = {"id": "s1", "messages": []}
        adapter = AgentAdapter(_MockRuntime(), None, session)
        expanded = adapter._expand_continuation("write a function")
        assert expanded == "write a function"


# ═══════════════════════════════════════════════════════════════════
# 4. Approval session state
# ═══════════════════════════════════════════════════════════════════

class TestApprovalSessionState:
    """Approval state tracks per-tool and global approvals."""

    def test_allow_tool(self):
        state = ApprovalSessionState()
        state.allow_tool("read_file")
        assert state.is_allowed("read_file")
        assert not state.is_allowed("write_file")

    def test_deny_tool(self):
        state = ApprovalSessionState()
        state.deny_tool("run_bash")
        assert "run_bash" in state.denied_tools

    def test_auto_approve(self):
        state = ApprovalSessionState()
        state.set_auto()
        assert state.is_allowed("anything")
        assert not state.should_ask("anything")

    def test_block_all(self):
        state = ApprovalSessionState()
        state.set_block()
        assert not state.is_allowed("read_file")

    def test_should_ask_default(self):
        state = ApprovalSessionState()
        assert state.should_ask("read_file")

    def test_should_not_ask_after_allow(self):
        state = ApprovalSessionState()
        state.allow_tool("read_file")
        assert not state.should_ask("read_file")


# ═══════════════════════════════════════════════════════════════════
# 5. Approval branch Y vs y
# ═══════════════════════════════════════════════════════════════════

class TestApprovalBranches:
    """Verify Y (always-allow) and y (once) branches work correctly."""

    def test_uppercase_Y_sets_allow_tool(self):
        """Uppercase Y should register the tool as always-allowed."""
        state = ApprovalSessionState()
        raw = "Y"
        choice = raw.strip()
        if choice in ("y", "Y"):
            if choice == "Y":
                state.allow_tool("read_file")
        assert state.is_allowed("read_file")
        assert not state.should_ask("read_file")

    def test_lowercase_y_does_not_set_allow_tool(self):
        """Lowercase y should NOT register the tool as always-allowed."""
        state = ApprovalSessionState()
        raw = "y"
        choice = raw.strip()
        if choice in ("y", "Y"):
            if choice == "Y":
                state.allow_tool("read_file")
        # y doesn't set allow, so should_ask should still be True for next time
        assert state.should_ask("read_file")


# ═══════════════════════════════════════════════════════════════════
# 6. Provider status rendering (circuit breaker visibility)
# ═══════════════════════════════════════════════════════════════════

class TestProviderStatusRendering:
    """CLITransport renders provider_status events honestly."""

    def _render(self, event, mode_setup=None):
        from io import StringIO

        from wisp.terminal_width import set_output_mode

        old = None
        if mode_setup is not None:
            from wisp.terminal_width import get_output_mode
            old = get_output_mode()
            set_output_mode(mode_setup)
        try:
            transport = CLITransport(_MockRuntime())
            out = StringIO()
            transport._render_event(out, event)
            return out.getvalue()
        finally:
            if old is not None:
                set_output_mode(old)

    def test_circuit_open_renders_with_retry_horizon(self):
        out = self._render({
            "type": EventType.PROVIDER_STATUS,
            "status": "circuit_open",
            "detail": "Provider failing repeatedly.",
            "retry_after": 12.0,
        })
        assert "Provider paused" in out
        assert "12s" in out

    def test_flat_dict_event_is_normalized(self):
        """The transport accepts flat provider_status dicts from the core."""
        out = self._render({"type": "provider_status", "status": "circuit_closed"})
        assert "recovered" in out.lower()

    def test_minimal_mode_stays_silent(self):
        from wisp.terminal_width import OutputMode

        out = self._render(
            {"type": EventType.PROVIDER_STATUS, "status": "circuit_open", "retry_after": 5.0},
            mode_setup=OutputMode.MINIMAL,
        )
        assert out == ""

class TestStructuredToolResultRender:
    """Spawn/MCP results carry dict data — must render, never crash."""

    def test_dict_data_result_renders_without_split_error(self):
        transport = CLITransport(_MockRuntime())
        result = {
            "status": "ok",
            "data": {"ok": True, "summary": "extracted DEFAULTS"},
            "metadata": {},
        }
        rendered = transport._render_tool_result(
            "spawn", result, duration_ms=8100.0, width=80
        )
        assert rendered is not None
        assert "spawn" in rendered

    def test_preview_lines_coerces_non_string(self):
        from wisp.transport.cli import _preview_lines

        out = _preview_lines({"k": "v"})
        assert "k" in out


# ═══════════════════════════════════════════════════════════════════
# REPL polish: banner shows short id + tidy workspace, not raw noise
# ═══════════════════════════════════════════════════════════════════


class TestBannerPolish:
    def _transport(self):
        from wisp.transport.cli import CLITransport

        t = CLITransport.__new__(CLITransport)
        t.config = None
        return t

    def test_banner_shortens_uuid_and_tildes_home(self):
        import io

        t = self._transport()
        out = io.StringIO()
        t.print_banner(out, {
            "id": "17052f74-2824-465a-a81f-1e9d6921f240",
            "workspace": "/Users/philosopher/Documents/wisp",
            "messages": [],
        }, "nemotron")
        text = out.getvalue()
        assert "17052f74" in text, "short session id should display"
        assert "17052f74-2824" not in text, "full UUID is noise"
        assert "~/Documents/wisp" in text, "home should collapse to ~"

    def test_banner_keeps_hint_line(self):
        import io

        t = self._transport()
        out = io.StringIO()
        t.print_banner(out, {"id": "abc12345", "workspace": "/tmp", "messages": []}, "m")
        assert "/help" in out.getvalue()

    def test_continuation_banner_also_tidy(self):
        import io

        t = self._transport()
        out = io.StringIO()
        t.print_continuation_banner(out, {
            "id": "17052f74-2824-465a-a81f-1e9d6921f240",
            "workspace": "/Users/philosopher/Documents/wisp",
            "messages": [{"role": "user", "content": "hi"}],
            "title": "",
        }, "nemotron")
        text = out.getvalue()
        assert "17052f74-2824" not in text
        assert "~/Documents/wisp" in text


# ═══════════════════════════════════════════════════════════════════
# REPL polish: turn hygiene — flush ordering + content separation
# ═══════════════════════════════════════════════════════════════════


class TestTurnHygiene:
    def _transport(self):
        from wisp.transport.cli import CLITransport
        from wisp.transport.progress import ProgressTracker
        import wisp.terminal_width as TW

        TW.set_output_mode(TW.OutputMode.UNICODE)
        t = CLITransport.__new__(CLITransport)
        t._stdout = None
        t.config = None
        t._progress = ProgressTracker()
        t._spinner = None
        t._thinking_buffer = []
        t._content_buffer = []
        t._in_thinking = False
        t._in_content = False
        t.show_tool_output = True
        t._turn_number = 1
        return t

    def test_system_line_flushes_pending_thinking_first(self):
        import io

        t = self._transport()
        out = io.StringIO()
        t._render_event(out, {"type": "thinking", "text": "Let me reason about this."})
        t._render_event(out, {"type": "system", "level": "info", "message": "note"})
        text = out.getvalue()
        thinking_pos = text.find("Thinking")
        note_pos = text.find("note")
        assert thinking_pos != -1 and note_pos != -1
        assert thinking_pos < note_pos, (
            f"system line cut ahead of buffered thinking:\n{text!r}"
        )

    def test_content_after_tool_result_gets_blank_line(self):
        import io

        t = self._transport()
        out = io.StringIO()
        t.show_tool_output = False  # compact header line for result
        t._render_event(out, {
            "type": "tool_call", "name": "read_file",
            "arguments": {"path": "a.txt"},
        })
        t._render_event(out, {
            "type": "tool_result", "name": "read_file",
            "result": '{"status":"ok"}', "duration_ms": 8,
        })
        t._buffer_content("The answer is 4.")
        t._flush_content(out)
        text = out.getvalue()
        # Between the ✓ header line and the response body there is a gap.
        assert "\n\nThe answer is 4." in text or "\n\n  The answer is 4." in text, (
            f"no separation before response:\n{text!r}"
        )


# ═══════════════════════════════════════════════════════════════════
# 7. Tool result icons follow output mode
# ═══════════════════════════════════════════════════════════════════


class TestToolResultIconSelection:
    """Icon selection follows output mode, not a character-width probe."""

    def _render(self, result, mode_setup=None):
        from wisp.terminal_width import get_output_mode, set_output_mode

        old = None
        if mode_setup is not None:
            old = get_output_mode()
            set_output_mode(mode_setup)
        try:
            transport = CLITransport(_MockRuntime())
            return transport._render_tool_result(
                "read_file", result, duration_ms=None, width=80
            )
        finally:
            if old is not None:
                set_output_mode(old)

    def test_unicode_success_uses_checkmark(self):
        from wisp.terminal_width import OutputMode

        out = self._render('{"status": "ok"}', mode_setup=OutputMode.UNICODE)
        assert "✓" in out
        assert "[OK]" not in out

    def test_unicode_error_uses_cross(self):
        from wisp.terminal_width import OutputMode

        out = self._render('{"status": "error"}', mode_setup=OutputMode.UNICODE)
        assert "✗" in out

    def test_ascii_success_uses_ok_marker(self):
        from wisp.terminal_width import OutputMode

        out = self._render('{"status": "ok"}', mode_setup=OutputMode.ASCII)
        assert "[OK]" in out
        assert "✓" not in out

    def test_ascii_error_uses_x_marker(self):
        from wisp.terminal_width import OutputMode

        out = self._render('{"status": "error"}', mode_setup=OutputMode.ASCII)
        assert "[X]" in out
        assert "✗" not in out

    def test_minimal_success_uses_ok_marker(self):
        from wisp.terminal_width import OutputMode

        out = self._render('{"status": "ok"}', mode_setup=OutputMode.MINIMAL)
        assert "[OK]" in out
        assert "✓" not in out

    def test_accessible_success_uses_pass_marker(self):
        from wisp.terminal_width import OutputMode

        out = self._render('{"status": "ok"}', mode_setup=OutputMode.ACCESSIBLE)
        assert "[PASS]" in out


class TestApprovalArgRedaction:
    """Secrets must never appear verbatim in approval prompts."""

    def test_api_key_redacted_in_prompt(self):
        import io
        from unittest.mock import patch

        from wisp.transport.cli import CLITransport

        transport = CLITransport.__new__(CLITransport)
        transport._approval_state = ApprovalSessionState()
        transport._force_approval_mode = False
        transport._spinner = None

        async def deny():
            return "n"

        transport._read_approval_answer = deny
        buf = io.StringIO()
        # v3 stream discipline: the approval card is chrome → stderr.
        with patch.object(cli_mod.sys, "stderr", buf):
            approved = asyncio.run(transport.approve({
                "name": "run_bash",
                "arguments": {"command": "deploy", "api_key": "sk-live-999999"},
            }))
        assert approved is False
        out = buf.getvalue()
        assert "999999" not in out, f"secret leaked: {out!r}"
        assert "api_key" in out


# ═══════════════════════════════════════════════════════════════════
# REPL latency contract: no silent window between Enter and first event
# ═══════════════════════════════════════════════════════════════════


class _FakeTty(io.StringIO):
    def isatty(self):
        return True


class TestWaitClock:
    def _transport(self):
        from wisp.transport.cli import CLITransport

        t = CLITransport.__new__(CLITransport)
        t.config = None
        t._stdout = None
        return t

    def test_ticker_renders_elapsed_and_stops_cleanly(self):
        import time as _time

        out = _FakeTty()
        t = self._transport()
        t.start_wait_clock(stdout=out)
        assert getattr(t, "_wait_stop", None) is not None, "clock must run on tty"
        _time.sleep(0.6)  # let at least two ticks land
        t.stop_wait_clock(stdout=out)
        text = out.getvalue()
        assert "waiting" in text and "s" in text, f"no elapsed rendered: {text!r}"
        # After stop, one more tick must not append.
        before = len(text)
        import time as _t2
        _t2.sleep(0.4)
        assert len(out.getvalue()) == before, "ticker kept running after stop"

    def test_non_tty_stays_silent(self):
        out = io.StringIO()  # not a tty
        t = self._transport()
        t.start_wait_clock(stdout=out)
        assert getattr(t, "_wait_stop", None) is None, "piped output must not tick"
        t.stop_wait_clock(stdout=out)
        assert out.getvalue() == ""

    def test_first_event_stops_clock_before_rendering(self):

        out = _FakeTty()
        t = self._transport()
        t._stdout = out
        t._progress = __import__(
            "wisp.transport.progress", fromlist=["ProgressTracker"]
        ).ProgressTracker()
        t._spinner = None
        t._thinking_buffer = []
        t._content_buffer = []
        t._in_thinking = False
        t._in_content = False
        t.show_tool_output = True
        t._turn_number = 1
        t._last_block_was_tool = False
        t._phase = "understand"
        t.start_wait_clock(stdout=out)
        stopped = []
        real_stop = t.stop_wait_clock
        t.stop_wait_clock = lambda *a, **k: (stopped.append(1), real_stop(*a, **k))
        t._render_event(out, {"type": "content", "text": "answer"})
        t.stop_wait_clock(out)
        assert stopped, "first event must stop the wait clock"


class TestWaitClockParityAndAcceptance:
    """Design-doc gates for the latency contract (§4, principle 5)."""

    def _transport(self):
        t = CLITransport.__new__(CLITransport)
        t.config = None
        return t

    def test_ascii_mode_prints_ascii_ticker(self):
        import time as _time
        from wisp.terminal_width import set_output_mode

        old = set_output_mode("ascii")
        try:
            out = _FakeTty()
            t = self._transport()
            t.start_wait_clock(stdout=out)
            _time.sleep(0.35)
            t.stop_wait_clock(stdout=out)
            text = out.getvalue()
            assert "... waiting - " in text, text
            assert "…" not in text and "·" not in text
        finally:
            set_output_mode(old)

    def test_minimal_mode_suppresses_ticker_entirely(self):
        from wisp.terminal_width import set_output_mode

        old = set_output_mode("minimal")
        try:
            out = _FakeTty()
            t = self._transport()
            t.start_wait_clock(stdout=out)
            assert getattr(t, "_wait_stop", None) is None, (
                "minimal keeps only outcome lines — no ticker"
            )
        finally:
            set_output_mode(old)

    def test_accessible_mode_spells_waiting(self):
        import time as _time
        from wisp.terminal_width import set_output_mode

        old = set_output_mode("accessible")
        try:
            out = _FakeTty()
            t = self._transport()
            t.start_wait_clock(stdout=out)
            _time.sleep(0.3)
            t.stop_wait_clock(stdout=out)
            assert "[waiting]" in out.getvalue()
        finally:
            set_output_mode(old)

    def test_stop_is_idempotent(self):
        out = _FakeTty()
        t = self._transport()
        t.start_wait_clock(stdout=out)
        t.stop_wait_clock(stdout=out)
        t.stop_wait_clock(stdout=out)  # must not raise


# ═══════════════════════════════════════════════════════════════════
# Bounded rendering: adversarial tool outputs must render in O(lines)
# ═══════════════════════════════════════════════════════════════════


class TestBoundedToolRender:
    def _transport(self, out):
        from wisp.transport.cli import CLITransport
        from wisp.transport.progress import ProgressTracker

        t = CLITransport.__new__(CLITransport)
        t.config = None
        t._stdout = None
        t._spinner = None
        t._progress = ProgressTracker()
        t._thinking_buffer = []
        t._content_buffer = []
        t._in_thinking = False
        t._in_content = False
        t.show_tool_output = True
        t._turn_number = 1
        t._last_block_was_tool = False
        t._phase = "understand"
        return t

    def test_huge_single_line_output_stays_bounded(self):
        import io
        import time

        out = io.StringIO()
        t = self._transport(out)
        huge = '{"data": "' + "x" * (5 * 1024 * 1024) + '"}'
        ev = {"type": "tool_result", "name": "web_search", "success": True,
              "duration_ms": 5.0, "result": huge}
        t0 = time.perf_counter()
        t._render_event(out, ev)
        dt = time.perf_counter() - t0
        assert dt < 0.25, f"rendering 5MB output took {dt*1000:.0f}ms"
        text = out.getvalue()
        assert "+ more lines" in text or "more lines" in text

    def test_small_output_byte_identical_to_golden(self):
        import io

        out = io.StringIO()
        t = self._transport(out)
        ev = {"type": "tool_result", "name": "bash", "success": True,
              "duration_ms": 3.0, "result": "alpha\nbeta\ngamma"}
        t._render_event(out, ev)
        text = out.getvalue()
        assert "alpha" in text and "gamma" in text
        assert "more lines" not in text

# ═══════════════════════════════════════════════════════════════════
# Token streaming: content deltas paint live, not one block at done
# ═══════════════════════════════════════════════════════════════════


class TestTokenStreaming:
    def _transport(self, out):
        from wisp.transport.cli import CLITransport
        from wisp.transport.progress import ProgressTracker

        t = CLITransport.__new__(CLITransport)
        for k, v in dict(config=None, _content_buffer=[], _thinking_buffer=[],
                         _in_content=False, _in_thinking=False,
                         _streaming_content_live=False,
                         _last_block_was_tool=False, _stdout=out,
                         show_tool_output=True).items():
            setattr(t, k, v)
        t._progress = ProgressTracker()
        t._spinner = None
        return t

    def test_deltas_write_immediately_per_event(self):
        import io

        out = io.StringIO()
        t = self._transport(out)
        ev = {"type": "content", "text": "T cells "}
        t._render_event(out, ev)
        first = out.getvalue()
        assert "T cells" in first          # visible after ONE delta
        ev2 = {"type": "content", "text": "coordinate immunity"}
        t._render_event(out, ev2)
        assert "coordinate immunity" in out.getvalue()

    def test_boundary_flush_does_not_duplicate_streamed_text(self):
        import io

        out = io.StringIO()
        t = self._transport(out)
        t._render_event(out, {"type": "content", "text": "streamed answer"})
        before = out.getvalue()
        t._render_event(out, {"type": "tool_result", "name": "web_fetch",
                              "result": "{}"})
        after = out.getvalue()
        assert after.count("streamed answer") == 1  # no re-render at boundary
        assert len(after) > len(before)              # but the turn continued

    def test_error_recovery_path_still_renders_block(self):
        # Non-streamed leftovers (error-recovery accumulation) must still
        # render via the block renderer at flush time.
        import io

        out = io.StringIO()
        t = self._transport(out)
        t._buffer_content("recovered text")
        t._flush_content(out, width=80)
        assert "recovered text" in out.getvalue()

    def test_accessible_mode_labels_stream(self):
        import io
        from unittest.mock import patch as _patch

        out = io.StringIO()
        t = self._transport(out)
        with _patch("wisp.transport.cli.is_accessible", return_value=True):
            t._render_event(out, {"type": "content", "text": "hi"})
        assert "[Response]" in out.getvalue()

# ═══════════════════════════════════════════════════════════════════
# Stream discipline (v3 §0): prose→stdout, chrome→stderr
# ═══════════════════════════════════════════════════════════════════


class TestStreamDiscipline:
    def _transport(self, out, err):
        from wisp.transport.cli import CLITransport
        from wisp.transport.progress import ProgressTracker

        t = CLITransport.__new__(CLITransport)
        for k, v in dict(config=None, _content_buffer=[], _thinking_buffer=[],
                         _in_content=False, _in_thinking=False,
                         _streaming_content_live=False,
                         _last_block_was_tool=False,
                         show_tool_output=True, _stdout=out, _stderr=err,
                         _spinner=None).items():
            setattr(t, k, v)
        t._progress = ProgressTracker()
        return t

    def test_content_goes_to_stdout_only(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "content", "text": "the answer"}, err)
        assert "the answer" in out.getvalue()
        assert err.getvalue() == ""

    def test_system_warning_goes_to_stderr_only(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "system",
                              "message": "rate limited", "level": "warning"}, err)
        assert "rate limited" in err.getvalue()
        assert out.getvalue() == ""

    def test_subagent_lines_go_to_stderr(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "subagent", "kind": "task_started",
                              "role": "researcher", "detail": "Research T cells"}, err)
        assert "researcher" in err.getvalue()
        assert out.getvalue() == ""

    def test_error_card_goes_to_stderr(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "error",
                              "message": "boom", "recoverable": False}, err)
        assert "boom" in err.getvalue()
        assert out.getvalue() == ""

    def test_heartbeat_updates_status_row_not_new_line(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        # Start a tool so a status row exists.
        t._render_event(out, {"type": "tool_call", "name": "spawn",
                              "arguments": {"task": "x"}}, err)
        base_rows = err.getvalue().count("\r")
        t._render_event(out, {"type": "system",
                              "message": "⏳ spawn running… 5s"}, err)
        body = err.getvalue()
        # Updated via \r rewrite, never appended as a plain line.
        assert "running… 5s" in body
        assert "  ℹ ⏳" not in body and "⚠ ⏳" not in body
        assert body.count("\r") >= base_rows

    def test_rustc_error_format_when_coded(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "error", "recoverable": False,
                              "message": "web_fetch timed out after 30s",
                              "code": "E2103",
                              "context": ["https://example.com (attempt 1/2)"],
                              "hint": "raise tool_timeout"}, err)
        e = err.getvalue()
        assert "error[E2103]: web_fetch timed out after 30s" in e
        assert "→ https://example.com (attempt 1/2)" in e
        assert "help: raise tool_timeout" in e
        assert out.getvalue() == ""

    def test_uncoded_error_keeps_legacy_box(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "error", "message": "boom",
                              "recoverable": False}, err)
        assert "Error" in err.getvalue()      # boxed legacy card

class TestMarkdownStreaming:
    """Block constructs style at line completion; prose stays instant."""

    def _transport(self, out, err=None):
        from wisp.transport.cli import CLITransport
        from wisp.transport.progress import ProgressTracker
        t = CLITransport.__new__(CLITransport)
        for k, v in dict(config=None, _content_buffer=[], _thinking_buffer=[],
                         _in_content=False, _in_thinking=False,
                         _streaming_content_live=False,
                         _last_block_was_tool=False,
                         show_tool_output=True, _stdout=out, _stderr=err or out,
                         _spinner=None, _md_hold="", _md_fence_open=False,
                         _md_fence_body=[], _warn_counts={}).items():
            setattr(t, k, v)
        t._progress = ProgressTracker()
        return t

    def test_prose_paints_without_delay(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "content", "text": "T cells "}, err)
        assert "T cells" in out.getvalue()   # no buffering for prose

    def test_heading_styles_at_newline(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "content", "text": "## Summary"}, err)
        t._render_event(out, {"type": "content", "text": "\n"}, err)
        body = out.getvalue()
        assert "##" not in body and "Summary" in body

    def test_partial_heading_held_then_styled(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "content", "text": "## Ti"}, err)
        assert "##" not in out.getvalue()     # held, not painted raw
        t._render_event(out, {"type": "content", "text": "tle\n"}, err)
        assert "Title" in out.getvalue()

    def test_done_flushes_held_text(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = self._transport(out, err)
        t._render_event(out, {"type": "content", "text": "- item"}, err)
        t._render_event(out, {"type": "done", "session_id": "s"}, err)
        assert "item" in out.getvalue()


class TestWarningDedup:
    def test_duplicate_warnings_collapse(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = TestMarkdownStreaming()._transport(out, err)
        msg = {"type": "system", "message": "rate limited", "level": "warning"}
        t._render_event(out, dict(msg), err)              # 1st: shown
        t._render_event(out, dict(msg), err)              # 2nd: silent
        t._render_event(out, dict(msg), err)              # 3rd: ×3 collapse
        body = err.getvalue()
        assert body.count("rate limited") == 2            # shown + collapse line

    def test_counts_reset_each_turn(self):
        import io
        out, err = io.StringIO(), io.StringIO()
        t = TestMarkdownStreaming()._transport(out, err)
        msg = {"type": "system", "message": "blip", "level": "warning"}
        for _ in range(3):
            t._render_event(out, dict(msg), err)
        t._render_event(out, {"type": "done", "session_id": "s"}, err)
        t._render_event(out, dict(msg), err)
        assert err.getvalue().count("blip") == 3          # fresh turn → shown again

