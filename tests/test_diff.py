"""Tests for wisp.diff — diff computation, fuzzy matching, and edit application."""

import pytest
from wisp.diff import (
    normalize_for_fuzzy_match,
    fuzzy_find_text,
    apply_edits_to_content,
    generate_diff_string,
    parse_hunks,
    compute_edit_diff,
    apply_edit_with_diff,
    EditOp,
    DiffResult,
    EditResult,
    FuzzyMatchResult,
    DiffHunk,
    detect_line_ending,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)


# ── Unicode normalization tests ──────────────────────────────────────

class TestNormalizeForFuzzyMatch:
    def test_smart_quotes_to_straight(self):
        text = 'message = f\u201cHello\u201d'
        result = normalize_for_fuzzy_match(text)
        assert result == 'message = f"Hello"'

    def test_tabs_to_spaces(self):
        text = 'def hello():\n\tprint("world")'
        result = normalize_for_fuzzy_match(text)
        assert '\t' not in result
        assert '    print' in result

    def test_special_spaces_to_regular(self):
        text = 'line\u00a0with\u2002spaces'
        result = normalize_for_fuzzy_match(text)
        assert '\u00a0' not in result
        assert '\u2002' not in result
        assert result == 'line with spaces'

    def test_dashes_to_hyphens(self):
        text = 'a\u2013b\u2014c\u2212d'
        result = normalize_for_fuzzy_match(text)
        assert result == 'a-b-c-d'

    def test_trailing_whitespace_stripped(self):
        text = 'line with trailing   \nnext line'
        result = normalize_for_fuzzy_match(text)
        assert result == 'line with trailing\nnext line'


# ── Fuzzy find tests ─────────────────────────────────────────────────

class TestFuzzyFindText:
    def test_exact_match(self):
        content = 'def hello():\n    print("world")\n'
        result = fuzzy_find_text(content, '    print("world")')
        assert result.found is True
        assert result.used_fuzzy_match is False
        assert result.original_index == 13  # after "def hello():\n"
        assert result.original_match_length == 18

    def test_fuzzy_match_tabs_vs_spaces(self):
        content = 'def hello():\n\tprint("world")\n'
        result = fuzzy_find_text(content, '    print("world")')
        assert result.found is True
        assert result.used_fuzzy_match is True
        assert result.original_index == 13  # tab position
        assert result.original_match_length == 15  # tab (1) + print("world") (14)

    def test_fuzzy_match_smart_quotes(self):
        content = 'def greet():\n    msg = f\u201cHello\u201d\n'
        result = fuzzy_find_text(content, '    msg = f"Hello"')
        assert result.found is True
        assert result.used_fuzzy_match is True

    def test_not_found(self):
        content = 'x = 1\ny = 2\n'
        result = fuzzy_find_text(content, 'class UserModel')
        assert result.found is False

    def test_original_index_maps_back_correctly(self):
        """Verify that fuzzy match returns original indices, not normalized."""
        content = 'a\tb\tc\td\n'
        # old_text has spaces matching the expanded tab
        result = fuzzy_find_text(content, 'b    c')
        assert result.found is True
        assert result.used_fuzzy_match is True
        # The match should map back to original index 2 (the 'b' after tab at index 1)
        assert result.original_index == 2
        # Match length should span from 'b' to after 'c' in original
        assert result.original_match_length == 3  # 'b', '\t', 'c'


# ── Edit application tests ───────────────────────────────────────────

