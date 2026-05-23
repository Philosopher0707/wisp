"""TDD tests for Spinner — terminal inline progress indicator."""

import io
import pytest
from wisp.transport.spinner import Spinner
from wisp.terminal_width import OutputMode


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
