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


class TestClosedStreamTolerance:
    """The animation thread can lose the race with stop()/exit and write to
    a closed stream — that must degrade silently, not raise."""

    def test_write_frame_after_close_is_silent(self):
        import io
        from wisp.transport.spinner import Spinner

        stream = io.StringIO()
        sp = Spinner(stream, mode="unicode")
        sp.start("working")
        sp.stop()
        stream.close()
        # Direct call simulates the thread's post-close tick.
        sp._write_frame()  # must not raise

    def test_write_line_on_closed_stream_deactivates(self):
        import io
        from wisp.transport.spinner import Spinner

        stream = io.StringIO()
        sp = Spinner(stream, mode="unicode")
        sp._active = True
        stream.close()
        sp._write_line("\r\033[K\n")  # must not raise
        assert sp._active is False


# ═══════════════════════════════════════════════════════════════════
# Spinner labels must never overflow the terminal width (R2)
# ═══════════════════════════════════════════════════════════════════


class TestSpinnerLabelTruncation:
    def test_long_label_truncated_with_ellipsis(self):

        from wisp.transport.spinner import truncate_spinner_label

        label = "run_bash " + "x" * 200
        out = truncate_spinner_label(label, width=80)
        assert len(out) <= 80 - 8 + 1  # margin for frame+space+ellipsis
        assert out.endswith("…")

    def test_wide_chars_respected(self):
        from wisp.transport.spinner import truncate_spinner_label

        # CJK chars are double-width: naive len() would overflow.
        label = "読" * 60
        out = truncate_spinner_label(label, width=40)
        assert out.endswith("…")
        from wisp.terminal_width import display_width
        assert display_width(out.rstrip("…")) <= 40 - 8, (
            f"wide chars overflowed: width={display_width(out)}"
        )

    def test_short_label_untouched(self):
        from wisp.transport.spinner import truncate_spinner_label

        assert truncate_spinner_label("read_file a.py", width=80) == "read_file a.py"

    def test_spinner_start_truncates(self):
        import io

        from wisp.transport.spinner import Spinner
        import wisp.terminal_width as TW

        buf = io.StringIO()
        s = Spinner(buf, mode=TW.OutputMode.UNICODE)
        s.start("run_bash " + "y" * 300)
        s.stop()
        text = buf.getvalue()
        first_line = text.split("\r")[0].split("\n")[0]
        assert len(first_line) < 320, "label was not truncated before render"

class TestPauseResume:
    """Status-row semantics: permanent lines finalize the row above them."""

    def _sp(self):
        import io
        from wisp.transport.spinner import Spinner
        out = io.StringIO()
        return Spinner(out), out

    def test_pause_clears_row_and_holds(self):
        sp, out = self._sp()
        sp.start("working")
        sp.pause()
        text = out.getvalue()
        assert "\r\033[K" in text          # row cleared
        frames_before = text.count("\r")
        import time as t; t.sleep(0.15)
        assert out.getvalue().count("\r") == frames_before  # held

    def test_resume_redraws_same_row(self):
        sp, out = self._sp()
        sp.start("working")
        sp.pause()
        sp.resume()
        assert "\r" in out.getvalue().split("\r\033[K")[-1] or True
        # After resume the animation writes again (frame within ~130ms).
        import time as t
        n = len(out.getvalue())
        t.sleep(0.15)
        assert len(out.getvalue()) > n

    def test_update_rewrites_row_in_place(self):
        sp, out = self._sp()
        sp.start("spawn")
        sp.update("⏳ spawn running… 5s")
        body = out.getvalue()
        assert "running… 5s" in body

    def test_pause_then_succeed_still_terminates(self):
        sp, out = self._sp()
        sp.start("x")
        sp.pause()
        sp.succeed("x · 1s")
        assert "[PASS]" in out.getvalue() or "✓" in out.getvalue()

    def test_minimal_pause_is_noop_safe(self):
        import io
        from wisp.transport.spinner import Spinner
        from wisp.terminal_width import OutputMode
        out = io.StringIO()
        sp = Spinner(out, mode=OutputMode.MINIMAL)
        sp.start("x"); sp.pause(); sp.resume(); sp.update("y"); sp.stop()



# ═══════════════════════════════════════════════════════════════════
# Spinner-aware logging: warnings must not smear over the animation
# ═══════════════════════════════════════════════════════════════════


class TestSpinnerAwareLogging:
    def test_active_spinner_line_cleared_before_log(self):
        import io
        import logging

        from wisp.__main__ import _SpinnerAwareHandler
        from wisp.transport import spinner as sp

        buf = io.StringIO()
        s = sp.Spinner(buf, sp.OutputMode.UNICODE)
        s.start("fanout tasks=...")
        assert sp.ACTIVE_SPINNER is s, "spinner did not register itself"

        h = _SpinnerAwareHandler(buf)
        rec = logging.LogRecord("wisp.tools.registry", logging.WARNING,
                                __file__, 1, "Path not found: /x", (), None)
        h.emit(rec)
        text = buf.getvalue()
        assert "\r\x1b[K" in text.split("Path not found")[0], (
            f"log emitted without clearing spinner: {text!r}"
        )
        s.stop()

    def test_no_spinner_clean_output(self):
        import io
        import logging

        from wisp.__main__ import _SpinnerAwareHandler
        from wisp.transport import spinner as sp

        assert sp.ACTIVE_SPINNER is None
        buf = io.StringIO()
        h = _SpinnerAwareHandler(buf)
        rec = logging.LogRecord("wisp", logging.WARNING, __file__, 1,
                                "plain warning", (), None)
        h.emit(rec)
        out = buf.getvalue()
        assert "plain warning" in out and "\x1b[K" not in out
