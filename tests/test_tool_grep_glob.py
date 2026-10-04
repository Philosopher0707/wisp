"""`grep` and `glob`: purpose-built search tools over the workspace, read-only and contained.

Behaviour, caps and every way they could be misused: traversal, symlink escape, binary and oversized files, junk
directories, catastrophic regexes, runaway output and a runaway search.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from wisp.tools import find as find_mod
from wisp.tools.errors import ToolError
from wisp.tools.find import tool_glob, tool_grep


def _w(path: Path, text: str, mtime: float | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


@pytest.fixture
def ws(tmp_path: Path) -> str:
    root = tmp_path / "ws"
    _w(root / "src" / "a.py", "import os\nclass Foo:\n    def bar(self):\n        return 'needle'\n", 1_000)
    _w(root / "src" / "b.py", "needle two\nNEEDLE upper\nplain\n", 3_000)
    _w(root / "src" / "pkg" / "c.txt", "nothing here\n", 2_000)
    _w(root / "README.md", "needle in the readme\n", 500)
    for junk in ("node_modules/x/index.js", ".git/config", ".venv/lib/y.py", "__pycache__/z.py", "dist/o.js"):
        _w(root / junk, "needle in junk\n")
    (root / "blob.bin").write_bytes(b"needle\x00\x01\x02 binary")
    (root / "huge.txt").write_text("needle " * 400_000, encoding="utf-8")  # about 2.8 MB
    outside = tmp_path / "outside"
    _w(outside / "secret.txt", "needle outside the workspace\n")
    os.symlink(outside, root / "linkdir")
    os.symlink(outside / "secret.txt", root / "linkfile.txt")
    return str(root)


def _lines(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln and not ln.startswith("(")]


class TestGlob:
    def test_recursive_pattern_finds_files_and_skips_junk_directories(self, ws):
        assert set(_lines(tool_glob("**/*.py", workspace=ws))) == {"src/a.py", "src/b.py"}

    def test_results_are_newest_first(self, ws):
        assert _lines(tool_glob("**/*.py", workspace=ws)) == ["src/b.py", "src/a.py"]

    def test_a_top_level_pattern_does_not_recurse(self, ws):
        assert _lines(tool_glob("*.md", workspace=ws)) == ["README.md"]

    def test_a_pattern_with_a_directory_prefix(self, ws):
        assert _lines(tool_glob("src/**/*.txt", workspace=ws)) == ["src/pkg/c.txt"]

    def test_path_narrows_the_search_and_results_stay_workspace_relative(self, ws):
        assert set(_lines(tool_glob("*.py", path="src", workspace=ws))) == {"src/a.py", "src/b.py"}

    def test_no_match_says_so(self, ws):
        assert "No files matched" in tool_glob("**/*.rs", workspace=ws)

    def test_the_cap_is_applied_and_disclosed(self, ws):
        for i in range(30):
            _w(Path(ws) / "many" / f"f{i}.log", "x")
        out = tool_glob("many/*.log", workspace=ws, max_results=10)
        assert len(_lines(out)) == 10
        assert "30" in out and "10" in out and "narrow" in out.lower()

    @pytest.mark.parametrize("pattern", ["/etc/*", "../*", "src/../../x", "a/../b"])
    def test_traversal_in_the_pattern_is_refused(self, ws, pattern):
        with pytest.raises(ToolError, match="(?i)traversal|invalid pattern"):
            tool_glob(pattern, workspace=ws)

    def test_an_empty_pattern_is_refused(self, ws):
        with pytest.raises(ToolError):
            tool_glob("  ", workspace=ws)

    def test_a_path_outside_the_workspace_is_refused(self, ws, tmp_path):
        with pytest.raises(ToolError, match="(?i)outside workspace|access denied"):
            tool_glob("*", path=str(tmp_path / "outside"), workspace=ws)

    def test_a_symlinked_directory_is_not_followed(self, ws):
        assert not any("secret" in ln for ln in _lines(tool_glob("**/*.txt", workspace=ws)))

    def test_a_path_that_is_a_file_is_an_error(self, ws):
        with pytest.raises(ToolError, match="(?i)not a directory"):
            tool_glob("*", path="README.md", workspace=ws)


class TestGrep:
    def test_content_mode_gives_path_line_and_text(self, ws):
        out = tool_grep("needle", workspace=ws)
        assert "src/a.py:4:        return 'needle'" in out
        assert "src/b.py:1:needle two" in out
        assert "README.md:1:needle in the readme" in out

    def test_it_is_case_sensitive_unless_asked(self, ws):
        assert "NEEDLE upper" not in tool_grep("needle", workspace=ws)
        assert "src/b.py:2:NEEDLE upper" in tool_grep("needle", workspace=ws, ignore_case=True)

    def test_junk_directories_are_never_searched(self, ws):
        out = tool_grep("needle", workspace=ws)
        for junk in ("node_modules", ".git/", ".venv", "__pycache__", "dist/"):
            assert junk not in out

    def test_the_glob_filter_restricts_files(self, ws):
        out = tool_grep("needle", workspace=ws, glob="*.py")
        assert "src/a.py" in out and "README.md" not in out

    def test_path_restricts_the_search(self, ws):
        assert "No matches" in tool_grep("needle", path="src/pkg", workspace=ws)

    def test_files_with_matches_mode(self, ws):
        out = tool_grep("needle", workspace=ws, output_mode="files_with_matches")
        assert _lines(out) == sorted(["README.md", "src/a.py", "src/b.py"])

    def test_count_mode(self, ws):
        out = tool_grep("needle", workspace=ws, output_mode="count")
        assert "src/b.py:1" in out and "src/a.py:1" in out

    def test_context_lines_use_a_dash_and_groups_are_separated(self, ws):
        _w(Path(ws) / "ctx.txt", "one\nneedle A\ntwo\nthree\nfour\nfive\nneedle B\nsix\n")
        out = tool_grep("needle", path="ctx.txt", workspace=ws, context=1)
        assert "ctx.txt-1-one" in out and "ctx.txt:2:needle A" in out and "ctx.txt-3-two" in out
        assert "--" in out and "ctx.txt-6-five" in out and "ctx.txt:7:needle B" in out

    def test_the_result_cap_is_applied_and_disclosed(self, ws):
        _w(Path(ws) / "lots.txt", "needle\n" * 500)
        out = tool_grep("needle", path="lots.txt", workspace=ws, max_results=25)
        assert len([ln for ln in out.splitlines() if ln.startswith("lots.txt:")]) == 25
        assert "truncated" in out.lower()

    def test_binary_and_oversized_files_are_skipped_and_counted(self, ws):
        out = tool_grep("needle", workspace=ws)
        assert "blob.bin" not in out and "huge.txt" not in out
        assert "skipped 2" in out

    def test_an_invalid_regex_is_a_clear_error(self, ws):
        with pytest.raises(ToolError, match="(?i)invalid regular expression"):
            tool_grep("(unclosed", workspace=ws)

    def test_an_empty_pattern_is_refused(self, ws):
        with pytest.raises(ToolError):
            tool_grep("", workspace=ws)

    def test_an_overlong_pattern_is_refused(self, ws):
        with pytest.raises(ToolError, match="(?i)too long"):
            tool_grep("a" * 501, workspace=ws)

    @pytest.mark.parametrize("pattern", [r"(a+)+$", r"(.*)*x", r"(a|aa)+$", r"(\w+\s?)*$", r"([a-z]+)*b"])
    def test_catastrophic_shapes_are_refused_before_any_matching(self, ws, pattern):
        with pytest.raises(ToolError, match="(?i)nested|backtrack|catastrophic"):
            tool_grep(pattern, workspace=ws)

    @pytest.mark.parametrize("pattern", [r"(ab)+", r"a+b+", r"(\d+)-(\d+)", r"def \w+\(", r"^\s*class\b", r"[A-Z][a-z]+"])
    def test_ordinary_patterns_are_accepted(self, ws, pattern):
        tool_grep(pattern, workspace=ws)

    def test_a_polynomial_pattern_over_many_lines_ends_at_the_budget_not_never(self, ws, monkeypatch):
        """`.*a.*b.*c` is not nested, so the shape check cannot refuse it, yet it is cubic in the line length:
        measured at 2,000 characters it takes over 3 s per line. With a 500-character window and a budget checked on
        every line, the search ends near the budget and says so."""
        _w(Path(ws) / "nasty.txt", ("ab" * 250 + "\n") * 400)
        monkeypatch.setattr(find_mod, "_SEARCH_BUDGET_S", 1.0)
        t0 = time.monotonic()
        out = tool_grep(r".*a.*b.*c", path="nasty.txt", workspace=ws)
        assert time.monotonic() - t0 < 5.0
        assert "time budget" in out.lower()

    def test_a_long_line_is_truncated_and_only_its_head_is_matched(self, ws):
        _w(Path(ws) / "wide.txt", "needle " + "x" * 10_000 + " tail-needle\n")
        out = tool_grep("needle", path="wide.txt", workspace=ws)
        assert out.count("needle") == 1 and "…" in out
        assert len(out) < 1_000

    def test_a_symlinked_directory_and_symlinked_files_are_never_read(self, ws):
        out = tool_grep("needle", workspace=ws)
        assert "outside the workspace" not in out and "linkdir" not in out and "linkfile" not in out

    def test_a_path_outside_the_workspace_is_refused(self, ws, tmp_path):
        with pytest.raises(ToolError, match="(?i)outside workspace|access denied"):
            tool_grep("needle", path=str(tmp_path / "outside"), workspace=ws)

    def test_a_path_through_a_symlink_to_outside_is_refused(self, ws):
        with pytest.raises(ToolError, match="(?i)outside workspace|access denied"):
            tool_grep("needle", path="linkdir", workspace=ws)

    def test_a_runaway_search_stops_at_its_time_budget_with_partial_results(self, ws, monkeypatch):
        monkeypatch.setattr(find_mod, "_SEARCH_BUDGET_S", 0.0)
        out = tool_grep("needle", workspace=ws)
        assert "time budget" in out.lower()

    def test_unicode_is_searched(self, ws):
        _w(Path(ws) / "u.txt", "héllo wörld — needle ✓\n")
        assert "needle ✓" in tool_grep("wörld", path="u.txt", workspace=ws)

    def test_a_bad_output_mode_is_refused(self, ws):
        with pytest.raises(ToolError, match="(?i)output_mode"):
            tool_grep("needle", workspace=ws, output_mode="everything")

    def test_a_single_file_path_searches_just_that_file(self, ws):
        out = tool_grep("needle", path="src/a.py", workspace=ws)
        assert "src/a.py:4" in out and "src/b.py" not in out
