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
        """Return tools exposed by MCP servers."""
        if self._manager is None:
            return []
        tools = []
        for server in self._manager.servers:
            try:
                if hasattr(server, "list_tools"):
                    server_tools = server.list_tools()
                    for tool in server_tools:
                        tools.append({
                            "type": "function",
                            "function": {
                                # Canonical MCP tool name (MCPTool.prefixed_name):
                                # mcp:server/tool — matches manager.call_tool dispatch.
                                "name": f"mcp:{server.config.name}/{tool.get('name', 'unknown')}",
                                "description": tool.get("description", ""),
                                "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                            },
                        })
            except Exception as exc:
                logger.warning("MCP server '%s' tools() failed: %s", getattr(server.config, "name", "?"), exc)
        return tools

    def intercept(self, event: dict) -> dict:
        """MCP doesn't block events by default."""
        return {"action": "allow"}
