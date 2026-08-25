"""Regression tests for the REPL operation audit fixes.

Covers: /new session split-brain, /continue in single-shot mode,
/mode command interception + validation, approval [c] honest cancel,
cancellable approval stdin reads, multiline Ctrl+C contract.
"""

import asyncio
import io
import signal
from unittest.mock import MagicMock

import pytest

from wisp.entry import _run_repl, _run_single_prompt
from wisp.transport.cli import CLITransport


class _StubRuntime:
    """Runtime stub that records what run_turn actually receives."""

    def __init__(self):
        self.store = MagicMock()
        self.turn_session_ids: list[str] = []
        self.saved_sessions: list[dict] = []
        self.store.save_session.side_effect = self._record_save

    def _record_save(self, session) -> None:
        self.saved_sessions.append(session)

    async def get_or_create_session(self, session_id: str, model: str, workspace: str) -> dict:
        return {
            "id": session_id,
            "model": model,
            "workspace": workspace,
            "messages": [],
            "title": "stub",
        }

    async def run_turn(self, session, prompt, approval_handler=None):
        self.turn_session_ids.append(session["id"])
        yield {"type": "content", "text": f"echo:{prompt}"}

    def save_session(self, session):
        self.saved_sessions.append(session)


class _StubRoot:
    def __init__(self, runtime):
        self.runtime = runtime

    def bind_loop(self, loop):
        pass


class _StubConfig:
    model = "test-model"
    workspace = "/tmp"
    provider = "ollama"


def _feed_stdin(script: str, monkeypatch):
    """Route stdin through a non-tty StringIO with the given script."""
    fake = io.StringIO(script)
    monkeypatch.setattr("sys.stdin", fake)
    return fake


@pytest.fixture
def no_signal_side_effects():
    """Snapshot and restore the process SIGINT handler around the test."""
    old = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGINT, old)


# ═══════════════════════════════════════════════════════════════════
# 1. /new must swap the session the turn runner actually uses
# ═══════════════════════════════════════════════════════════════════


class TestNewSessionSync:
    def test_turns_follow_adapter_after_new(
        self, monkeypatch, capsys, no_signal_side_effects
    ):
        runtime = _StubRuntime()

        # /new generates its id from SessionDTO timestamps; pin the lookup
        # by asserting on "not the original id" rather than exact value.
        script = "first prompt\n/new\nsecond prompt\nexit\n"
        _feed_stdin(script, monkeypatch)

        transport = CLITransport(MagicMock())
        _run_repl(transport, _StubRoot(runtime), _StubConfig(), session_id="original-session")

        assert runtime.turn_session_ids[0] == "original-session", (
            "sanity: the pre-/new turn ran on the original session"
        )
        assert runtime.turn_session_ids[1] != "original-session", (
            "/new must redirect subsequent turns to the new session"
        )

    def test_exit_force_saves_live_session(
        self, monkeypatch, capsys, no_signal_side_effects
    ):
        runtime = _StubRuntime()
        script = "/new\nexit\n"
        _feed_stdin(script, monkeypatch)

        transport = CLITransport(MagicMock())
        _run_repl(transport, _StubRoot(runtime), _StubConfig(), session_id="original-session")

        saved_ids = [
            str(s.get("id")) for s in runtime.saved_sessions if isinstance(s, dict)
        ]
        assert saved_ids, "exit must persist the live session"
        assert saved_ids[-1] != "original-session", (
            "the forced exit-save must target the post-/new session"
        )

    def test_resume_hint_tracks_current_session(self, monkeypatch, capsys, no_signal_side_effects):
        runtime = _StubRuntime()
        script = "/new\nexit\n"
        _feed_stdin(script, monkeypatch)

        transport = CLITransport(MagicMock())
        _run_repl(transport, _StubRoot(runtime), _StubConfig(), session_id="original-session")

        out = capsys.readouterr().out
        assert "-S original-session" not in out.split("Resume:")[-1], (
            "resume hint must reference the live (post-/new) session id"
        )


# ═══════════════════════════════════════════════════════════════════
# 2. /multiline is intercepted cleanly — no unknown-command noise
# ═══════════════════════════════════════════════════════════════════


