"""TDD for __main__.py migration to entry.py.

Tests that __main__.py delegates to wisp.entry.run_mode.
"""

from unittest.mock import patch


class TestMainDelegatesToEntry:
    """main() delegates run/repl/server/tui to entry.run_mode."""

    def test_run_command_delegates_to_entry(self):
        with patch("wisp.entry.run_mode") as mock_run_mode:
            from wisp.__main__ import main
            import sys
            with patch.object(sys, "argv", ["wisp", "run", "hello"]):
                main()
            mock_run_mode.assert_called_once()
            args = mock_run_mode.call_args
            assert args[0][0] == "cli"
            assert args[1]["prompt"] == "hello"

    def test_repl_command_delegates_to_entry(self):
        with patch("wisp.entry.run_mode") as mock_run_mode:
            from wisp.__main__ import main
            import sys
            with patch.object(sys, "argv", ["wisp", "repl"]):
                main()
            mock_run_mode.assert_called_once()
            args = mock_run_mode.call_args
            assert args[0][0] == "cli"

    def test_server_command_delegates_to_entry(self):
        with patch("wisp.entry.run_mode") as mock_run_mode:
            from wisp.__main__ import main
            import sys
            with patch.object(sys, "argv", ["wisp", "server", "--port", "9000"]):
                main()
            mock_run_mode.assert_called_once()
            args = mock_run_mode.call_args
            assert args[0][0] == "server"
            assert args[1]["port"] == 9000

    def test_implicit_run_delegates_to_entry(self):
        with patch("wisp.entry.run_mode") as mock_run_mode:
            from wisp.__main__ import main
            import sys
            with patch.object(sys, "argv", ["wisp", "hello", "world"]):
                main()
            mock_run_mode.assert_called_once()
            args = mock_run_mode.call_args
            assert args[0][0] == "cli"
            assert args[1]["prompt"] == "hello world"

    def test_version_flag_exits_early(self):
        with patch("wisp.entry.run_mode") as mock_run_mode:
            from wisp.__main__ import main
            import sys
            with patch.object(sys, "argv", ["wisp", "--version"]):
                main()
            mock_run_mode.assert_not_called()

    def test_help_flag_exits_early(self):
        with patch("wisp.entry.run_mode") as mock_run_mode:
            from wisp.__main__ import main
            import sys
            with patch.object(sys, "argv", ["wisp", "--help"]):
                main()
            mock_run_mode.assert_not_called()
