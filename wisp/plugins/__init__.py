"""Wisp plugin packaging system.

Plugins extend Wisp with skills, slash commands, hooks, MCP servers,
custom agents, and themes. Each plugin is a directory containing a
plugin.json manifest at its root.

Public API:
    PluginManifest      -- plugin metadata and capabilities
    PluginCommand       -- slash command definition
    PluginRegistry      -- local plugin management (install/uninstall/enable/disable)
    MarketplaceRegistry -- remote community plugin marketplace
    NamespaceManager    -- tool name isolation for plugin tools
    discover_plugins    -- auto-discover plugins at startup
"""

from wisp.plugins.manifest import PluginManifest, PluginCommand
from wisp.plugins.registry import PluginRegistry, MarketplaceRegistry
from wisp.plugins.namespace import NamespaceManager
from wisp.plugins.discovery import discover_plugins

__all__ = [
    "PluginManifest",
    "PluginCommand",
    "PluginRegistry",
    "MarketplaceRegistry",
    "NamespaceManager",
    "discover_plugins",
]
