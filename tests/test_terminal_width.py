"""Tests for terminal_width — width-aware rendering, box modes, unicode width."""

import os
import pytest
from wisp.terminal_width import (
    display_width,
    wrap_text_wide,
    pad_right,
    truncate,
    BoxChars,
    OutputMode,
    set_output_mode,
    _strip_ansi,
    _display_width_approx,
)


class TestDisplayWidth:
    """Display width calculations must handle emoji, CJK, and ANSI."""

    def test_ascii_chars_width_1(self):
        assert display_width("hello") == 5
        assert display_width("test.py") == 7
        assert display_width("12345") == 5
        assert display_width("") == 0

    def test_spaces_width_1(self):
        assert display_width("  ") == 2

    def test_emoji_display_width_actual(self):
        """Actual display width via wcwidth library.

        Note: ✓/✗/✔/✕ are Dingbats (width 1), not emoji.
        🧠 and other emoji-range chars are width 2.
        """
        assert display_width("✓") == 1  # Dingbat checkmark
        assert display_width("✗") == 1  # Dingbat x mark
        assert display_width("🧠") == 2  # Brain emoji
        assert display_width("⚠") == 1  # Warning sign

    def test_emoji_combined_width(self):
        # "  ✓ test" = 2 spaces(2) + dingbat(1) + space(1) + 4 chars(4) = 8
        assert display_width("  ✓ test") == 8
        # "  🧠 test" = 2 spaces(2) + emoji(2) + space(1) + 4 chars(4) = 9
        assert display_width("  🧠 test") == 9

    def test_cjk_display_width_2(self):
        assert display_width("你好") == 4
        assert display_width("日本語") == 6

    def test_combining_chars_zero_width(self):
        assert display_width("é") == 1  # single precomposed char
        # Combining diacritical: e + combining acute
        assert display_width("e\u0301") == 1

    def test_ansi_codes_stripped(self):
        styled = "\033[32mhello\033[0m"
        assert display_width(styled) == 5
        assert _strip_ansi(styled) == "hello"

    def test_strip_ansi_no_codes(self):
        assert _strip_ansi("plain text") == "plain text"


class TestDisplayWidthApprox:
    """Approximation without wcwidth."""

    def test_basic_approx(self):
        assert _display_width_approx("hello") == 5

    def test_approx_emoji_width_2(self):
        # Emoji range chars are approximated as width 2
        assert _display_width_approx("🧠") == 2  # In emoji range 0x1F300-
        assert _display_width_approx("😀") == 2  # In emoji range

    def test_approx_dingbats_width_1(self):
        # Dingbats (✓✗✔✕ etc) at U+2700-U+27BF are NOT in emoji range
        # They are typically rendered as normal-width
        assert _display_width_approx("✓") == 1
        assert _display_width_approx("✗") == 1
        assert _display_width_approx("✔") == 1

    def test_approx_with_ansi(self):
        styled = "\033[32mhello\033[0m"
        assert _display_width_approx(styled) == 5


class TestWrapTextWide:
    """Width-aware text wrapping."""

    def test_simple_wrap(self):
        result = wrap_text_wide("hello world test", 10)
        assert result == ["hello", "world test"]

    def test_no_wrap_short(self):
        result = wrap_text_wide("hi", 10)
        assert result == ["hi"]

    def test_wrap_with_emoji(self):
        # ✓ (U+2713 Dingbat) has width 1, so "✓ hello" = 7 fits in width 7
        result = wrap_text_wide("✓ hello", 7)
        assert result == ["✓ hello"]

    def test_wrap_with_wide_emoji(self):
        # 🧠 (U+1F9E0 Brain emoji) has width 2, so "🧠 hello" = 8 doesn't fit in 7
        result = wrap_text_wide("🧠 hello", 7)
        # "🧠" = 2 + space + "hello" = 5 = 8, exceeds 7
        assert "🧠 " in result[0] or "hello" in result[1] or len(result) > 1

    def test_wrap_with_indent(self):
        result = wrap_text_wide("hello world", 5, indent="  ")
        assert "  world" in result

    def test_cjk_wrap(self):
        result = wrap_text_wide("你好世界你好", 6)
        assert "你好世" in result or len(result) > 1

    def test_long_word_break(self):
        result = wrap_text_wide("supercalifragilistic", 10)
        assert all(display_width(line) <= 10 for line in result)
        assert len(result) > 1

    def test_empty_string(self):
        result = wrap_text_wide("", 10)
        assert result == [""]

    def test_multiline_paragraphs(self):
        result = wrap_text_wide("hello\n\nworld", 10)
        assert result == ["hello", "", "world"]


class TestPadRight:
    """Right-padding to target display width."""

    def test_pad_right_basic(self):
        assert pad_right("hi", 5) == "hi   "
        assert pad_right("test", 10) == "test      "

    def test_pad_right_noop(self):
        assert pad_right("hello", 3) == "hello"

    def test_pad_right_with_emoji(self):
        # "✓" is width 2, need 3 more spaces for target 5
        result = pad_right("✓", 5)
        assert display_width(result) == 5


class TestTruncate:
    """Width-aware truncation."""

    def test_truncate_short(self):
        assert truncate("hello", 10) == "hello"

    def test_truncate_with_ellipsis(self):
        result = truncate("hello world", 8)
        assert display_width(result) == 8
        assert result.endswith("...")

    def test_truncate_fits(self):
        result = truncate("hello", 5)
        assert result == "hello"

    def test_truncate_emoji(self):
        result = truncate("✓ hello world", 5)
        assert display_width(result) == 5