class TestMultilineCommand:
    def test_no_unknown_command_error_and_toggle_works(
        self, monkeypatch, capsys, no_signal_side_effects
    ):
        runtime = _StubRuntime()
        script = "/multiline\nexit\n"
        _feed_stdin(script, monkeypatch)

        transport = CLITransport(MagicMock())
        _run_repl(transport, _StubRoot(runtime), _StubConfig())

        out = capsys.readouterr().out
        assert "Unknown command" not in out
        assert "Input mode: multi" in out

    def test_invalid_mode_rejected_with_warning(
        self, monkeypatch, capsys, no_signal_side_effects
    ):
        runtime = _StubRuntime()
        script = "/multiline banana\nexit\n"
        _feed_stdin(script, monkeypatch)

        transport = CLITransport(MagicMock())
        _run_repl(transport, _StubRoot(runtime), _StubConfig())

        out = capsys.readouterr().out
        assert "Unknown mode 'banana'" in out
        assert "Input mode: banana" not in out

    def test_explicit_mode_argument_accepted(
        self, monkeypatch, capsys, no_signal_side_effects
    ):
        runtime = _StubRuntime()
        script = "/multiline single\nexit\n"
        _feed_stdin(script, monkeypatch)

        transport = CLITransport(MagicMock())
        _run_repl(transport, _StubRoot(runtime), _StubConfig())

        out = capsys.readouterr().out
        assert "Input mode: single" in out


# ═══════════════════════════════════════════════════════════════════
# 3. Single-prompt mode runs /continue follow-up turns
# ═══════════════════════════════════════════════════════════════════


class TestSinglePromptFollowUp:
    @pytest.mark.asyncio
    async def test_continue_string_becomes_a_turn(self, monkeypatch, capsys):
        runtime = _StubRuntime()
        captured_prompts: list[str] = []

        async def run_turn(session, prompt, approval_handler=None):
            captured_prompts.append(prompt)
            yield {"type": "done", "done_reason": "stop"}

        runtime.run_turn = run_turn

        import wisp.commands as commands_module

        monkeypatch.setattr(
            commands_module, "dispatch", lambda text, agent: "expanded continuation"
        )

        transport = CLITransport(MagicMock())
        await _run_single_prompt(
            transport, _StubRoot(runtime), "/continue", _StubConfig()
        )

        assert captured_prompts == ["expanded continuation"], (
            "/continue's returned prompt must be executed, not swallowed"
        )

    @pytest.mark.asyncio
    async def test_consumed_command_runs_no_turn(self, monkeypatch, capsys):
        runtime = _StubRuntime()

        async def run_turn(session, prompt, approval_handler=None):
            raise AssertionError("no turn expected for consumed commands")
            yield  # pragma: no cover

        runtime.run_turn = run_turn

        import wisp.commands as commands_module

        monkeypatch.setattr(commands_module, "dispatch", lambda text, agent: True)

        transport = CLITransport(MagicMock())
        await _run_single_prompt(
            transport, _StubRoot(runtime), "/help", _StubConfig()
        )


# ═══════════════════════════════════════════════════════════════════
# 4. Approval [c] cancels the turn for real
# ═══════════════════════════════════════════════════════════════════


class TestApprovalCancel:
    def _interactive_transport(self):
        """Transport past the non-interactive auto-deny guard."""
        from wisp.approval_state import SessionPolicy

        transport = CLITransport(MagicMock())
        transport._force_approval_mode = False
        transport._approval_state.session_policy = SessionPolicy.PROMPT
        return transport

    @pytest.mark.asyncio
    async def test_c_raises_cancelled_error(self):
        transport = self._interactive_transport()

        async def fake_answer() -> str:
            return "c"

        transport._read_approval_answer = fake_answer

        with pytest.raises(asyncio.CancelledError):
            await transport.approve({"name": "write_file", "arguments": {}})

    @pytest.mark.asyncio
    async def test_n_still_denies_without_cancel(self):
        transport = self._interactive_transport()

        async def fake_answer() -> str:
            return "n"

        transport._read_approval_answer = fake_answer

        result = await transport.approve({"name": "write_file", "arguments": {}})
        assert result is False


# ═══════════════════════════════════════════════════════════════════
# 5. Approval stdin reader cooperates with cancellation
# ═══════════════════════════════════════════════════════════════════


class TestApprovalReaderCancellation:
    def test_pre_cancelled_reader_returns_promptly(self, monkeypatch):
        import threading


        stop = threading.Event()
        stop.set()

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        monkeypatch.setattr("sys.stdin", fake_stdin)
        monkeypatch.setattr(
            "wisp.transport.cli.select.select",
            lambda *a, **k: ([], [], []),
        )

        import time
        start = time.monotonic()
        result = CLITransport._read_approval_line("Approve? ", stop)
        elapsed = time.monotonic() - start

        assert result == ""
        assert elapsed < 1.0, "cancelled reader must not park on stdin"


# ═══════════════════════════════════════════════════════════════════
# 6. Multiline Ctrl+C clears input instead of exiting
# ═══════════════════════════════════════════════════════════════════


class TestMultilineInterruptContract:
    def test_ctrl_c_returns_empty_not_exception(self, monkeypatch):
        from wisp.transport.cli import _input_multiline

        fake_stdin = MagicMock()
        fake_stdin.isatty.return_value = True
        monkeypatch.setattr("sys.stdin", fake_stdin)
        monkeypatch.setattr(
            "builtins.input", MagicMock(side_effect=KeyboardInterrupt)
        )

        result = _input_multiline("➜ ", "... ")
        assert result == "", "Ctrl+C in multiline clears input, never exits"


