"""TDD for CLI transport — error detection, phase tracking, adapter, approval state.

The REPL loop is driven by entry._run_repl, not CLITransport.run().
Tests here validate component behavior in isolation.
"""

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
        import io

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
