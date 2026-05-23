"""Tests for the new tools registry — verifies _legacy.py is fully replaced."""

import pytest


class TestRegistryImports:

    def test_tool_schemas_available(self):
        from wisp.tools import TOOL_SCHEMAS
        assert isinstance(TOOL_SCHEMAS, list)
        assert len(TOOL_SCHEMAS) > 0

    def test_tool_impls_available(self):
        from wisp.tools import TOOL_IMPLS
        assert isinstance(TOOL_IMPLS, dict)
        assert len(TOOL_IMPLS) > 0

    def test_execute_tool_available(self):
        from wisp.tools import execute_tool
        assert callable(execute_tool)

    def test_tool_error_available(self):
        from wisp.tools import ToolError
        assert issubclass(ToolError, Exception)

    def test_all_tools_have_schemas(self):
        from wisp.tools import TOOL_SCHEMAS, TOOL_IMPLS
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        impl_names = set(TOOL_IMPLS.keys())
        assert schema_names == impl_names, f"Mismatch: {schema_names ^ impl_names}"

    def test_no_legacy_imports(self):
        """Verify no code imports from _legacy.py anymore."""
        import subprocess
        result = subprocess.run(
            ["grep", "-rn", "wisp.tools._legacy", "--include=*.py", "wisp/"],
            capture_output=True, text=True,
        )
        # Filter out __pycache__ and the _legacy file itself
        lines = [l for l in result.stdout.splitlines() if "__pycache__" not in l and "_legacy.py:" not in l]
        assert len(lines) == 0, f"Found legacy imports: {lines}"


class TestSubmoduleTools:

    def test_filesystem_tools(self):
        from wisp.tools import TOOL_IMPLS
        assert "read_file" in TOOL_IMPLS
        assert "write_file" in TOOL_IMPLS
        assert "edit_file" in TOOL_IMPLS
        assert "edit_file_multi" in TOOL_IMPLS
        assert "list_files" in TOOL_IMPLS

    def test_bash_tool(self):
        from wisp.tools import TOOL_IMPLS
        assert "run_bash" in TOOL_IMPLS

    def test_web_tools(self):
        from wisp.tools import TOOL_IMPLS
        assert "web_fetch" in TOOL_IMPLS
        assert "web_search" in TOOL_IMPLS

    def test_git_tools(self):
        from wisp.tools import TOOL_IMPLS
        assert "git_status" in TOOL_IMPLS
        assert "git_diff" in TOOL_IMPLS
        assert "git_branch" in TOOL_IMPLS
        assert "git_commit" in TOOL_IMPLS
        assert "git_push" in TOOL_IMPLS
        assert "gh_pr_create" in TOOL_IMPLS

    def test_lsp_tools(self):
        from wisp.tools import TOOL_IMPLS
        assert "lsp_diagnostics" in TOOL_IMPLS
        assert "lsp_definition" in TOOL_IMPLS
        assert "lsp_references" in TOOL_IMPLS
        assert "lsp_hover" in TOOL_IMPLS
        assert "lsp_symbols" in TOOL_IMPLS

    def test_memory_tools(self):
        from wisp.tools import TOOL_IMPLS
        assert "remember" in TOOL_IMPLS
        assert "recall" in TOOL_IMPLS

    def test_search_tools(self):
        from wisp.tools import TOOL_IMPLS
        assert "search_symbols" in TOOL_IMPLS
        assert "search_codebase" in TOOL_IMPLS

    def test_plan_tools(self):
        from wisp.tools import TOOL_IMPLS
        assert "plan_task" in TOOL_IMPLS
        assert "mark_step_done" in TOOL_IMPLS
        assert "update_plan" in TOOL_IMPLS

    def test_diagnose_tool(self):
        from wisp.tools import TOOL_IMPLS
        assert "diagnose" in TOOL_IMPLS

    def test_spawn_tools(self):
        from wisp.tools import TOOL_IMPLS
        assert "spawn" in TOOL_IMPLS
        assert "fanout" in TOOL_IMPLS
        # spawn_subagent removed from schemas — merged into spawn

    def test_run_tests_tool(self):
        from wisp.tools import TOOL_IMPLS
        assert "run_tests" in TOOL_IMPLS


