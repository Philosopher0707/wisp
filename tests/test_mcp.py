"""Tests for MCP client — config discovery, server connection, tool management."""

import json

from wisp.mcp import (
    MCPTool,
    discover_mcp_configs,
    MCPManager,
)


class TestDiscoverMCPConfigs:
    def test_no_config_files(self, tmp_path):
        """Empty workspace yields no configs."""
        configs = discover_mcp_configs(str(tmp_path))
        assert configs == []

    def test_discover_from_wisp_dir(self, tmp_path):
        """Discover config from .wisp/mcp.json in workspace."""
        from wisp.trust import WorkspaceTrustManager
        WorkspaceTrustManager.trust_workspace(str(tmp_path))
        mcp_dir = tmp_path / ".wisp"
        mcp_dir.mkdir(parents=True)
        config_file = mcp_dir / "mcp.json"
        config_file.write_text(json.dumps([
            {
                "name": "test-server",
                "command": "python",
                "args": ["-m", "test_server"],
            }
        ]))
        configs = discover_mcp_configs(str(tmp_path))
        assert len(configs) == 1
        assert configs[0].name == "test-server"
        assert configs[0].command == "python"

    def test_discover_multiple_servers(self, tmp_path):
        """Discover multiple MCP servers from config."""
        from wisp.trust import WorkspaceTrustManager
        WorkspaceTrustManager.trust_workspace(str(tmp_path))
        mcp_dir = tmp_path / ".wisp"
        mcp_dir.mkdir(parents=True)
        config_file = mcp_dir / "mcp.json"
        config_file.write_text(json.dumps({
            "mcpServers": [
                {"name": "db", "command": "db-tool"},
                {"name": "api", "command": "api-tool", "disabled": True},
                {"name": "fs", "url": "http://localhost:8080/mcp"},
            ]
        }))
        configs = discover_mcp_configs(str(tmp_path))
        assert len(configs) == 2  # disabled server excluded
        names = [c.name for c in configs]
        assert "db" in names
        assert "fs" in names
        assert "api" not in names

    def test_workspace_overrides_home(self, tmp_path, monkeypatch):
        """Workspace config takes priority over home config."""
        from wisp.trust import WorkspaceTrustManager
        WorkspaceTrustManager.trust_workspace(str(tmp_path))
        monkeypatch.setattr("wisp.mcp.manager.Path.home", lambda: tmp_path / "home")

        # Home config
        home_mcp = tmp_path / "home" / ".config" / "wisp" / "mcp.json"
        home_mcp.parent.mkdir(parents=True)
        home_mcp.write_text(json.dumps([
            {"name": "shared-server", "command": "shared"},
        ]))

        # Workspace config (overrides)
        ws_mcp = tmp_path / ".wisp" / "mcp.json"
        ws_mcp.parent.mkdir(parents=True)
        ws_mcp.write_text(json.dumps([
            {"name": "shared-server", "command": "workspace-version"},
            {"name": "local-server", "command": "local"},
        ]))

        configs = discover_mcp_configs(str(tmp_path))
        assert len(configs) == 2
        names = {c.name: c.command for c in configs}
        assert names["shared-server"] == "workspace-version"  # workspace wins
        assert names["local-server"] == "local"


class TestMCPManager:
    def test_initialization_with_no_configs(self, tmp_path):
        """Manager initializes cleanly with no MCP servers."""
        manager = MCPManager(str(tmp_path))
        manager.initialize()
        assert manager.get_all_tools() == []
        assert manager.get_tool_schemas() == []

    def test_get_all_tools_empty(self, tmp_path):
        """No tools when no servers connected."""
        manager = MCPManager(str(tmp_path))
        assert manager.get_all_tools() == []

    def test_shutdown_cleanup(self, tmp_path):
        """Shutdown cleans up without errors."""
        manager = MCPManager(str(tmp_path))
        manager.initialize()
        manager.shutdown()
        assert len(manager.servers) == 0

    def test_call_unknown_tool_raises(self, tmp_path):
        """Calling an unknown tool raises ValueError."""
        manager = MCPManager(str(tmp_path))
        manager.initialize()
        try:
            manager.call_tool("nonexistent", {})
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "not found" in str(e)


class TestMCPTool:
    def test_tool_creation(self):
        """MCPTool can be created with required fields."""
        tool = MCPTool(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {}},
            server_name="test-server",
        )
        assert tool.name == "test_tool"
        assert tool.server_name == "test-server"
