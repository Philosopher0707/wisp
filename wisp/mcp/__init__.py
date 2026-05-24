"""MCP (Model Context Protocol) integration — server discovery, lifecycle, tool execution."""

from wisp.mcp.manager import (
    MCPAuthMethod,
    MCPTool,
    MCPServerConfig,
    MCPServer,
    discover_mcp_configs,
    connect_server,
    disconnect_server,
    call_tool,
    MCPManager,
    get_mcp_manager,
    shutdown_global_mcp_manager,
)

__all__ = [
    "MCPAuthMethod",
    "MCPTool",
    "MCPServerConfig",
    "MCPServer",
    "discover_mcp_configs",
    "connect_server",
    "disconnect_server",
    "call_tool",
    "MCPManager",
    "get_mcp_manager",
    "shutdown_global_mcp_manager",
]
