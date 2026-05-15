"""Tests for wisp.diff_renderer — terminal diff rendering with Rich."""

import pytest
from wisp.diff_renderer import (
    _strip_line_number,
    _build_diff_line,
    _build_diff_text_with_dmp,
    colorize_diff,
    render_diff_panel,
)


class TestStripLineNumber:
    def test_add_line_with_number(self):
        assert _strip_line_number("+  42 def hello():") == "+def hello():"

    def test_remove_line_with_number(self):
        assert _strip_line_number("-   1 import os") == "-import os"

    def test_context_line_with_number(self):
        assert _strip_line_number("     5     pass") == "     pass"

    def test_single_digit_line(self):
        assert _strip_line_number("+ 1 x") == "+x"

    def test_no_line_number_unchanged(self):
        assert _strip_line_number("+hello") == "+hello"
        assert _strip_line_number("-hello") == "-hello"

    def test_empty_line(self):
        assert _strip_line_number("") == ""
        assert _strip_line_number("+") == "+"

    def test_hunk_header_unchanged(self):
        assert _strip_line_number("@@ -1,5 +1,5 @@") == "@@ -1,5 +1,5 @@"

    def test_skip_marker_unchanged(self):
        assert _strip_line_number("      ...") == "      ..."


class TestBuildDiffLine:
    def test_add_line_strips_number(self):
        text = _build_diff_line("+  42 def hello():", language="python")
        rendered = str(text)
        # Should contain the code content, not the line number
        assert "def hello():" in rendered
        assert "  42" not in rendered

    def test_remove_line_strips_number(self):
        text = _build_diff_line("-   1 import os", language="python")
        rendered = str(text)
        assert "import os" in rendered
        assert "   1" not in rendered

    def test_context_line_strips_number(self):
        text = _build_diff_line("     5     pass", language="python")
        rendered = str(text)
        assert "    pass" in rendered
        assert "    5" not in rendered

    def test_hunk_header_preserved(self):
        text = _build_diff_line("@@ -10,5 +12,8 @@")
        rendered = str(text)
        assert "@@ -10,5 +12,8 @@" in rendered

    def test_no_language_no_crash(self):
        text = _build_diff_line("+  42 print('hi')")
        rendered = str(text)
        assert "print('hi')" in rendered


class TestBuildDiffTextWithDMP:
    def test_dmp_strips_line_numbers(self):
        lines = [
            "-  41 def old_func():",
            "+  41 def new_func():",
        ]
        text = _build_diff_text_with_dmp(lines, language="python")
        rendered = str(text)
        # DMP should compare "def old_func():" vs "def new_func():"
        # not "  41 def old_func():" vs "  41 def new_func():"
        assert "old_func" in rendered
        assert "new_func" in rendered
        # Line numbers should not appear in the rendered output
        assert "  41" not in rendered

    def test_no_dmp_fallback(self):
        lines = [
            "-  1 hello",
            "+  1 world",
        ]
        text = _build_diff_text_with_dmp(lines)
        rendered = str(text)
        assert "hello" in rendered
        assert "world" in rendered


class TestColorizeDiff:
    def test_empty_diff(self):
        assert colorize_diff("") == ""
        assert colorize_diff("   ") == " "  # whitespace-only line renders as context

    def test_basic_diff(self):
        diff_text = " 1 hello\n-2 world\n+2 universe\n"
        result = colorize_diff(diff_text)
        assert "hello" in result
        assert "world" in result
        assert "universe" in result


class TestRenderDiffPanel:
    def test_empty_returns_empty(self):
        assert render_diff_panel("") == ""
        assert render_diff_panel(None) == ""

    def test_truncates_long_diffs(self):
        lines = [f" {i} line{i}" for i in range(1, 100)]
        diff_text = "\n".join(lines)
        result = render_diff_panel(diff_text, max_lines=10)
        assert "more lines" in result

    def test_box_mode_false(self):
        diff_text = " 1 hello\n-2 world\n+2 universe\n"
        result = render_diff_panel(diff_text, box_mode=False)
        assert "hello" in result
        assert "world" in result
