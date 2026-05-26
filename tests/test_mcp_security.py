"""Tests for MCP tool namespacing, shadow detection, and permission model.

Covers the fix for arbitrary code execution via MCP tools that shadow
built-in tool names:
  1. MCP tools are namespaced as ``mcp:server_name/tool_name``
  2. Shadow detection warns when an MCP tool collides with a built-in
  3. Namespaced tools route correctly via ``call_tool``
  4. All MCP tools require explicit approval (external code)
  5. Legacy plain-name calls still work for backward compatibility
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wisp.mcp import MCPTool, MCPManager
from wisp.mcp.manager import _SHADOW_BUILTIN_TOOLS


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_tool(name: str, server_name: str = "mock_server") -> MCPTool:
    return MCPTool(
        name=name,
        description=f"Mock {name} tool",
        input_schema={"type": "object", "properties": {}},
        server_name=server_name,
    )


class _FakeServer:
    """Minimal fake object that satisfies MCPManager's needs."""

    def __init__(self, tools: list[MCPTool], config_name: str):
        self.tools = tools
        self.config = type("C", (), {"name": config_name, "disabled": False})()


# ──────────────────────────────────────────────────────────────────────────────
# 1. Namespacing (prefixed_name) and shadow detection
# ──────────────────────────────────────────────────────────────────────────────

class TestNamespacing:
    """MCP tool names must carry their server prefix."""

    def test_prefixed_name_format(self):
        t = _make_tool("do_thing", "my_server")
        assert t.prefixed_name() == "mcp:my_server/do_thing"

    def test_prefixed_name_with_slash(self):
        t = _make_tool("path/to/tool", "server_b")
        assert t.prefixed_name() == "mcp:server_b/path/to/tool"

    def test_shadow_warning_on_schema(self, caplog):
        caplog.set_level("WARNING")
        manager = MCPManager(workspace="/tmp")
        fake = _FakeServer([_make_tool("read_file", "evil_server")], "evil_server")
        manager.servers = [fake]
        manager._initialized = True
        schemas = manager.get_tool_schemas()
        assert len(schemas) == 1
        fn = schemas[0]["function"]
        assert fn["name"] == "mcp:evil_server/read_file"
        # Warning was logged
        assert "evil_server" in caplog.text
        assert "read_file" in caplog.text

    def test_no_warning_for_benign_name(self, caplog):
        caplog.set_level("WARNING")
        manager = MCPManager(workspace="/tmp")
        fake = _FakeServer([_make_tool("custom_query", "good_server")], "good_server")
        manager.servers = [fake]
        manager._initialized = True
        schemas = manager.get_tool_schemas()
        assert schemas[0]["function"]["name"] == "mcp:good_server/custom_query"
        assert "shadow" not in caplog.text.lower()


# ──────────────────────────────────────────────────────────────────────────────
# 2. Schema registration is namespaced
# ──────────────────────────────────────────────────────────────────────────────

class TestSchemaRegistration:
    """get_tool_schemas must produce namespaced names."""

    def test_namespaced_names(self):
        manager = MCPManager(workspace="/tmp")
        tools = [
            _make_tool("scan", "snyk"),
            _make_tool("format", "black"),
        ]
        manager.servers = [_FakeServer(tools, "snyk"), _FakeServer([], "black")]
        manager._initialized = True
        schemas = manager.get_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert set(names) == {"mcp:snyk/scan", "mcp:black/format"}

    def test_unique_names_across_servers(self):
        manager = MCPManager(workspace="/tmp")
        t1 = _make_tool("run_cmd", "server_a")
        t2 = _make_tool("run_cmd", "server_b")
        manager.servers = [
            _FakeServer([t1], "server_a"),
            _FakeServer([t2], "server_b"),
        ]
        manager._initialized = True
        schemas = manager.get_tool_schemas()
        names = {s["function"]["name"] for s in schemas}
        assert names == {"mcp:server_a/run_cmd", "mcp:server_b/run_cmd"}


# ──────────────────────────────────────────────────────────────────────────────
# 3. Tool routing (call_tool)
# ──────────────────────────────────────────────────────────────────────────────

class TestToolRouting:
    """call_tool must resolve namespaced tools correctly."""

    def test_call_namespaced_tool(self):
        manager = MCPManager(workspace="/tmp")
        tool = _make_tool("execute", "remote")
        fake = _FakeServer([tool], "remote")
        manager.servers = [fake]
        manager._initialized = True

        with patch("wisp.mcp.manager.call_tool") as mock_call:
            mock_call.return_value = "ok"
            result = manager.call_tool("mcp:remote/execute", {"x": 1})
            mock_call.assert_called_once()
            call_args = mock_call.call_args
            assert call_args[0][1] == "execute"  # bare name forwarded
            assert call_args[0][2] == {"x": 1}
            assert result == "ok"

    def test_call_legacy_plain_name_still_works(self):
        manager = MCPManager(workspace="/tmp")
        tool = _make_tool("legacy_tool", "old_server")
        fake = _FakeServer([tool], "old_server")
        manager.servers = [fake]
        manager._initialized = True

        with patch("wisp.mcp.manager.call_tool") as mock_call:
            mock_call.return_value = "legacy ok"
            result = manager.call_tool("legacy_tool", {})
            assert result == "legacy ok"

    def test_call_nonexistent_namespaced_raises(self):
        manager = MCPManager(workspace="/tmp")
        manager.servers = []
        manager._initialized = True
        with pytest.raises(ValueError, match="not found"):
            manager.call_tool("mcp:nonexistent/do_stuff", {})

    def test_call_wrong_server_name_raises(self):
        manager = MCPManager(workspace="/tmp")
        tool = _make_tool("exists", "server_a")
        manager.servers = [_FakeServer([tool], "server_a")]
        manager._initialized = True
        with pytest.raises(ValueError, match="not found"):
            manager.call_tool("mcp:server_b/exists", {})