# ═══════════════════════════════════════════════════════════════════
# 7. Piped stdin EOF terminates the REPL (no 100%-CPU spin)
# ═══════════════════════════════════════════════════════════════════


class TestPipedEofTermination:
    def test_single_mode_eof_exits_cleanly(self, monkeypatch, capsys, no_signal_side_effects):
        runtime = _StubRuntime()
        _feed_stdin("only prompt\n", monkeypatch)  # EOF after this line

        transport = CLITransport(MagicMock())
        _run_repl(transport, _StubRoot(runtime), _StubConfig(), session_id="s")

        out = capsys.readouterr().out
        assert "Exiting" in out, "EOF must take the graceful exit path"

    def test_input_line_returns_none_at_eof(self, monkeypatch):
        import io

        from wisp.transport.cli import _input_line

        fake = io.StringIO("one\n")
        monkeypatch.setattr("sys.stdin", fake)
        assert _input_line("? ") == "one"
        assert _input_line("? ") is None, "exhausted readline must signal EOF"
        assert _input_line("? ") is None, "must stay None, never spin empty"

    def test_multiline_mode_eof_exits(self, monkeypatch, capsys, no_signal_side_effects):
        runtime = _StubRuntime()
        _feed_stdin("/multiline\n", monkeypatch)

        transport = CLITransport(MagicMock())
        _run_repl(transport, _StubRoot(runtime), _StubConfig(), session_id="s")

        out = capsys.readouterr().out
        assert "Exiting" in out, "multiline EOF must not loop forever"


# ═══════════════════════════════════════════════════════════════════
# 8. Thinking summary grammar
# ═══════════════════════════════════════════════════════════════════


class TestThinkingGrammar:
    def _flush(self, transport, buffer):
        import io as _io

        transport._thinking_buffer = buffer
        buf = _io.StringIO()
        transport._flush_thinking(buf, width=80)
        return buf.getvalue()

    def test_single_line_thinking_says_line(self):
        transport = CLITransport(MagicMock())
        out = self._flush(transport, ["just one thought"])
        assert "(1 line " in out, out
        assert "1 lines" not in out

    def test_multi_line_thinking_says_lines(self):
        transport = CLITransport(MagicMock())
        out = self._flush(transport, ["thought one\nthought two"])
        assert "2 lines" in out, out


class TestTypedAheadReplay:
    """Prompts typed while a turn runs are captured and replayed."""

    def test_typed_ahead_prompt_replays_after_turn(self, monkeypatch, no_signal_side_effects):
        from wisp.entry import _run_repl

        class FakeBuffer:
            def __init__(self):
                self.enabled = False
                self._drained = False

            def start(self):
                self.enabled = True

            def drain(self, timeout=2.0):
                if self._drained:
                    return [], ""
                self._drained = True
                return ["second prompt"], ""

        shared = FakeBuffer()
        monkeypatch.setattr("wisp.entry.TypeAheadBuffer", lambda: shared)

        runtime = _StubRuntime()
        # Capture executed prompts via run_turn yields (echo: prefix).
        executed: list[str] = []

        original_run_turn = runtime.run_turn

        async def recording_run_turn(session, prompt, approval_handler=None):
            executed.append(prompt)
            async for ev in original_run_turn(session, prompt, approval_handler):
                yield ev

        runtime.run_turn = recording_run_turn

        _feed_stdin("first prompt\n/exit\n", monkeypatch)
        _run_repl(CLITransport(runtime, _StubConfig()), _StubRoot(runtime), _StubConfig())

        assert executed == ["first prompt", "second prompt"]

    def test_no_typeahead_when_buffer_disabled(self, monkeypatch, no_signal_side_effects):
        from wisp.entry import _run_repl

        class DisabledBuffer:
            enabled = False

            def start(self):
                self.enabled = False

            def drain(self, timeout=2.0):
                return [], ""

        monkeypatch.setattr("wisp.entry.TypeAheadBuffer", DisabledBuffer)

        runtime = _StubRuntime()
        executed: list[str] = []
        original_run_turn = runtime.run_turn

        async def recording_run_turn(session, prompt, approval_handler=None):
            executed.append(prompt)
            async for ev in original_run_turn(session, prompt, approval_handler):
                yield ev

        runtime.run_turn = recording_run_turn

        _feed_stdin("only prompt\n/exit\n", monkeypatch)
        _run_repl(CLITransport(runtime, _StubConfig()), _StubRoot(runtime), _StubConfig())

        assert executed == ["only prompt"]
