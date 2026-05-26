"""TDD for Extension implementations.

Tests the four extension types that plug into ExtensionHost.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestPluginExtension:
    """PluginExtension wraps PluginRegistry."""

    def test_plugin_extension_has_name(self):
        from wisp.extensions.plugins import PluginExtension
        ext = PluginExtension()
        assert hasattr(ext, "name")
        assert isinstance(ext.name, str)

    def test_plugin_extension_has_tools(self):
        from wisp.extensions.plugins import PluginExtension
        ext = PluginExtension()
        assert hasattr(ext, "tools")
        tools = ext.tools()
        assert isinstance(tools, list)

    def test_plugin_extension_has_intercept(self):
        from wisp.extensions.plugins import PluginExtension
        ext = PluginExtension()
        assert hasattr(ext, "intercept")
        result = ext.intercept({"type": "test"})
        assert result["action"] == "allow"

    def test_plugin_extension_lifecycle(self):
        from wisp.extensions.plugins import PluginExtension
        ext = PluginExtension()
        ext.start()
        ext.stop()


class TestHookExtension:
    """HookExtension wraps HookManager."""

    def test_hook_extension_has_name(self):
        from wisp.extensions.hooks import HookExtension
        ext = HookExtension()
        assert hasattr(ext, "name")
        assert isinstance(ext.name, str)

    def test_hook_extension_has_tools(self):
        from wisp.extensions.hooks import HookExtension
        ext = HookExtension()
        tools = ext.tools()
        assert isinstance(tools, list)

    def test_hook_extension_intercept_allows_by_default(self):
        from wisp.extensions.hooks import HookExtension
        ext = HookExtension()
        result = ext.intercept({"type": "test"})
        assert result["action"] == "allow"

    def test_hook_extension_lifecycle(self):
        from wisp.extensions.hooks import HookExtension
        ext = HookExtension()
        ext.start()
        ext.stop()


class TestMCPExtension:
    """MCPExtension wraps MCPManager."""

    def test_mcp_extension_has_name(self):
        from wisp.extensions.mcp import MCPExtension
        ext = MCPExtension()
        assert hasattr(ext, "name")
        assert isinstance(ext.name, str)

    def test_mcp_extension_has_tools(self):
        from wisp.extensions.mcp import MCPExtension
        ext = MCPExtension()
        tools = ext.tools()
        assert isinstance(tools, list)

    def test_mcp_extension_intercept_allows_by_default(self):
        from wisp.extensions.mcp import MCPExtension
        ext = MCPExtension()
        result = ext.intercept({"type": "test"})
        assert result["action"] == "allow"

    def test_mcp_extension_lifecycle(self):
        from wisp.extensions.mcp import MCPExtension
        ext = MCPExtension()
        ext.start()
        ext.stop()


class TestSkillExtension:
    """SkillExtension wraps Skill discovery."""

    def test_skill_extension_has_name(self):
        from wisp.extensions.skills import SkillExtension
        ext = SkillExtension()
        assert hasattr(ext, "name")
        assert isinstance(ext.name, str)

    def test_skill_extension_has_tools(self):
        from wisp.extensions.skills import SkillExtension
        ext = SkillExtension()
        tools = ext.tools()
        assert isinstance(tools, list)

    def test_skill_extension_intercept_allows_by_default(self):
        from wisp.extensions.skills import SkillExtension
        ext = SkillExtension()
        result = ext.intercept({"type": "test"})
        assert result["action"] == "allow"

    def test_skill_extension_lifecycle(self):
        from wisp.extensions.skills import SkillExtension
        ext = SkillExtension()
        ext.start()
        ext.stop()


class TestExtensionHostIntegration:
    """ExtensionHost manages all four extension types."""

    def test_host_registers_all_extensions(self):
        from wisp.infra.extensions import ExtensionHost
        from wisp.extensions.plugins import PluginExtension
        from wisp.extensions.hooks import HookExtension
        from wisp.extensions.mcp import MCPExtension
        from wisp.extensions.skills import SkillExtension

        host = ExtensionHost()
        host.register(PluginExtension())
        host.register(HookExtension())
        host.register(MCPExtension())
        host.register(SkillExtension())

        tools = host.tools()
        assert isinstance(tools, list)

    def test_host_intercept_first_block_wins(self):
        from wisp.infra.extensions import ExtensionHost

        host = ExtensionHost()
        mock_ext = MagicMock()
        mock_ext.intercept.return_value = {"action": "block", "reason": "test"}
        host.register(mock_ext)

        result = host.intercept({"type": "tool_call"})
        assert result["action"] == "block"
        assert result["reason"] == "test"

    def test_host_shutdown_stops_all(self):
        from wisp.infra.extensions import ExtensionHost

        host = ExtensionHost()
        mock_ext1 = MagicMock()
        mock_ext2 = MagicMock()
        host.register(mock_ext1)
        host.register(mock_ext2)

        host.stop()
        mock_ext1.stop.assert_called_once()
        mock_ext2.stop.assert_called_once()


class TestHookManagerSeparation:
    """Issue 9: HookManager split into InterceptHookManager and ToolHookManager."""

    def test_intercept_hook_manager_exists(self):
        from wisp.infra.hook_types import InterceptHookManager
        assert InterceptHookManager is not None

    def test_tool_hook_manager_exists(self):
        from wisp.infra.hook_types import ToolHookManager
        assert ToolHookManager is not None

    def test_intercept_hook_manager_run_hooks_evaluates_matching_hooks(self):
        from wisp.infra.hook_types import InterceptHookManager, HookEvent, HookConfig
        mgr = InterceptHookManager()
        mgr.register(HookConfig(name="blocker", event=HookEvent.TOOL_CALL, command="exit 2", enabled=True))
        event = HookEvent(HookEvent.TOOL_CALL, name="bash", args={"cmd": "ls"})
        result = mgr.run_hooks(event)
        assert result.decision == "block"

    def test_tool_hook_manager_run_hooks_returns_list(self):
        from wisp.infra.hook_types import ToolHookManager, HookEvent, HookConfig
        mgr = ToolHookManager()
        mgr.register(HookConfig(name="pre", event=HookEvent.PRE_TOOL_USE, command="exit 0", enabled=True))
        results = mgr.run_hooks(HookEvent.PRE_TOOL_USE, {"tool_name": "bash"})
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0].decision == "allow"

    def test_intercept_and_tool_managers_are_distinct_instances(self):
        from wisp.infra.hook_types import InterceptHookManager, ToolHookManager
        i = InterceptHookManager()
        t = ToolHookManager()
        assert i is not t
