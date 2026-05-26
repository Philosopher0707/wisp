"""Namespace isolation — prevent plugin tools from colliding
with core tools or each other.

Plugin tools are prefixed as '{namespace}__{tool_name}' so that
a plugin with namespace "myorg" providing a tool "search_files"
becomes "myorg__search_files" in the tool registry.

Core (built-in) tools are never prefixed.
"""

from __future__ import annotations

import logging
import re

from wisp.plugins.manifest import PluginManifest

logger = logging.getLogger(__name__)

# Core tool names that are reserved and cannot be used by plugins.
CORE_TOOL_PREFIXES: set[str] = {
    "read",
    "write",
    "edit",
    "bash",
    "grep",
    "glob",
    "ls",
    "search",
    "web_search",
    "web_fetch",
    "memory",
    "skill",
    "task",
    "todo",
}

# Allowed namespace pattern: alphanumeric + underscores, must start with letter.
_NAMESPACE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")

# Separator between namespace and tool name.
_NS_SEPARATOR = "__"


class NamespaceManager:
    """Ensure plugin tools are namespaced and don't collide.

    Plugin tools get a '{namespace}__{tool_name}' prefix.
    Core tools are never prefixed and always resolve to (None, name).
    """

    def __init__(self, reserved_prefixes: set[str] | None = None):
        self._reserved = reserved_prefixes or CORE_TOOL_PREFIXES
        self._tool_to_plugin: dict[str, str] = {}

    def prefix_tool_name(self, plugin_name: str, tool_name: str) -> str:
        """Add plugin namespace prefix to a tool name.

        Core tools are returned unchanged. Plugin tools get the
        '{namespace}__{tool_name}' prefix.

        Args:
            plugin_name: The plugin's namespace string.
            tool_name: The original tool name.

        Returns:
            The possibly-prefixed tool name.
        """
        # Core tools are never prefixed
        if tool_name in self._reserved:
            return tool_name

        # Check if any reserved prefix matches the start
        for prefix in self._reserved:
            if tool_name == prefix or tool_name.startswith(f"{prefix}_"):
                return tool_name

        prefixed = f"{plugin_name}{_NS_SEPARATOR}{tool_name}"
        self._tool_to_plugin[prefixed] = plugin_name
        return prefixed

    def resolve_tool_name(self, prefixed_name: str) -> tuple[str | None, str]:
        """Resolve a prefixed tool name back to its source.

        Args:
            prefixed_name: A tool name that may be prefixed.

        Returns:
            A tuple of (plugin_name, original_tool_name).
            plugin_name is None for core/unprefixed tools.
        """
        if _NS_SEPARATOR not in prefixed_name:
            return (None, prefixed_name)

        parts = prefixed_name.split(_NS_SEPARATOR, 1)
        if len(parts) == 2:
            candidate_ns, original = parts
            if candidate_ns in self._tool_to_plugin.get(
                prefixed_name, ""
            ) or self._is_known_namespace(candidate_ns):
                return (candidate_ns, original)

        return (None, prefixed_name)

    def validate_namespace(self, namespace: str) -> bool:
        """Check if a namespace is valid and not already taken.

        A valid namespace:
        - Matches the pattern [a-zA-Z][a-zA-Z0-9_]*
        - Is not a reserved core prefix
        - Is not already registered

        Args:
            namespace: The namespace string to validate.

        Returns:
            True if the namespace is valid and available.
        """
        if not namespace or not _NAMESPACE_RE.match(namespace):
            logger.debug("Namespace '%s' fails pattern check", namespace)
            return False

        if namespace in self._reserved:
            logger.debug("Namespace '%s' conflicts with core tool prefix", namespace)
            return False

        # check for separator in namespace (would cause ambiguity)
        if _NS_SEPARATOR in namespace:
            logger.debug(
                "Namespace '%s' contains reserved separator '%s'",
                namespace,
                _NS_SEPARATOR,
            )
            return False

        return True

    def register_plugin_namespace(self, namespace: str) -> None:
        """Record that a namespace is in use.

        Args:
            namespace: The namespace to register.

        Raises:
            ValueError: If the namespace is already taken.
        """
        if not self.validate_namespace(namespace):
            raise ValueError(f"Namespace '{namespace}' is invalid or already taken")

    def register_plugin(self, manifest: PluginManifest) -> None:
        """Register all tool names from a plugin under its namespace.

        Args:
            manifest: The plugin manifest whose namespace to register.

        Raises:
            ValueError: If the namespace is invalid or conflicts.
        """
        self.register_plugin_namespace(manifest.namespace)
        logger.debug(
            "Registered namespace '%s' for plugin '%s'",
            manifest.namespace,
            manifest.name,
        )

    def is_core_tool(self, tool_name: str) -> bool:
        """Check if a tool name belongs to the core tool set.

        Args:
            tool_name: The tool name to check (with or without prefix).

        Returns:
            True if the tool is a core (built-in) tool.
        """
        base_name = self.resolve_tool_name(tool_name)[1]
        if base_name in self._reserved:
            return True
        for prefix in self._reserved:
            if base_name == prefix or base_name.startswith(f"{prefix}_"):
                return True
        return False

    def _is_known_namespace(self, namespace: str) -> bool:
        """Check if a namespace string has been registered."""
        return namespace in set(
            self._tool_to_plugin.values()
        )