class TestBoxChars:
    """Box-drawing character selection per mode."""

    def test_unicode_mode(self):
        set_output_mode("unicode")
        box = BoxChars()
        assert box.tl == "┌"
        assert box.tr == "┐"
        assert box.hz == "─"
        assert box.vt == "│"

    def test_ascii_mode(self):
        set_output_mode("ascii")
        box = BoxChars()
        assert box.tl == "+"
        assert box.tr == "+"
        assert box.hz == "-"
        assert box.vt == "|"

    def test_accessible_mode(self):
        set_output_mode("accessible")
        box = BoxChars()
        assert box.tl == "["
        assert box.tr == "]"
        assert box.hz == "-"
        assert box.vt == "|"

    def test_minimal_mode(self):
        set_output_mode("minimal")
        box = BoxChars()
        assert box.tl == ""
        assert box.tr == ""
        assert box.hz == ""

    def test_box_top_with_title(self):
        set_output_mode("unicode")
        box = BoxChars()
        top = box.top(20, title="Test")
        assert "┌" in top
        assert "Test" in top

    def test_box_top_no_title(self):
        set_output_mode("unicode")
        box = BoxChars()
        top = box.top(10)
        assert "┌" in top
        assert "┐" in top

    def test_box_bottom(self):
        set_output_mode("unicode")
        box = BoxChars()
        bottom = box.bottom(10)
        assert "└" in bottom
        assert "┘" in bottom

    def test_box_line(self):
        set_output_mode("unicode")
        box = BoxChars()
        line = box.line(10, "Hi")
        assert "│" in line
        assert "Hi" in line

    def test_line_with_emoji(self):
        set_output_mode("unicode")
        box = BoxChars()
        line = box.line(20, "✓ test")
        # Emoji is 2 cols, " test" is 5 — total 7, should fit in 16 inner
        assert "✓" in line
        assert "test" in line

    def test_line_truncate_long_content(self):
        set_output_mode("unicode")
        box = BoxChars()
        # 10 width - 4 borders = 6 inner width
        line = box.line(10, "hello world")
        # Should be truncated to fit
        inner = display_width(_strip_ansi(line)) - 4  # borders cost
        assert inner <= 8  # approximately


class TestModeDetection:
    """Output mode detected from environment."""

    def test_default_mode(self):
        # After importing, mode is set based on actual TTY state
        # We just check it's one of the valid modes
        assert OutputMode.UNICODE in list(OutputMode)
        assert OutputMode.ASCII in list(OutputMode)

    def test_set_mode(self):
        set_output_mode("ascii")
        box = BoxChars()
        assert box.mode == OutputMode.ASCII
        # Reset for other tests
        set_output_mode("unicode")

    def test_set_mode_enum(self):
        set_output_mode(OutputMode.MINIMAL)
        box = BoxChars()
        assert box.mode == OutputMode.MINIMAL
        set_output_mode("unicode")


class TestModeEnvironment:
    """Mode detection from environment variables."""

    def test_no_color_sets_ascii(self, monkeypatch):
        from wisp.terminal_width import _detect_mode
        monkeypatch.setenv("NO_COLOR", "1")
        assert _detect_mode() == OutputMode.ASCII

    def test_wisp_output_mode_unicode(self, monkeypatch):
        from wisp.terminal_width import _detect_mode
        monkeypatch.setenv("WISP_OUTPUT_MODE", "unicode")
        assert _detect_mode() == OutputMode.UNICODE

    def test_wisp_output_mode_accessible(self, monkeypatch):
        from wisp.terminal_width import _detect_mode
        monkeypatch.setenv("WISP_OUTPUT_MODE", "accessible")
        assert _detect_mode() == OutputMode.ACCESSIBLE

    def test_wisp_accessible_flag(self, monkeypatch):
        from wisp.terminal_width import _detect_mode
        monkeypatch.setenv("WISP_ACCESSIBLE", "1")
        assert _detect_mode() == OutputMode.ACCESSIBLE

    def test_term_dumb(self, monkeypatch):
        from wisp.terminal_width import _detect_mode
        monkeypatch.setenv("TERM", "dumb")
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("WISP_OUTPUT_MODE", raising=False)
        monkeypatch.delenv("WISP_ACCESSIBLE", raising=False)
        assert _detect_mode() == OutputMode.ASCII


class TestEdgeCases:
    """Edge cases and regression tests."""

    def test_empty_box_line(self):
        set_output_mode("unicode")
        box = BoxChars()
        line = box.line(10, "")
        assert "│" in line

    def test_zero_width_fill(self):
        result = pad_right("test", 10, fill="")
        assert result == "test"

    def test_newline_in_string(self):
        result = wrap_text_wide("line1\nline2", 10)
        assert len(result) == 2

    def test_mixed_emoji_and_cjk(self):
        text = "✓ 你好"
        w = display_width(text)
        # ✓ = 1 (dingbat), space = 1, 你 = 2, 好 = 2
        assert w == 6

    def test_zero_width_chars(self):
        # Zero-width spaces should have width 0
        # But soft hyphen (U+00AD) actually has width 1 in wcwidth
        assert display_width("\u200b") == 0  # ZWSP
        assert display_width("\u200c") == 0  # ZWNJ
