"""TDD for ExtensionHost — unified extension system.

Replaces: plugin_registry.py, hooks.py, mcp.py, and skills.py
with one lifecycle-managed host.
"""

import pytest
from dataclasses import dataclass


# ── Test fixtures ──────────────────────────────────────────────────

@pytest.fixture
def host():
    from wisp.infra.extensions import ExtensionHost
    return ExtensionHost()


# ── Minimal extension implementations for testing ────────────────────

@dataclass
class _TestPlugin:
    name: str = "test_plugin"
    _started: bool = False
    _stopped: bool = False

    def start(self):
        self._started = True

    def stop(self):
        self._stopped = True

    def tools(self):
        return [{"function": {"name": f"{self.name}__read_file"}}]

    def intercept(self, event):
        return {"action": "allow"}


@dataclass
class _TestHook:
    name: str = "test_hook"
    _started: bool = False
    _stopped: bool = False
    block_event: str = ""

    def start(self):
        self._started = True

    def stop(self):
        self._stopped = True

    def tools(self):
        return []

    def intercept(self, event):
        if event.get("type") == self.block_event:
            return {"action": "block", "reason": "hook says no"}
        return {"action": "allow"}


# ═══════════════════════════════════════════════════════════════════
# 1. Registration and lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestExtensionLifecycle:
    """Extensions are started on register, stopped on shutdown."""

    def test_register_starts_extension(self, host):
        ext = _TestPlugin()
        host.register(ext)
        assert ext._started is True

    def test_stop_stops_extensions(self, host):
        ext = _TestPlugin()
        host.register(ext)
        host.stop()
        assert ext._stopped is True

    def test_stop_reverses_order(self, host):
        order = []

        class _Ordered:
            def __init__(self, name):
                self.name = name
            def start(self): pass
            def stop(self): order.append(self.name)
            def tools(self): return []
            def intercept(self, event): return {"action": "allow"}

        host.register(_Ordered("first"))
        host.register(_Ordered("second"))
        host.stop()
        assert order == ["second", "first"]


# ═══════════════════════════════════════════════════════════════════
# 2. Tool aggregation
# ═══════════════════════════════════════════════════════════════════

class TestToolAggregation:
    """Tools from all extensions are aggregated."""

    def test_empty_host_has_no_tools(self, host):
        assert host.tools() == []

    def test_single_extension_tools(self, host):
        host.register(_TestPlugin(name="myplugin"))
        tools = host.tools()
        assert len(tools) == 1
        assert tools[0]["function"]["name"] == "myplugin__read_file"

    def test_multiple_extension_tools(self, host):
        host.register(_TestPlugin(name="plugin_a"))
        host.register(_TestPlugin(name="plugin_b"))
        tools = host.tools()
        names = {t["function"]["name"] for t in tools}
        assert names == {"plugin_a__read_file", "plugin_b__read_file"}


# ═══════════════════════════════════════════════════════════════════
# 3. Event interception
# ═══════════════════════════════════════════════════════════════════

class TestEventInterception:
    """Extensions can intercept and block events."""

    def test_allow_by_default(self, host):
        result = host.intercept({"type": "tool_call", "name": "read_file"})
        assert result["action"] == "allow"

    def test_hook_can_block(self, host):
        host.register(_TestHook(block_event="run_bash"))
        result = host.intercept({"type": "run_bash"})
        assert result["action"] == "block"
        assert "hook says no" in result["reason"]

    def test_first_block_wins(self, host):
        host.register(_TestHook(block_event="run_bash"))  # blocks
        host.register(_TestPlugin())  # allows
        result = host.intercept({"type": "run_bash"})
        assert result["action"] == "block"

    def test_second_hook_can_block_if_first_allows(self, host):
        host.register(_TestPlugin())  # allows
        host.register(_TestHook(block_event="run_bash"))  # blocks
        result = host.intercept({"type": "run_bash"})
        assert result["action"] == "block"


# ═══════════════════════════════════════════════════════════════════
# 4. Namespace isolation
# ═══════════════════════════════════════════════════════════════════

class TestNamespaceIsolation:
    """Extension names are prefixed to prevent collisions."""

    def test_tool_names_are_prefixed(self, host):
        host.register(_TestPlugin(name="ext1"))
        host.register(_TestPlugin(name="ext2"))
        tools = host.tools()
        names = [t["function"]["name"] for t in tools]
        assert all("__" in n for n in names)
        assert len(set(names)) == 2


# ═══════════════════════════════════════════════════════════════════
# 5. Error handling
# ═══════════════════════════════════════════════════════════════════

class TestExtensionErrorHandling:
    """Broken extensions don't crash the host."""

    def test_broken_tool_list_is_ignored(self, host):
        class _Broken:
            def start(self): pass
            def stop(self): pass
            def tools(self): raise RuntimeError("boom")
            def intercept(self, event): return {"action": "allow"}

        host.register(_Broken())
        assert host.tools() == []  # gracefully ignored

    def test_broken_intercept_denies_by_default(self, host):
        """Broken extensions should deny (fail-closed) for security."""
        class _Broken:
            def start(self): pass
            def stop(self): pass
            def tools(self): return []
            def intercept(self, event): raise RuntimeError("boom")

        host.register(_Broken())
        result = host.intercept({"type": "tool_call"})
        assert result["action"] == "block"
        assert "Extension error" in result["reason"]