class TestExecuteTool:

    def test_execute_unknown_tool(self):
        from wisp.tools import execute_tool, ToolError
        with pytest.raises(ToolError, match="Unknown tool"):
            execute_tool("nonexistent", {}, ".")

    def test_execute_list_files(self, tmp_path):
        from wisp.tools import execute_tool
        (tmp_path / "test.txt").write_text("hello")
        result = execute_tool("list_files", {"path": str(tmp_path)}, str(tmp_path))
        assert "test.txt" in result

    def test_execute_read_file(self, tmp_path):
        from wisp.tools import execute_tool
        (tmp_path / "test.txt").write_text("hello world")
        result = execute_tool("read_file", {"path": "test.txt"}, str(tmp_path))
        assert "hello world" in result

    def test_execute_spawn_subagent_stub(self):
        from wisp.tools import execute_tool, ToolError
        # spawn_subagent removed from schemas/impls — now unknown tool
        with pytest.raises(ToolError, match="Unknown tool"):
            execute_tool("spawn_subagent", {"task": "test"}, ".")

    def test_execute_spawn_stub(self):
        from wisp.tools import execute_tool
        result = execute_tool("spawn", {"task": "test"}, ".")
        assert "error" in result
        assert "agent loop" in result


# ═══════════════════════════════════════════════════════════════════
# ToolRegistry class tests (Phase 7.2)
# ═══════════════════════════════════════════════════════════════════

class TestToolRegistry:
    """TDD tests for ToolRegistry class."""

    def test_create_registry_with_defaults(self):
        from wisp.tools.registry import ToolRegistry
        registry = ToolRegistry()
        schemas = registry.schemas()
        assert len(schemas) >= 28, f"Expected >=28 built-in tools, got {len(schemas)}"
        assert registry.has_tool("read_file")
        assert registry.has_tool("write_file")
        assert registry.has_tool("run_bash")

    def test_register_new_tool(self):
        from wisp.tools.registry import ToolRegistry
        registry = ToolRegistry()
        registry.register("my_tool", lambda **kw: "done", {"type": "function", "function": {"name": "my_tool"}})
        assert registry.has_tool("my_tool")
        schemas = registry.schemas()
        names = {s["function"]["name"] for s in schemas}
        assert "my_tool" in names

    def test_unregister_removes_tool(self):
        from wisp.tools.registry import ToolRegistry
        registry = ToolRegistry()
        registry.register("temp_tool", lambda **kw: "ok", {"type": "function", "function": {"name": "temp_tool"}})
        assert registry.has_tool("temp_tool")
        registry.unregister("temp_tool")
        assert not registry.has_tool("temp_tool")

    def test_execute_runs_tool(self):
        from wisp.tools.registry import ToolRegistry
        registry = ToolRegistry()
        registry.register("echo", lambda text, **kw: text, {"type": "function", "function": {"name": "echo"}})
        result = registry.execute("echo", {"text": "hello"}, "/tmp")
        assert "hello" in result

    def test_execute_unknown_tool_raises(self):
        from wisp.tools.registry import ToolRegistry, ToolError
        registry = ToolRegistry()
        with pytest.raises(ToolError, match="Unknown tool"):
            registry.execute("nonexistent", {}, "/tmp")

    def test_singleton_shared_state(self):
        from wisp.tools.registry import default_registry, TOOL_IMPLS
        assert default_registry._impls is TOOL_IMPLS, \
            "default_registry._impls must be the same object as TOOL_IMPLS"

    def test_runtime_register_unregister_cycle(self):
        from wisp.tools.registry import ToolRegistry
        registry = ToolRegistry()
        registry.register("step1", lambda **kw: "first", {"type": "function", "function": {"name": "step1"}})
        assert registry.has_tool("step1")
        registry.unregister("step1")
        assert not registry.has_tool("step1")
        registry.register("step1", lambda **kw: "second", {"type": "function", "function": {"name": "step1"}})
        assert registry.has_tool("step1")
