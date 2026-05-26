"""Tests for wisp.import_graph."""

from pathlib import Path


from wisp.import_graph import (
    _extract_imports_from_file,
    _resolve_import,
    build_import_graph,
    find_affected_tests,
    find_tests_for_file,
)


class TestResolveImport:
    def test_absolute_import_package(self, tmp_path: Path):
        (tmp_path / "wisp").mkdir()
        (tmp_path / "wisp" / "__init__.py").write_text("")
        result = _resolve_import(tmp_path, tmp_path / "main.py", ["wisp"], 0)
        assert result == (tmp_path / "wisp" / "__init__.py").resolve()

    def test_absolute_import_module(self, tmp_path: Path):
        (tmp_path / "wisp").mkdir()
        (tmp_path / "wisp" / "tools.py").write_text("")
        result = _resolve_import(tmp_path, tmp_path / "main.py", ["wisp", "tools"], 0)
        assert result == (tmp_path / "wisp" / "tools.py").resolve()

    def test_relative_import_sibling(self, tmp_path: Path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        (tmp_path / "pkg" / "a.py").write_text("")
        src = tmp_path / "pkg" / "b.py"
        result = _resolve_import(tmp_path, src, ["a"], 1)
        assert result == (tmp_path / "pkg" / "a.py").resolve()

    def test_relative_import_parent(self, tmp_path: Path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        (tmp_path / "root.py").write_text("")
        src = tmp_path / "pkg" / "sub.py"
        result = _resolve_import(tmp_path, src, ["root"], 2)
        assert result == (tmp_path / "root.py").resolve()

    def test_missing_import(self, tmp_path: Path):
        result = _resolve_import(tmp_path, tmp_path / "main.py", ["nonexistent"], 0)
        assert result is None


class TestExtractImports:
    def test_import(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("import os\n")
        imports = _extract_imports_from_file(f, tmp_path)
        # os is stdlib, not in workspace
        assert len(imports) == 0

    def test_from_import(self, tmp_path: Path):
        (tmp_path / "wisp").mkdir()
        (tmp_path / "wisp" / "__init__.py").write_text("")
        (tmp_path / "wisp" / "tools.py").write_text("")
        f = tmp_path / "main.py"
        f.write_text("from wisp import tools\n")
        imports = _extract_imports_from_file(f, tmp_path)
        # "from wisp import tools" loads wisp/__init__.py first
        assert (tmp_path / "wisp" / "__init__.py").resolve() in imports

    def test_from_import_module(self, tmp_path: Path):
        (tmp_path / "wisp").mkdir()
        (tmp_path / "wisp" / "__init__.py").write_text("")
        (tmp_path / "wisp" / "tools.py").write_text("")
        f = tmp_path / "main.py"
        f.write_text("from wisp.tools import read_file\n")
        imports = _extract_imports_from_file(f, tmp_path)
        assert (tmp_path / "wisp" / "tools.py").resolve() in imports

    def test_relative_import(self, tmp_path: Path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        (tmp_path / "pkg" / "utils.py").write_text("")
        f = tmp_path / "pkg" / "main.py"
        f.write_text("from . import utils\n")
        imports = _extract_imports_from_file(f, tmp_path)
        # "from . import utils" resolves the package first
        assert (tmp_path / "pkg" / "__init__.py").resolve() in imports

    def test_relative_import_module(self, tmp_path: Path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "__init__.py").write_text("")
        (tmp_path / "pkg" / "utils.py").write_text("")
        f = tmp_path / "pkg" / "main.py"
        f.write_text("from .utils import helper\n")
        imports = _extract_imports_from_file(f, tmp_path)
        assert (tmp_path / "pkg" / "utils.py").resolve() in imports

    def test_syntax_error(self, tmp_path: Path):
        f = tmp_path / "bad.py"
        f.write_text("def foo(\n")
        imports = _extract_imports_from_file(f, tmp_path)
        assert imports == set()


class TestBuildImportGraph:
    def test_simple_graph(self, tmp_path: Path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("import a\n")
        graph = build_import_graph(tmp_path)
        a = (tmp_path / "a.py").resolve()
        b = (tmp_path / "b.py").resolve()
        assert graph[b] == {a}
        assert graph[a] == set()

    def test_skips_pycache(self, tmp_path: Path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "foo.py").write_text("import os\n")
        (tmp_path / "main.py").write_text("")
        graph = build_import_graph(tmp_path)
        assert len(graph) == 1

    def test_skips_hidden_dirs(self, tmp_path: Path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / ".hidden" / "foo.py").write_text("import os\n")
        (tmp_path / "main.py").write_text("")
        graph = build_import_graph(tmp_path)
        assert len(graph) == 1


class TestFindAffectedTests:
    def test_direct_import(self, tmp_path: Path):
        (tmp_path / "src.py").write_text("")
        (tmp_path / "test_src.py").write_text("import src\n")
        graph = build_import_graph(tmp_path)
        affected = find_affected_tests([str(tmp_path / "src.py")], graph)
        assert len(affected) == 1
        assert affected[0].name == "test_src.py"

    def test_transitive_import(self, tmp_path: Path):
        (tmp_path / "core.py").write_text("")
        (tmp_path / "utils.py").write_text("import core\n")
        (tmp_path / "test_utils.py").write_text("import utils\n")
        graph = build_import_graph(tmp_path)
        affected = find_affected_tests([str(tmp_path / "core.py")], graph)
        # Only test files are returned; utils.py is filtered out
        assert len(affected) == 1
        assert affected[0].name == "test_utils.py"

    def test_no_affected_tests(self, tmp_path: Path):
        (tmp_path / "src.py").write_text("")
        graph = build_import_graph(tmp_path)
        affected = find_affected_tests([str(tmp_path / "src.py")], graph)
        assert affected == []

    def test_non_test_files_filtered(self, tmp_path: Path):
        (tmp_path / "src.py").write_text("")
        (tmp_path / "main.py").write_text("import src\n")
        graph = build_import_graph(tmp_path)
        affected = find_affected_tests([str(tmp_path / "src.py")], graph)
        assert affected == []


class TestFindTestsForFile:
    def test_single_file(self, tmp_path: Path):
        (tmp_path / "src.py").write_text("")
        (tmp_path / "test_src.py").write_text("import src\n")
        graph = build_import_graph(tmp_path)
        affected = find_tests_for_file(str(tmp_path / "src.py"), graph)
        assert len(affected) == 1
