"""Tests for wisp.import_graph."""

from pathlib import Path

import pytest

from wisp.import_graph import (
    _extract_imports_from_file,
    _resolve_import,
    build_import_graph,
    find_affected_tests,
    find_named_tests,
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


class TestANamedTestFileIsRunByTheToolOnly:
    """`run_tests(files=["tests/test_x.py"])` ran nothing and answered `0/0 passed`: only tests that IMPORT a named file counted, never the named test file. The 0/0 is
    not evidence, so a verifier that asked for exactly the test it meant to judge by could not produce any (seen with the real model, 2026-10-12). The explicit tool now
    runs it. The automatic test after a write does not: writing a test file must not start running it (that is a different change, with its own cost of up to 60 s a write)."""

    def _project(self, tmp_path: Path) -> Path:
        (tmp_path / "app.py").write_text("x = 1\n")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_app.py").write_text("import app\n\ndef test_app():\n    assert app.x == 1\n")
        return tmp_path

    def test_a_named_test_file_is_found(self, tmp_path: Path):
        (tmp_path / "src.py").write_text("")
        (tmp_path / "test_src.py").write_text("import src\n")
        graph = build_import_graph(tmp_path)
        assert [p.name for p in find_named_tests([str(tmp_path / "test_src.py")], graph)] == ["test_src.py"]

    def test_the_other_naming_convention_too(self, tmp_path: Path):
        (tmp_path / "src_test.py").write_text("")
        graph = build_import_graph(tmp_path)
        assert [p.name for p in find_named_tests([str(tmp_path / "src_test.py")], graph)] == ["src_test.py"]

    def test_it_is_listed_once(self, tmp_path: Path):
        (tmp_path / "test_src.py").write_text("")
        graph = build_import_graph(tmp_path)
        assert len(find_named_tests([str(tmp_path / "test_src.py")] * 3, graph)) == 1

    def test_only_files_of_the_workspace_graph_count(self, tmp_path: Path):
        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "src.py").write_text("")
        outside = tmp_path / "test_outside.py"
        outside.write_text("def test_x():\n    pass\n")
        graph = build_import_graph(ws)
        assert find_named_tests([str(outside), str(ws / "test_missing.py")], graph) == []

    def test_a_named_file_that_is_not_a_test_is_not_one(self, tmp_path: Path):
        (tmp_path / "src.py").write_text("")
        graph = build_import_graph(tmp_path)
        assert find_named_tests([str(tmp_path / "src.py")], graph) == []

    def test_what_a_change_affects_is_as_before(self, tmp_path: Path):
        (tmp_path / "src.py").write_text("")
        (tmp_path / "test_src.py").write_text("import src\n")
        graph = build_import_graph(tmp_path)
        assert find_affected_tests([str(tmp_path / "test_src.py")], graph) == []

    def test_the_run_tests_tool_runs_the_test_it_was_given(self, tmp_path: Path):
        from wisp.tools.tests import tool_run_tests

        report = tool_run_tests(files=["tests/test_app.py"], workspace=str(self._project(tmp_path)))
        assert report.startswith("## Test Results (1/1 passed)"), report

    def test_it_still_runs_the_tests_of_a_named_source_file(self, tmp_path: Path):
        from wisp.tools.tests import tool_run_tests

        report = tool_run_tests(files=["app.py"], workspace=str(self._project(tmp_path)))
        assert report.startswith("## Test Results (1/1 passed)"), report

    def test_the_switch_turns_it_off(self, tmp_path: Path):
        from wisp.tools.tests import tool_run_tests

        report = tool_run_tests(files=["tests/test_app.py"], workspace=str(self._project(tmp_path)), include_named_tests=False)
        assert report.startswith("## Test Results (0/0 passed)"), report

    @pytest.mark.asyncio
    async def test_writing_a_test_file_does_not_start_running_it(self, tmp_path: Path):
        """The automatic test after a write (`_run_write_verify`) keeps its old behaviour: through the real executor and the real tool."""
        from unittest.mock import MagicMock, patch

        from wisp.config import WispConfig
        from wisp.tool_executor import ToolExecutor

        ws = self._project(tmp_path)
        executor = ToolExecutor(config=WispConfig().replace(workspace=str(ws), auto_approve=True), hook_manager=MagicMock())
        with patch("wisp.tools.lsp.tool_lsp_diagnostics", return_value="✓ No issues found."):
            assert await executor._run_write_verify("tests/test_app.py", str(ws)) == ""
            assert "[Tests: ## Test Results (1/1 passed)" in await executor._run_write_verify("app.py", str(ws))  # a source file's tests still run