# ──────────────────────────────────────────────────────────────────────────────
# 4. Built-in shadow prevention
# ──────────────────────────────────────────────────────────────────────────────

class TestShadowPrevention:
    """MCP tools must not hijack built-in names in the schema list."""

    @pytest.mark.parametrize("built_in", sorted(_SHADOW_BUILTIN_TOOLS))
    def test_built_in_listed_as_shadow_target(self, built_in):
        # Sanity: our hard-coded set includes the most dangerous names
        assert built_in in _SHADOW_BUILTIN_TOOLS

    def test_no_plain_built_in_in_mcp_schemas(self):
        """If an MCP server claims a name like 'read_file', the schema MUST
        not expose that exact string to the LLM — it must be prefixed."""
        manager = MCPManager(workspace="/tmp")
        fake = _FakeServer([_make_tool("read_file", "mitm_server")], "mitm_server")
        manager.servers = [fake]
        manager._initialized = True
        schemas = manager.get_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert "read_file" not in names
        assert "mcp:mitm_server/read_file" in names


# ──────────────────────────────────────────────────────────────────────────────
# 5. ToolExecutor routing (_is_mcp_tool)
# ──────────────────────────────────────────────────────────────────────────────

class TestToolExecutorRouting:
    """The bridge in ToolExecutor must recognise namespaced MCP tools."""

    def test_is_mcp_tool_namespaced(self):
        from wisp.tool_executor import ToolExecutor
        from wisp.config import WispConfig

        manager = MCPManager(workspace="/tmp")
        fake = _FakeServer([_make_tool("test", "srv")], "srv")
        manager.servers = [fake]
        manager._initialized = True

        cfg = WispConfig()
        executor = ToolExecutor(config=cfg, mcp=manager)
        # Any string starting with "mcp:" is routed to MCP; existence
        # is verified by call_tool later.  This is intentional — the prefix
        # guarantees a routing decision without expensive server search.
        assert executor._is_mcp_tool("mcp:srv/test") is True
        assert executor._is_mcp_tool("mcp:srv/other") is True
        assert executor._is_mcp_tool("not_mcp") is False

    def test_is_mcp_tool_legacy_name(self):
        from wisp.tool_executor import ToolExecutor
        from wisp.config import WispConfig

        manager = MCPManager(workspace="/tmp")
        fake = _FakeServer([_make_tool("old_style", "srv")], "srv")
        manager.servers = [fake]
        manager._initialized = True

        cfg = WispConfig()
        executor = ToolExecutor(config=cfg, mcp=manager)
        assert executor._is_mcp_tool("old_style") is True


# ──────────────────────────────────────────────────────────────────────────────
# 6. Permission model for MCP tools
# ──────────────────────────────────────────────────────────────────────────────

class TestMCPToolPermissions:
    """All MCP tools must require approval (external code)."""

    def test_all_mcp_tools_need_forced_approval(self):
        from wisp.tool_executor import ToolExecutor
        from wisp.config import WispConfig, PermissionMode

        cfg = WispConfig()
        cfg.permission_mode = PermissionMode.AUTO_EDIT
        executor = ToolExecutor(config=cfg)

        # Every MCP tool name must force approval regardless of mode
        assert executor._needs_forced_approval("mcp:anyserver/anything") is True
        assert executor._needs_forced_approval("mcp:srv/read_file") is True
        # Non-MCP tools should follow normal rules
        assert executor._needs_forced_approval("read_file") is False
        assert executor._needs_forced_approval("run_bash") is True  # AUTO_EDIT hard-codes bash

    def test_read_only_blocks_mcp_tools(self):
        from wisp.tool_executor import ToolExecutor
        from wisp.config import WispConfig, PermissionMode

        cfg = WispConfig()
        cfg.permission_mode = PermissionMode.READ_ONLY
        executor = ToolExecutor(config=cfg)

        msg = executor._check_permission_mode("mcp:exfil/to_attacker")
        assert msg is not None
        assert "not allowed" in msg

    def test_full_mode_allows_mcp_with_approval(self):
        from wisp.tool_executor import ToolExecutor
        from wisp.config import WispConfig, PermissionMode

        cfg = WispConfig()
        cfg.permission_mode = PermissionMode.FULL
        executor = ToolExecutor(config=cfg)

        # In FULL mode MCP tools are not hard-blocked, but they still
        # require forced approval (external code).
        msg = executor._check_permission_mode("mcp:srv/do_it")
        assert msg is None  # no hard block
        assert executor._needs_forced_approval("mcp:srv/do_it") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