class TestApplyEditsToContent:
    def test_exact_edit(self):
        content = 'hello world\nfoo bar\n'
        base, new, fuzzy = apply_edits_to_content(
            content, [EditOp(old_text='hello world', new_text='hi universe')]
        )
        assert base == content
        assert new == 'hi universe\nfoo bar\n'
        assert fuzzy is False

    def test_fuzzy_edit_preserves_untouched_content(self):
        """Critical test: fuzzy edits must not corrupt untouched regions."""
        content = 'def greet():\n    msg = f\u201cHello\u201d\n    farewell = f\u201cBye\u201d\n'
        base, new, fuzzy = apply_edits_to_content(
            content,
            [EditOp(old_text='    msg = f"Hello"', new_text='    message = f"Hi"')]
        )
        # Base should be original content
        assert base == content
        # Edited line should have straight quotes
        assert 'message = f"Hi"' in new
        # Untouched line should preserve smart quotes
        assert 'farewell = f\u201cBye\u201d' in new
        assert fuzzy is True

    def test_fuzzy_edit_preserves_tabs(self):
        """Tabs in untouched regions must survive fuzzy matching."""
        content = 'def hello():\n\tprint("world")\n\treturn True\n'
        base, new, fuzzy = apply_edits_to_content(
            content,
            [EditOp(old_text='    print("world")', new_text='    print("universe")')]
        )
        assert base == content
        # Edited line gets spaces (as provided in new_text)
        assert '    print("universe")' in new
        # Untouched line keeps its tab
        assert '\treturn True' in new
        assert fuzzy is True

    def test_multiple_edits(self):
        content = 'a\nb\nc\n'
        edits = [
            EditOp(old_text='a', new_text='alpha'),
            EditOp(old_text='c', new_text='charlie'),
        ]
        base, new, fuzzy = apply_edits_to_content(content, edits)
        assert new == 'alpha\nb\ncharlie\n'

    def test_overlapping_edits_raises(self):
        content = 'hello world foo bar'
        edits = [
            EditOp(old_text='hello world', new_text='hi'),
            EditOp(old_text='world foo', new_text='xyz'),
        ]
        with pytest.raises(ValueError, match="overlap"):
            apply_edits_to_content(content, edits)

    def test_duplicate_match_raises(self):
        content = 'abc\nabc\n'
        with pytest.raises(ValueError, match="2 occurrences"):
            apply_edits_to_content(content, [EditOp(old_text='abc', new_text='xyz')])

    def test_no_change_raises(self):
        content = 'hello world\n'
        with pytest.raises(ValueError, match="No changes made"):
            apply_edits_to_content(content, [EditOp(old_text='hello world', new_text='hello world')])

    def test_empty_old_text_raises(self):
        with pytest.raises(ValueError, match="old_text must not be empty"):
            apply_edits_to_content('x', [EditOp(old_text='', new_text='y')])


# ── Diff generation tests ──────────────────────────────────────────────

class TestGenerateDiffString:
    def test_simple_addition(self):
        old = 'line1\nline2\n'
        new = 'line1\nline2\nline3\n'
        result = generate_diff_string(old, new)
        assert result.diff is not None
        assert 'line3' in result.diff
        assert result.first_changed_line == 3

    def test_simple_removal(self):
        old = 'line1\nline2\nline3\n'
        new = 'line1\nline3\n'
        result = generate_diff_string(old, new)
        assert 'line2' in result.diff
        assert result.first_changed_line == 2

    def test_no_changes(self):
        old = 'line1\nline2\n'
        new = 'line1\nline2\n'
        result = generate_diff_string(old, new)
        assert result.diff == ''
        assert result.first_changed_line is None

    def test_context_lines_around_changes(self):
        old = '\n'.join(f'line{i}' for i in range(1, 21)) + '\n'
        new = '\n'.join(f'line{i}' for i in range(1, 11)) + '\nmodified\n' + '\n'.join(f'line{i}' for i in range(12, 21)) + '\n'
        result = generate_diff_string(old, new, context_lines=3)
        # Should show context around the change but skip distant unchanged lines
        assert 'line8' in result.diff or 'line9' in result.diff  # leading context
        assert 'line13' in result.diff or 'line14' in result.diff  # trailing context
        assert '...' in result.diff  # skipped lines marker


# ── Hunk parsing tests ───────────────────────────────────────────────

class TestParseHunks:
    def test_parse_simple_diff(self):
        diff_text = (
            " 1 hello\n"
            " 2 world\n"
            "-3 old_line\n"
            "+3 new_line\n"
            " 4 foo\n"
        )
        hunks = parse_hunks(diff_text)
        assert len(hunks) == 1
        hunk = hunks[0]
        assert hunk.old_start == 1
        assert hunk.old_count == 2  # 1 removed + 1 context (parser only counts change block)
        assert hunk.new_count == 2  # 1 added + 1 context
        assert len(hunk.lines) == 5  # 2 context + 1 removed + 1 added + 1 context

    def test_parse_multiple_hunks(self):
        diff_text = (
            " 1 a\n"
            "-2 b\n"
            "+2 B\n"
            " 3 c\n"
            "      ...\n"
            " 10 x\n"
            "-11 y\n"
            "+11 Y\n"
            " 12 z\n"
        )
        hunks = parse_hunks(diff_text)
        assert len(hunks) == 2

    def test_empty_diff(self):
        assert parse_hunks("") == []
        assert parse_hunks("   \n") == []

    def test_line_numbers_parsed_correctly(self):
        diff_text = "  1   first\n+  2   second\n"
        hunks = parse_hunks(diff_text)
        assert len(hunks) == 1
        lines = hunks[0].lines
        assert lines[0]["line_num"] == 1
        assert lines[0]["content"] == "  first"
        assert lines[1]["line_num"] == 2
        assert lines[1]["content"] == "  second"


# ── Line ending tests ────────────────────────────────────────────────

