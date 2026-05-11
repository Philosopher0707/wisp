"""Tests for wisp.plugin_registry — runtime tool registration."""

import pytest
from wisp.plugin_registry import (
    register_tool,
    register_tools,
    unregister_tool,
    list_plugin_tools,
    get_plugin_impl,
    get_plugin_schemas,
    has_plugin_tool,
    execute_plugin_tool,
    _build_schema_from_signature,
    clear_plugin_tools,
)


class TestRegisterTool:
    """Plugin registration end-to-end."""

    def test_register_simple_tool(self):
        clear_plugin_tools()

        def my_tool(query: str) -> str:
            return f"result: {query}"

        register_tool("my_tool", my_tool, description="A test tool")
        assert "my_tool" in list_plugin_tools()
        schemas = get_plugin_schemas()
        assert any(s["function"]["name"] == "my_tool" for s in schemas)

    def test_auto_schema_generation(self):
        clear_plugin_tools()

        def my_api(x: int, y: str, flag: bool = True) -> dict:
            return {"x": x, "y": y, "flag": flag}

        register_tool("my_api", my_api)
        schemas = get_plugin_schemas()
        schema = next(s for s in schemas if s["function"]["name"] == "my_api")
        params = schema["function"]["parameters"]
        assert "x" in params["properties"]
        assert "y" in params["properties"]
        assert "flag" in params["properties"]
        assert params["properties"]["x"]["type"] == "integer"
        assert params["properties"]["y"]["type"] == "string"
        assert params["properties"]["flag"]["type"] == "boolean"
        assert "x" in params["required"]
        assert "y" in params["required"]
        assert "flag" not in params["required"]  # has default

    def test_execute_plugin_tool(self):
        clear_plugin_tools()

        def calc(a: int, b: int) -> int:
            return a + b

        register_tool("calc", calc)
        assert has_plugin_tool("calc")
        result = execute_plugin_tool("calc", a=3, b=4)
        assert result == 7

    def test_unregister_tool(self):
        clear_plugin_tools()

        def t(): pass
        register_tool("t", t)
        assert "t" in list_plugin_tools()
        assert unregister_tool("t") is True
        assert "t" not in list_plugin_tools()
        assert unregister_tool("t") is False

    def test_override_existing_tool_warns(self):
        clear_plugin_tools()

        def v1(): return "v1"
        def v2(): return "v2"
        register_tool("dup", v1)
        # Overriding should work
        register_tool("dup", v2)
        impl = get_plugin_impl("dup")
        assert impl() == "v2"
        assert len([s for s in get_plugin_schemas() if s["function"]["name"] == "dup"]) == 1

    def test_register_tools_bulk(self):
        clear_plugin_tools()

        def a(): return "a"
        def b(): return "b"

        register_tools([
            {"name": "tool_a", "impl": a, "description": "Tool A"},
            {"name": "tool_b", "impl": b, "description": "Tool B"},
        ])
        assert set(list_plugin_tools()) == {"tool_a", "tool_b"}

    def test_invalid_schema_raises(self):
        clear_plugin_tools()

        with pytest.raises(ValueError):
            register_tool("bad", lambda: None, schema={"invalid": True})

    def test_clear_plugin_tools(self):
        clear_plugin_tools()
        register_tool("tmp", lambda: None)
        assert len(list_plugin_tools()) == 1
        clear_plugin_tools()
        assert list_plugin_tools() == []


class TestSchemaFromSignature:
    """Auto-schema generation from Python signatures."""

    def test_simple_params(self):
        def f(name: str, count: int) -> str:
            return ""
        schema = _build_schema_from_signature(f, "f")
        params = schema["function"]["parameters"]["properties"]
        assert params["name"]["type"] == "string"
        assert params["count"]["type"] == "integer"
        assert schema["function"]["parameters"]["required"] == ["name", "count"]

    def test_optional_param_not_required(self):
        def f(name: str, limit: int = 10) -> str:
            return ""
        schema = _build_schema_from_signature(f, "f")
        assert "name" in schema["function"]["parameters"]["required"]
        assert "limit" not in schema["function"]["parameters"]["required"]

    def test_skips_injected_params(self):
        def f(path: str, workspace: str, file_lock=None) -> str:
            return ""
        schema = _build_schema_from_signature(f, "f")
        props = schema["function"]["parameters"]["properties"]
        assert "path" in props
        assert "workspace" not in props
        assert "file_lock" not in props

    def test_description_from_docstring(self):
        def f(x: int) -> int:
            """Multiplies by two."""
            return x * 2
        schema = _build_schema_from_signature(f, "f")
        assert "Multiplies by two" in schema["function"]["description"]


class TestIntegrationWithToolExecution:
    """End-to-end: register_tool → execute_tool."""

    def test_plugin_fallback_in_execute_tool(self):
        clear_plugin_tools()

        def custom_lookup(query: str) -> str:
            return f"lookup_result: {query}"

        register_tool("custom_lookup", custom_lookup)
        from wisp.tools import execute_tool
        result = execute_tool("custom_lookup", {"query": "hello"}, ".")
        assert "status" in result
        assert "ok" in result
        assert "hello" in result

    def test_unknown_tool_still_errors(self):
        clear_plugin_tools()
        from wisp.tools import execute_tool, ToolError
        with pytest.raises(ToolError):
            execute_tool("__does_not_exist__", {}, ".")


class TestConfig:
    """Test configurability."""

    def test_no_tools_after_clear(self):
        clear_plugin_tools()
        assert get_plugin_schemas() == []
        assert get_plugin_impl("anything") is None
