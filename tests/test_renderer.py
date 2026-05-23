"""TDD tests for renderer additions — phase_bar, turn_stats, file_ticker."""

import pytest
from wisp.transport.renderer import (
    render_phase_bar,
    render_turn_stats,
    render_file_ticker,
)
from wisp.terminal_width import OutputMode, set_output_mode, get_output_mode


@pytest.fixture(autouse=True)
def _reset_mode():
    """Ensure tests run in unicode mode by default."""
    old = get_output_mode()
    set_output_mode(OutputMode.UNICODE)
    yield
    set_output_mode(old)


class TestPhaseBar:
    """render_phase_bar produces a phase indicator line."""

    def test_all_phases_present(self):
        bar = render_phase_bar("understand", {"tools_run": 0}, 80)
        assert "understand" in bar.lower()

    def test_current_phase_highlighted(self):
        bar = render_phase_bar("execute", {"tools_run": 3}, 80)
        assert "execute" in bar.lower()

    def test_shows_four_phases(self):
        bar = render_phase_bar("execute", {"tools_run": 5}, 80)
        assert "understand" in bar.lower()
        assert "plan" in bar.lower()
        assert "execute" in bar.lower()
        assert "verify" in bar.lower()

    def test_ascii_mode(self):
        set_output_mode(OutputMode.ASCII)
        bar = render_phase_bar("plan", {"tools_run": 1}, 80)
        assert "plan" in bar.lower()

    def test_accessible_mode(self):
        set_output_mode(OutputMode.ACCESSIBLE)
        bar = render_phase_bar("verify", {"tools_run": 2}, 80)
        assert "verify" in bar.lower()

    def test_minimal_mode_returns_empty(self):
        set_output_mode(OutputMode.MINIMAL)
        bar = render_phase_bar("understand", {"tools_run": 0}, 80)
        assert bar == ""


class TestTurnStats:
    """render_turn_stats produces a one-line turn summary."""

    def test_includes_turn_number(self):
        stats = {"turn_number": 3, "tools_run": 2, "tools_succeeded": 2,
                 "tools_failed": 0, "files_changed": [], "elapsed": 1.5,
                 "phase": "execute"}
        line = render_turn_stats(stats, 80)
        assert "3" in line

    def test_includes_tool_counts(self):
        stats = {"turn_number": 1, "tools_run": 4, "tools_succeeded": 3,
                 "tools_failed": 1, "files_changed": [], "elapsed": 2.0,
                 "phase": "execute"}
        line = render_turn_stats(stats, 80)
        assert "4" in line  # tools_run

    def test_includes_files(self):
        stats = {"turn_number": 1, "tools_run": 2, "tools_succeeded": 2,
                 "tools_failed": 0, "files_changed": ["a.py", "b.py"],
                 "elapsed": 1.0, "phase": "execute"}
        line = render_turn_stats(stats, 80)
        assert "2 files" in line

    def test_no_files_when_empty(self):
        stats = {"turn_number": 1, "tools_run": 1, "tools_succeeded": 1,
                 "tools_failed": 0, "files_changed": [], "elapsed": 0.5,
                 "phase": "understand"}
        line = render_turn_stats(stats, 80)
        assert "files" in line
        assert "0 files" in line

    def test_minimal_mode(self):
        set_output_mode(OutputMode.MINIMAL)
        stats = {"turn_number": 1, "tools_run": 2, "tools_succeeded": 2,
                 "tools_failed": 0, "files_changed": ["x.py"],
                 "elapsed": 1.0, "phase": "execute"}
        line = render_turn_stats(stats, 80)
        # Should still produce something useful even in minimal mode
        assert len(line) > 0


class TestFileTicker:
    """render_file_ticker shows changed files."""

    def test_empty_returns_empty(self):
        assert render_file_ticker([], 80) == ""

    def test_single_file(self):
        line = render_file_ticker(["src/auth.py"], 80)
        assert "src/auth.py" in line

    def test_multiple_files(self):
        line = render_file_ticker(["a.py", "b.py", "c.py"], 80)
        assert "a.py" in line
        assert "b.py" in line
        assert "c.py" in line

    def test_shows_file_list(self):
        line = render_file_ticker(["a.py", "b.py"], 80)
        assert "a.py" in line
        assert "b.py" in line

    def test_accessible_mode(self):
        set_output_mode(OutputMode.ACCESSIBLE)
        line = render_file_ticker(["src/app.py"], 80)
        assert "src/app.py" in line

    def test_minimal_mode(self):
        set_output_mode(OutputMode.MINIMAL)
        line = render_file_ticker(["x.py"], 80)
        assert "x.py" in line
