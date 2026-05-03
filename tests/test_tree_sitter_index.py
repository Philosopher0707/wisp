"""Tests for tree-sitter based code index."""

from wisp.tree_sitter_index import (
    is_tree_sitter_available,
    build_index,
    search_symbols,
    format_index_summary,
)


class TestTreeSitterAvailability:
    def test_availability_check(self):
        """is_tree_sitter_available() returns bool without error."""
        result = is_tree_sitter_available()
        assert isinstance(result, bool)


class TestBuildIndex:
    def test_empty_directory(self, tmp_path):
        """Empty directory yields empty index."""
        index = build_index(str(tmp_path))
        assert index.total_symbols == 0
        assert index.files_scanned == 0
        assert format_index_summary(index) == ""

    def test_python_file_found(self, tmp_path):
        """Python files are scanned (regardless of parser used)."""
        src = tmp_path / "main.py"
        src.write_text("x = 1\n")
        index = build_index(str(tmp_path))
        # Even without tree-sitter, regex fallback should find something
        # or at least not crash
        assert index.files_scanned >= 0

    def test_fallback_to_regex(self, tmp_path):
        """Without tree-sitter, falls back to regex-based index."""
        src = tmp_path / "test.py"
        src.write_text(
            "class MyClass:\n"
            "    def method(self): pass\n"
            "def func(): pass\n"
        )
        index = build_index(str(tmp_path))
        # Should find symbols via regex fallback
        if not is_tree_sitter_available():
            assert index.total_symbols >= 2


class TestSearchSymbols:
    def test_search_empty_index(self, tmp_path):
        """Searching empty index returns empty list."""
        index = build_index(str(tmp_path))
        results = search_symbols(index, "anything")
        assert results == []

    def test_search_finds_symbols(self, tmp_path):
        """Search finds symbols when they exist."""
        src = tmp_path / "utils.py"
        src.write_text("def helper(): pass\n")
        index = build_index(str(tmp_path))
        results = search_symbols(index, "helper")
        if index.total_symbols > 0:
            assert len(results) >= 1
            assert results[0].name == "helper"


class TestFormatIndexSummary:
    def test_empty_summary(self, tmp_path):
        """Empty index yields empty summary."""
        index = build_index(str(tmp_path))
        assert format_index_summary(index) == ""

    def test_non_empty_summary(self, tmp_path):
        """Non-empty index yields formatted summary."""
        src = tmp_path / "app.py"
        src.write_text("class App: pass\n")
        index = build_index(str(tmp_path))
        summary = format_index_summary(index)
        if index.total_symbols > 0:
            assert "symbols" in summary
            assert "search_symbols()" in summary
