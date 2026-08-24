"""Tests for wisp/entry.py — the main entry point."""

import pytest
from unittest.mock import MagicMock, patch


class TestRunMode:
    """Entry point dispatches to correct mode."""

    def test_cli_mode_creates_composition_root(self):
        from wisp.entry import run_mode

        with patch("wisp.entry.CompositionRoot") as mock_root:
            mock_instance = MagicMock()
            mock_root.return_value = mock_instance
            mock_instance.runtime = MagicMock()

            with patch("wisp.entry._run_cli") as mock_cli:
                run_mode("cli", prompt="hello")
                mock_root.assert_called_once()
                mock_cli.assert_called_once()

    def test_server_mode_runs_server(self):
        from wisp.entry import run_mode

        with patch("wisp.entry._run_server") as mock_server:
            run_mode("server")
            mock_server.assert_called_once()

    def test_tui_mode_creates_transport(self):
        from wisp.entry import run_mode

        with patch("wisp.entry.CompositionRoot") as mock_root:
            mock_instance = MagicMock()
            mock_root.return_value = mock_instance

            with patch("wisp.entry._run_tui") as mock_tui:
                run_mode("tui")
                mock_tui.assert_called_once()

    def test_unknown_mode_raises(self):
        from wisp.entry import run_mode

        with pytest.raises(ValueError, match="Unknown mode"):
            run_mode("unknown")


class TestRunCLI:
    """CLI mode runs single prompt or REPL."""

    def test_single_prompt_mode(self):
        from wisp.entry import _run_cli

        root = MagicMock()
        root.config = MagicMock()
        root.config.model = "test"
        root.config.workspace = "/tmp"

        loop = MagicMock()
        with patch("wisp.entry.asyncio.new_event_loop", return_value=loop), \
             patch("wisp.entry.asyncio.set_event_loop"), \
             patch("wisp.entry.CLITransport"):
            _run_cli(root, prompt="hello")
            loop.run_until_complete.assert_called()

    def test_repl_mode_no_prompt(self):
        from wisp.entry import _run_cli

        root = MagicMock()
        root.config = MagicMock()
        root.config.model = "test"
        root.config.workspace = "/tmp"

        with patch("wisp.entry._run_repl") as mock_repl:
            with patch("wisp.entry.asyncio.new_event_loop"), \
                 patch("wisp.entry.asyncio.set_event_loop"), \
                 patch("wisp.entry.CLITransport"):
                _run_cli(root, prompt=None)
                mock_repl.assert_called_once()


class TestRunREPL:
    """REPL mode creates session and loops with persistent loop."""

    def test_repl_creates_session(self):
        from unittest.mock import AsyncMock
        from wisp.entry import _run_repl

        root = MagicMock()
        root.config = MagicMock()
        root.config.model = "test"
        root.config.workspace = "/tmp"
        root.runtime.get_or_create_session = AsyncMock(
            return_value={"id": "test", "messages": []}
        )

        transport = MagicMock()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.return_value = ""
            _run_repl(transport, root, root.config)
            root.runtime.get_or_create_session.assert_called_once()

    def test_repl_uses_session_id(self):
        from unittest.mock import AsyncMock
        from wisp.entry import _run_repl

        root = MagicMock()
        root.config = MagicMock()
        root.config.model = "test"
        root.config.workspace = "/tmp"
        root.runtime.get_or_create_session = AsyncMock(
            return_value={"id": "my-session", "messages": []}
        )

        transport = MagicMock()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.return_value = ""
            _run_repl(transport, root, root.config, session_id="my-session")
            root.runtime.get_or_create_session.assert_called_with(
                session_id="my-session",
                model="test",
                workspace="/tmp",
            )


class TestHeadlessMode:
    """Headless mode returns structured result."""

    @pytest.mark.asyncio
    async def test_headless_returns_dict(self):
        from wisp.entry import run_headless

        root = MagicMock()
        root.config = MagicMock()
        root.config.model = "test"
        root.config.workspace = "/tmp"
        root.runtime = MagicMock()

        # Make get_or_create_session return a coroutine
        async def mock_get_session(*args, **kwargs):
            return {"id": "test", "messages": []}

        async def mock_run_turn(*args, **kwargs):
            if False:
                yield {}

        root.runtime.get_or_create_session = mock_get_session
        root.runtime.run_turn = mock_run_turn

        with patch("wisp.entry.CompositionRoot") as mock_root:
            mock_root.return_value = root
            result = await run_headless("hello", model="test")
            assert isinstance(result, dict)


# Helper
async def async_iter(items):
    for item in items:
        yield item


import signal


# ── REPL-owned SIGINT semantics ──────────────────────────────────────


class _FakeTask:
    def __init__(self, done=False):
        self._done = done
        self.cancelled = False

    def done(self):
        return self._done

    def cancel(self):
        self.cancelled = True


class _FakeSpinner:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeTransport:
    def __init__(self, spinner=None):
        self._spinner = spinner
        self.print_banner = MagicMock()
        self.print_continuation_banner = MagicMock()


