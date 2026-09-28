"""MCPExtension — wraps MCPManager for ExtensionHost.

Provides MCP server tools and lifecycle management.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MCPExtension:
    """Extension adapter for the MCP (Model Context Protocol) system."""

    name = "mcp"

    def __init__(self, workspace: str = ".", manager=None):
        self._workspace = workspace
        self._manager = manager  # can be injected for shared instance
        self._unadvertisable: set[str] = set()

    def start(self) -> None:
        """Start the MCP extension — load and connect configured servers."""
        try:
            from wisp.mcp import MCPManager
            if self._manager is None:
                self._manager = MCPManager(self._workspace)
            configs = self._manager.load_server_configs()
            for config in configs:
                if config.always_load:
                    try:
                        from wisp.mcp import connect_server
                        server = connect_server(config)
                        self._manager.servers.append(server)
                    except Exception as exc:
                        logger.warning("MCP server '%s' auto-connect failed: %s", config.name, exc)
            logger.debug("MCPExtension started with %d servers", len(self._manager.servers))
        except Exception as exc:
            logger.warning("MCPExtension start() failed: %s", exc)

    def stop(self) -> None:
        """Stop the MCP extension — disconnect all servers."""
        if self._manager is not None:
            for server in list(self._manager.servers):
                try:
                    from wisp.mcp import disconnect_server
                    disconnect_server(server)
                except Exception as exc:
                    logger.warning("MCP server disconnect failed: %s", exc)
            self._manager.servers.clear()
            self._manager = None
        logger.debug("MCPExtension stopped")

    def tools(self) -> list[dict]:
        """Return the connected servers' tools as the model sees them.

        Each tool is advertised under its wire name, ``mcp__server__tool``, the only form
        provider APIs accept as a function name; the executor maps it back to the canonical
        ``mcp:server/tool`` for policy, risk and dispatch. This used to test the server for a
        ``list_tools()`` method that ``MCPServer`` does not have, so no MCP tool ever reached
        the model.
        """
        if self._manager is None:
            return []
        tools: list[dict] = []
        seen: set[str] = set()
        for server in self._manager.servers:
            for tool in getattr(server, "tools", []):
                name = tool.wire_name()
                if name is None:
                    key = f"{tool.server_name}/{tool.name}"
                    if key not in self._unadvertisable:
                        self._unadvertisable.add(key)
                        logger.warning(
                            "MCP tool %r on server %r is not advertised: its name cannot be expressed as a "
                            "provider function name ([a-zA-Z0-9_-], at most 64, no '__' in the server name)",
                            tool.name, tool.server_name)
                    continue
                if name in seen:
                    continue
                seen.add(name)
                tools.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"[MCP/{tool.server_name}] {tool.description}",
                        "parameters": tool.input_schema or {"type": "object", "properties": {}},
                    },
                })
        return tools

    def intercept(self, event: dict) -> dict:
        """MCP doesn't block events by default."""
        return {"action": "allow"}
