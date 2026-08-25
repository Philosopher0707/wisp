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


# ═══════════════════════════════════════════════════════════════════
# Diff rendering functionality: no phantom blanks, plain mode, titles
# ═══════════════════════════════════════════════════════════════════


class TestDiffRenderingFunctionality:
    DIFF = (
        '   1 def greet(name):\n'
        '-2     return "hello"\n'
        '+2     return f"hello, {name}!"\n'
        '   3 \n'
        '   5     greet("world")\n'
    )

    def test_pygmentized_lines_not_double_spaced(self):
        """pygments ensurenl must not inject a phantom newline per line."""
        out = render_diff_panel(self.DIFF, title="D", width=100, language="python")
        lines = out.split("\n")
        blanks = [l for l in lines if not l.strip()]
        assert not blanks, f"blank rows in diff box: {lines!r}"

    def test_plain_mode_emits_no_ansi(self):
        out = render_diff_panel(self.DIFF, title="D", width=100,
                                language="python", plain=True)
        assert "\x1b[" not in out, "plain mode must be screen-reader safe"
        assert 'return "hello"' in out
        assert "-    return" in out and "+    return" in out

    def test_long_path_title_keeps_basename_with_leader(self):
        from wisp.diff_renderer import shorten_diff_title

        deep = "/very/long/prefix/repeated/over/and/over/again/src/pkg/app.py"
        title = shorten_diff_title(deep)
        assert title.startswith("Diff — …")
        assert "app.py" in title
        assert len(title) <= 60
        # Short paths pass through untouched.
        assert shorten_diff_title("src/app.py") == "Diff — src/app.py"

    def test_minimal_mode_skips_diff_box_in_cli(self):
        from unittest.mock import patch as mpatch

        import wisp.terminal_width as TW
        from wisp.transport.cli import CLITransport
        from wisp.transport.progress import ProgressTracker

        TW.set_output_mode(TW.OutputMode.MINIMAL)
        try:
            t = CLITransport.__new__(CLITransport)
            t._stdout = None; t.config = None
            t._progress = ProgressTracker(); t._spinner = None
            t._thinking_buffer = []; t._content_buffer = []
            t._in_thinking = False; t._in_content = False
            t.show_tool_output = True; t._turn_number = 1
            t._last_block_was_tool = False; t._phase = "understand"

            result = __import__("json").dumps({
                "status": "ok",
                "data": "Edited app.py — 1 edit",
                "metadata": {
                    "path": "app.py",
                    "diff": self.DIFF,
                },
            })
            with mpatch("wisp.diff_renderer.render_diff_box",
                        side_effect=AssertionError("diff box must not render in minimal")):
                rendered = t._render_tool_result(
                    "edit_file", result, 12.0, 80)
            assert "─── Diff" not in (rendered or "")
            assert "app.py" in rendered
        finally:
            TW.set_output_mode(TW.OutputMode.UNICODE)


class TestEnvelopeAndA11yPolish:
    def test_coerce_unwraps_spawn_envelope(self):
        from wisp.transport.cli import _coerce_tool_data

        nested = __import__("json").dumps(
            {"ok": True, "summary": "found 3 caches",
             "files": ["a.py", "b.py"], "error": None}
        )
        out = _coerce_tool_data(nested)
        assert "found 3 caches" in out
        assert '"ok"' not in out and "null" not in out
        assert "a.py, b.py" in out

    def test_coerce_leaves_file_content_alone(self):
        from wisp.transport.cli import _coerce_tool_data

        content = '{"model": "llama3", "retries": 3}'
        assert _coerce_tool_data(content) == content

    def test_plain_diff_uses_bracket_label(self):
        diff = '+1 hello'
        out = render_diff_panel(diff, title="…app.py", width=80,
                                language=None, plain=True)
        assert "[Diff]" in out
        assert "───" not in out

    def test_coerce_caps_files_list_with_tail(self):
        import json as _json

        from wisp.transport.cli import _coerce_tool_data

        nested = _json.dumps({
            "ok": True,
            "summary": "scanned",
            "files": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"],
            "error": None,
        })
        out = _coerce_tool_data(nested)
        assert "a.py, b.py, c.py, d.py" in out
        assert "+2" in out
        assert '"files"' not in out

    def test_coerce_falls_back_without_summary(self):
        import json as _json

        from wisp.transport.cli import _coerce_tool_data

        # A dict that merely looks JSON-ish must pass through untouched —
        # unwrapping is only for the known spawn/fanout envelope.
        other = _json.dumps({"status": "ok", "rows": 12})
        assert _coerce_tool_data(other) == other

    def test_plain_mode_emits_zero_ansi_in_body(self):
        import re

        out = render_diff_panel(
            "-old line\n+new line", title="…app.py", width=80,
            language="python", plain=True,
        )
        ansi = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\[[0-9;]*m")
        assert not ansi.search(out), f"ANSI leaked into plain mode: {out!r}"
