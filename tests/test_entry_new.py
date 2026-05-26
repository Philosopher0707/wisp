"""TDD for new entry point using CompositionRoot.

Tests the refactored entry point pattern.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestNewEntryPoint:
    """New entry point uses CompositionRoot pattern."""

    @pytest.fixture(autouse=True)
    def _mock_event_loop(self):
        """Prevent actual event loop and transport from running in tests."""
        loop = MagicMock()
        loop.run_until_complete = MagicMock()
        with patch("wisp.entry.asyncio.new_event_loop", return_value=loop), \
             patch("wisp.entry.asyncio.set_event_loop"), \
             patch("wisp.entry.CLITransport"):
            yield

    def test_entry_creates_composition_root(self):
        from wisp.entry import run_mode
        with patch("wisp.entry.CompositionRoot") as mock_root:
            mock_instance = MagicMock()
            mock_root.return_value = mock_instance
            run_mode("cli", "test prompt")
            mock_root.assert_called_once()

    def test_entry_starts_root(self):
        from wisp.entry import run_mode
        with patch("wisp.entry.CompositionRoot") as mock_root:
            mock_instance = MagicMock()
            mock_root.return_value = mock_instance
            run_mode("cli", "test prompt")
            mock_instance.start.assert_called_once()

    def test_entry_shuts_down_root(self):
        from wisp.entry import run_mode
        with patch("wisp.entry.CompositionRoot") as mock_root:
            mock_instance = MagicMock()
            mock_root.return_value = mock_instance
            run_mode("cli", "test prompt")
            mock_instance.shutdown.assert_called_once()

    def test_cli_mode_uses_cli_transport(self):
        from wisp.entry import run_mode
        with patch("wisp.entry.CompositionRoot") as mock_root:
            with patch("wisp.entry.CLITransport") as mock_transport_class:
                mock_instance = MagicMock()
                mock_root.return_value = mock_instance
                run_mode("cli", "test prompt")
                mock_transport_class.assert_called_once()

    def test_server_mode_runs_server_main(self):
        from wisp.entry import run_mode
        with patch("wisp.entry._run_server") as mock_run_server:
            run_mode("server")
            mock_run_server.assert_called_once_with()
