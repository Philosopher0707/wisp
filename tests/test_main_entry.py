"""TDD for __main__.py migration to entry.py.

Tests that __main__.py delegates to wisp.entry.run_mode.
"""

import pytest
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


class TestSubcommandHelp:
    """Every subcommand answers --help without side effects."""

    @pytest.mark.parametrize("sub", [
        "run", "repl", "tui", "skills", "config", "check", "models",
        "session", "memory", "mcp", "git", "plan", "progress", "diagnose",
        "locks", "changes", "acp", "server", "compact", "swarm", "agents",
        "bench",
    ])
    def test_help_covers_every_subcommand(self, sub, capsys):
        from wisp.__main__ import print_subcommand_help
        assert print_subcommand_help(sub) is True, sub
        out = capsys.readouterr().out
        assert "Usage" in out, sub

    def test_unknown_subcommand_returns_false(self, capsys):
        from wisp.__main__ import print_subcommand_help
        assert print_subcommand_help("definitely-not-a-command") is False

    def test_main_routes_dash_h_to_subcommand(self, monkeypatch, capsys):
        import wisp.__main__ as m
        monkeypatch.setattr("sys.argv", ["wisp", "compact", "--help"])
        m.main()  # must not run the compact handler or error on missing args
        out = capsys.readouterr().out
        assert "Usage: wisp compact" in out