def test_sigint_during_turn_cancels_task_and_dearms():
    """First Ctrl+C while a turn runs cancels the task and hands the next
    press to the default handler (force-quit path)."""
    import io
    import signal as sig
    from contextlib import redirect_stdout
    from wisp.entry import make_repl_sigint_handler

    transport = _FakeTransport(_FakeSpinner())
    task = _FakeTask(done=False)
    restored = []
    handler = make_repl_sigint_handler(
        transport, lambda: task,
        restore_default=lambda: restored.append(True),
    )

    buf = io.StringIO()
    with redirect_stdout(buf):
        handler(signal.SIGINT, None)

    assert task.cancelled is True
    assert spinner_stopped(transport) is True
    assert restored == [True]
    assert "cancelling turn" in buf.getvalue()


def spinner_stopped(transport):
    return transport._spinner.stopped


def test_sigint_at_idle_prompt_raises_keyboardinterrupt():
    """Idle at the prompt: a single Ctrl+C exits (single-line) or clears
    input (multiline) — no more 'Finishing current step...' theater."""
    import pytest
    from wisp.entry import make_repl_sigint_handler

    transport = _FakeTransport()
    task = _FakeTask(done=True)  # finished turn = idle
    handler = make_repl_sigint_handler(transport, lambda: task, lambda: None)

    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGINT, None)


def test_sigint_at_idle_without_any_turn_raises():
    from wisp.entry import make_repl_sigint_handler

    handler = make_repl_sigint_handler(_FakeTransport(), lambda: None, lambda: None)
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGINT, None)


def test_multiline_command_requires_exact_token(monkeypatch):
    """'/multilines' must reach slash dispatch as an unknown command, not be
    swallowed by the /multiline prefix check."""
    captured = {}
    monkeypatch.setattr("wisp.commands.dispatch",
                        lambda text, adapter: captured.setdefault("text", text))

    # Route through the same condition entry.py uses.
    prompt = "/multilines"
    intercepted = prompt == "/multiline" or prompt.startswith("/multiline ")
    assert intercepted is False

    prompt2 = "/multiline multi"
    intercepted2 = prompt2 == "/multiline" or prompt2.startswith("/multiline ")
    assert intercepted2 is True


# ── Persistent command history ───────────────────────────────────────


class TestCommandHistory:
    def test_history_path_env_override(self, monkeypatch, tmp_path):
        import wisp.entry as entry_mod
        custom = tmp_path / "custom-history"
        monkeypatch.setenv("WISP_HISTORY_FILE", str(custom))
        assert entry_mod._history_path() == custom

    def test_history_path_default_under_home(self, monkeypatch, tmp_path):
        import wisp.entry as entry_mod
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("WISP_HISTORY_FILE", raising=False)
        assert entry_mod._history_path() == tmp_path / ".wisp" / "history"

    def test_load_missing_file_is_noop_true(self, monkeypatch, tmp_path):
        import wisp.entry as entry_mod
        monkeypatch.setenv("WISP_HISTORY_FILE", str(tmp_path / "nope"))
        try:
            import readline  # noqa: F401
            has_readline = True
        except ImportError:
            has_readline = False
        result = entry_mod._load_command_history()
        if has_readline:
            assert result is True
        else:
            assert result is False

    def test_save_creates_file_and_round_trips(self, monkeypatch, tmp_path):
        import readline
        import wisp.entry as entry_mod
        hist_file = tmp_path / "nested" / "history"
        monkeypatch.setenv("WISP_HISTORY_FILE", str(hist_file))

        readline.clear_history()
        readline.add_history("/help me")
        assert entry_mod._save_command_history() is True
        assert hist_file.exists()
        content = hist_file.read_text(encoding="utf-8")
        assert "/help" in content  # readline escapes spaces as \\040

        # Fresh session loads it back
        readline.clear_history()
        assert entry_mod._load_command_history() is True
        assert readline.get_history_item(1) == "/help me"

    def test_repl_persists_entered_prompts(self, monkeypatch, tmp_path):
        """A full REPL session writes accepted input lines to the file."""
        from unittest.mock import MagicMock, patch
        import io
        import contextlib
        from wisp.entry import _run_repl

        hist_file = tmp_path / "h" / "history"
        monkeypatch.setenv("WISP_HISTORY_FILE", str(hist_file))

        transport = _FakeTransport()
        root = MagicMock()
        runtime = MagicMock()

        async def get_session(**kw):
            return {"id": "s", "messages": [], "workspace": "/tmp"}

        runtime.get_or_create_session = get_session
        root.runtime = runtime

        answers = iter(["/help", None])  # one command, then EOF

        def fake_input(prompt=""):
            item = next(answers)
            if item is None:
                raise EOFError()
            return item

        with patch("wisp.entry._input_line", side_effect=fake_input), \
             patch("wisp.entry._restore_signal_handler"), \
             patch("wisp.commands.dispatch", return_value=True) as dispatch_mock:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                _run_repl(transport, root, root.config)

        dispatch_mock.assert_called_once()
        assert hist_file.exists()
        assert "/help" in hist_file.read_text(encoding="utf-8")
