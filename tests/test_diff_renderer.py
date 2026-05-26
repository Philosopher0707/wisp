"""Tests for wisp.diff_renderer — terminal diff rendering with Rich."""

from wisp.diff_renderer import (
    _parse_diff_line_parts,
    _build_diff_line,
    _build_diff_text_with_dmp,
    colorize_diff,
    render_diff_panel,
)





class TestParseDiffLineParts:
    def test_add_line_with_number(self):
        assert _parse_diff_line_parts("+  42 def hello():") == ("+", "42", "def hello():")

    def test_remove_line_with_number(self):
        assert _parse_diff_line_parts("-   1 import os") == ("-", "1", "import os")

    def test_context_line_with_number(self):
        assert _parse_diff_line_parts("     5     pass") == (" ", "5", "    pass")

    def test_single_digit_line(self):
        assert _parse_diff_line_parts("+ 1 x") == ("+", "1", "x")

    def test_no_line_number_unchanged(self):
        assert _parse_diff_line_parts("+hello") == ("+", "", "hello")
        assert _parse_diff_line_parts("-hello") == ("-", "", "hello")

    def test_empty_line(self):
        assert _parse_diff_line_parts("") == ("", "", "")
        assert _parse_diff_line_parts("+") == ("", "", "+")

    def test_hunk_header_unchanged(self):
        assert _parse_diff_line_parts("@@ -1,5 +1,5 @@") == ("", "", "@@ -1,5 +1,5 @@")

    def test_skip_marker_unchanged(self):
        assert _parse_diff_line_parts("      ...") == (" ", "", "     ...")


class TestBuildDiffLine:
    def test_add_line_contains_code(self):
        text = _build_diff_line("+  42 def hello():", language="python")
        rendered = str(text)
        # Should contain the code content
        assert "def hello():" in rendered

    def test_remove_line_contains_code(self):
        text = _build_diff_line("-   1 import os", language="python")
        rendered = str(text)
        assert "import os" in rendered

    def test_context_line_contains_code(self):
        text = _build_diff_line("     5     pass", language="python")
        rendered = str(text)
        assert "    pass" in rendered

    def test_hunk_header_preserved(self):
        text = _build_diff_line("@@ -10,5 +12,8 @@")
        rendered = str(text)
        assert "@@ -10,5 +12,8 @@" in rendered

    def test_no_language_no_crash(self):
        text = _build_diff_line("+  42 print('hi')")
        rendered = str(text)
        assert "print('hi')" in rendered


class TestBuildDiffTextWithDMP:
    def test_dmp_compares_code_content(self):
        lines = [
            "-  41 def old_func():",
            "+  41 def new_func():",
        ]
        text = _build_diff_text_with_dmp(lines, language="python")
        rendered = str(text)
        # DMP should compare "def old_func():" vs "def new_func():"
        assert "old_func" in rendered
        assert "new_func" in rendered

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
        # whitespace-only line renders as context with line number padding
        result = colorize_diff("   ")
        assert result.strip() == ""

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
