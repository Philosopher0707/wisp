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

        with patch("wisp.entry.asyncio.run") as mock_run:
            with patch("wisp.entry.CLITransport") as mock_transport:
                _run_cli(root, prompt="hello")
                mock_run.assert_called()

    def test_repl_mode_no_prompt(self):
        from wisp.entry import _run_cli

        root = MagicMock()
        root.config = MagicMock()
        root.config.model = "test"
        root.config.workspace = "/tmp"

        with patch("wisp.entry._run_repl") as mock_repl:
            _run_cli(root, prompt=None)
            mock_repl.assert_called_once()


class TestRunREPL:
    """REPL mode creates session and loops."""

    def test_repl_creates_session(self):
        from wisp.entry import _run_repl

        root = MagicMock()
        root.config = MagicMock()
        root.config.model = "test"
        root.config.workspace = "/tmp"

        transport = MagicMock()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.return_value = ""
            with patch("wisp.entry.asyncio.run") as mock_run:
                mock_run.return_value = {"id": "test", "messages": []}
                _run_repl(transport, root, root.config)
                root.runtime.get_or_create_session.assert_called_once()

    def test_repl_uses_session_id(self):
        from wisp.entry import _run_repl

        root = MagicMock()
        root.config = MagicMock()
        root.config.model = "test"
        root.config.workspace = "/tmp"

        transport = MagicMock()

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.readline.return_value = ""
            with patch("wisp.entry.asyncio.run") as mock_run:
                mock_run.return_value = {"id": "my-session", "messages": []}
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