class TestLineEndings:
    def test_detect_crlf(self):
        assert detect_line_ending("a\r\nb\r\nc") == "\r\n"

    def test_detect_lf(self):
        assert detect_line_ending("a\nb\nc") == "\n"

    def test_normalize_to_lf(self):
        assert normalize_to_lf("a\r\nb\r\nc") == "a\nb\nc"

    def test_restore_crlf(self):
        assert restore_line_endings("a\nb\nc", "\r\n") == "a\r\nb\r\nc"

    def test_strip_bom(self):
        bom, text = strip_bom("\ufeffhello")
        assert bom == "\ufeff"
        assert text == "hello"

    def test_no_bom(self):
        bom, text = strip_bom("hello")
        assert bom == ""
        assert text == "hello"


# ── End-to-end edit with diff tests ──────────────────────────────────

class TestEndToEndEdit:
    def test_compute_edit_diff_preview(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    print('world')\n")
        result = compute_edit_diff(
            "test.py",
            [EditOp(old_text="    print('world')", new_text="    print('universe')")],
            str(tmp_path),
        )
        assert result.success is True
        assert result.diff is not None
        assert "world" in result.diff
        assert "universe" in result.diff
        # File should NOT be modified (preview only)
        assert f.read_text() == "def hello():\n    print('world')\n"

    def test_apply_edit_with_diff(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("def hello():\n    print('world')\n")
        result = apply_edit_with_diff(
            "test.py",
            [EditOp(old_text="    print('world')", new_text="    print('universe')")],
            str(tmp_path),
        )
        assert result.success is True
        assert result.diff is not None
        assert result.edits_applied == 1
        # File SHOULD be modified
        content = f.read_text()
        assert "universe" in content
        assert "world" not in content

    def test_apply_edit_preserves_line_endings(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_bytes(b"def hello():\r\n    print('world')\r\n")
        result = apply_edit_with_diff(
            "test.py",
            [EditOp(old_text="    print('world')", new_text="    print('universe')")],
            str(tmp_path),
        )
        assert result.success is True
        # CRLF should be preserved
        assert b"\r\n" in f.read_bytes()

    def test_apply_edit_preserves_bom(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_bytes("\ufeffdef hello():\n    print('world')\n".encode("utf-8"))
        result = apply_edit_with_diff(
            "test.py",
            [EditOp(old_text="    print('world')", new_text="    print('universe')")],
            str(tmp_path),
        )
        assert result.success is True
        # BOM should be preserved
        assert f.read_bytes().startswith(b"\xef\xbb\xbf")


# ── Non-ASCII / Unicode regression tests ──────────────────────────────

class TestNonAsciiEdits:
    def test_fullwidth_numbers_match_via_fuzzy(self):
        """Fullwidth ５００ should match when searching for straight '500'."""
        content = 'timeout = ５００\n'
        base, new, fuzzy = apply_edits_to_content(
            content,
            [EditOp(old_text='timeout = 500', new_text='timeout = 1000')]
        )
        assert fuzzy is True
        assert 'timeout = ５００' not in new
        assert 'timeout = 1000' in new

    def test_smart_quotes_in_code_match_straight_quotes(self):
        """Smart quotes in file should match straight-quote old_text from LLM."""
        content = 'def greet():\n    msg = f\u201cHello\u201d\n    return msg\n'
        base, new, fuzzy = apply_edits_to_content(
            content,
            [EditOp(old_text='    msg = f"Hello"', new_text='    msg = f"Goodbye"')]
        )
        assert fuzzy is True
        assert 'msg = f\u201cHello\u201d' not in new
        assert 'msg = f"Goodbye"' in new

    def test_cjk_comment_preserved_when_editing_nearby_code(self):
        """CJK comment text should survive untouched while nearby code is edited."""
        content = 'def calc():\n    # 計算結果を返す\n    result = １\n    return result\n'
        base, new, fuzzy = apply_edits_to_content(
            content,
            [EditOp(old_text='    result = 1', new_text='    result = 42')]
        )
        assert fuzzy is True
        assert '計算結果を返す' in new, "CJK comment was corrupted"
        assert 'result = 42' in new
        assert 'result = １' not in new

    def test_nfkc_collision_is_caught(self):
        """If fullwidth 'a' would match regular 'a' and map to wrong text,
        the safety check should reject rather than silently corrupt."""
        # Two contexts where regular 'a' (U+0061) and fullwidth 'ａ' (U+FF41)
        # are both present. Searching for 'a' should NOT drift to fullwidth.
        content = (
            'alpha = "valid"\n'   # regular 'a'
            'beta = "\uff41lert"\n'  # fullwidth 'ａ' in "ａlert"
        )
        # The old_text uses regular 'a'. It should match alpha= not beta=.
        base, new, fuzzy = apply_edits_to_content(
            content,
            [EditOp(old_text='alpha = "valid"', new_text='alpha = "changed"')]
        )
        assert fuzzy is False  # exact match
        assert 'alpha = "changed"' in new
        assert '"\uff41lert"' in new  # fullwidth line untouched
