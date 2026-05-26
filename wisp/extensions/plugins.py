"""PluginExtension — wraps PluginRegistry for ExtensionHost.

Provides plugin tools and lifecycle management.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PluginExtension:
    """Extension adapter for the plugin system."""

    name = "plugins"

    def __init__(self):
        self._registry = None

    def start(self) -> None:
        """Start the plugin extension — load registry."""
        from wisp.plugins.registry import PluginRegistry
        self._registry = PluginRegistry()
        logger.debug("PluginExtension started")

    def stop(self) -> None:
        """Stop the plugin extension."""
        self._registry = None
        logger.debug("PluginExtension stopped")

    def tools(self) -> list[dict]:
        """Return plugin-provided tools."""
        if self._registry is None:
            return []
        try:
            installed = self._registry.list_installed()
            tools = []
            for plugin in installed:
                # Each plugin may expose tools via its namespace
                if hasattr(plugin, "tools"):
                    tools.extend(plugin.tools())
            return tools
        except Exception as exc:
            logger.warning("PluginExtension tools() failed: %s", exc)
            return []

    def intercept(self, event: dict) -> dict:
        """Intercept events — plugins don't block by default."""
        return {"action": "allow"}
