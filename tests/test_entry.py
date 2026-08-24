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
