"""TDD tests for Spinner — terminal inline progress indicator."""

import io
import shutil

from wisp.transport.spinner import Spinner
from wisp.terminal_width import OutputMode, display_width


class TestSpinnerFrames:
    """Frame selection per output mode."""

    def test_unicode_uses_braille(self):
        s = Spinner(io.StringIO(), OutputMode.UNICODE)
        assert len(s._frames) > 1
        assert "⠁" not in s._frames  # first frame should not be in middle

    def test_ascii_uses_text(self):
        s = Spinner(io.StringIO(), OutputMode.ASCII)
        assert s._frames == ["|", "/", "-", "\\"]

    def test_accessible_uses_text(self):
        s = Spinner(io.StringIO(), OutputMode.ACCESSIBLE)
        assert s._frames == ["[busy]"]

    def test_minimal_uses_empty(self):
        s = Spinner(io.StringIO(), OutputMode.MINIMAL)
        assert s._frames == [""]


class TestSpinnerStart:
    """start() writes initial frame."""

    def test_start_writes_label(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("loading...")
        output = out.getvalue()
        assert "|" in output
        assert "loading..." in output

    def test_start_tracks_active(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("working")
        assert s._active is True
        assert s._current_label == "working"


class TestSpinnerUpdate:
    """update() changes label while animation thread cycles frames."""

    def test_update_changes_label(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("work")
        s.update("new work")
        # Animation thread writes frames; label should update
        # Give thread time to write at least one frame with new label
        import time
        time.sleep(0.2)
        s.stop()
        output = out.getvalue()
        assert "new work" in output

    def test_update_only_when_active(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.update("nope")
        assert out.getvalue() == ""


class TestSpinnerSucceed:
    """succeed() replaces spinner with checkmark."""

    def test_succeed_replaces_with_check(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.UNICODE)
        s.start("testing")
        s.succeed("testing done")
        output = out.getvalue()
        assert "✓" in output
        assert "testing done" in output

    def test_succeed_ascii_uses_ok(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("work")
        s.succeed("work done")
        assert "[OK]" in out.getvalue()

    def test_succeed_deactivates(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("x")
        s.succeed("x done")
        assert s._active is False

    def test_succeed_writes_newline(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("x")
        s.succeed("x done")
        assert out.getvalue().endswith("\n")

    def test_succeed_minimal_clears_line_and_ends_it(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.MINIMAL)
        s.start("work")
        s.succeed("work done")
        output = out.getvalue()
        assert output.endswith("\n")
        assert "\033[K" in output


class TestSpinnerFail:
    """fail() replaces spinner with X mark."""

    def test_fail_replaces_with_x(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.UNICODE)
        s.start("testing")
        s.fail("testing failed")
        output = out.getvalue()
        assert "✗" in output

    def test_fail_ascii_uses_x(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("work")
        s.fail("work failed")
        assert "[X]" in out.getvalue()

    def test_fail_deactivates(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("x")
        s.fail("x failed")
        assert s._active is False

    def test_fail_minimal_clears_line_and_ends_it(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.MINIMAL)
        s.start("work")
        s.fail("work failed")
        output = out.getvalue()
        assert output.endswith("\n")
        assert "\033[K" in output


class TestSpinnerStop:
    """stop() clears the spinner line."""

    def test_stop_clears_line(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("working")
        s.stop()
        output = out.getvalue()
        assert "\r" in output
        assert s._active is False

    def test_stop_idempotent(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.stop()
        s.stop()
        # No exception, no extra output beyond first stop
        assert s._active is False

    def test_stop_minimal_ends_line(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.MINIMAL)
        s.start("work")
        s.stop()
        output = out.getvalue()
        assert output.endswith("\n")
        assert "\033[K" in output


class TestSpinnerLongLabel:
    """Long labels must be truncated to fit terminal width.

    If a label wraps across multiple physical lines, \\r can only
    return to the start of the last line, leaking old spinner frames.
    """

    def test_long_label_truncated_in_initial_frame(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        long_label = "run_bash " + ("x" * 200)
        s.start(long_label)
        s.stop()
        output = out.getvalue()
        # Label must be truncated; should not appear in full
        assert "x" * 200 not in output
        # Truncation marker present
        assert "..." in output

    def test_wide_char_label_truncated_to_display_width(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.UNICODE)
        s.start("読" * 120)
        initial_frame = out.getvalue().split("\r")[1].removesuffix("\033[K")
        assert display_width(initial_frame) <= shutil.get_terminal_size().columns

    def test_normal_label_not_truncated(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        s.start("short label")
        s.stop()
        output = out.getvalue()
        assert "short label" in output

    def test_long_label_truncated_with_ansi_clear(self):
        out = io.StringIO()
        s = Spinner(out, OutputMode.ASCII)
        long_label = "run_bash " + ("x" * 200)
        s.start(long_label)
        s.stop()
        output = out.getvalue()
        # \\033[K clears to end of line to prevent leftover chars
        assert "\033[K" in output
