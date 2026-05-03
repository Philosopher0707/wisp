"""MCP (Model Context Protocol) client for Wisp.

Connects to MCP servers (via stdio or HTTP) and exposes their tools
to the Wisp agent. MCP servers can provide additional capabilities
like database access, API integrations, file system operations, etc.

Config file: ~/.config/wisp/mcp.json or .wisp/mcp.json in workspace
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ── Data types ───────────────────────────────────────────────────────


@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: Optional[str] = None  # stdio server command
    args: list[str] = field(default_factory=list)
    url: Optional[str] = None  # HTTP server URL
    env: dict[str, str] = field(default_factory=dict)
    disabled: bool = False


@dataclass
class MCPServer:
    """A running MCP server connection."""

    config: MCPServerConfig
    tools: list[MCPTool] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None
    _next_id: int = 1

    def _next_request_id(self) -> int:
        self._next_id += 1
        return self._next_id


# ── Config loading ───────────────────────────────────────────────────


def discover_mcp_configs(workspace: str) -> list[MCPServerConfig]:
    """Discover MCP server configs from workspace and home directory.

    Checks (in priority order):
    1. .wisp/mcp.json in workspace
    2. ~/.config/wisp/mcp.json
    """
    configs: list[MCPServerConfig] = []
    seen_names: set[str] = set()

    paths_to_check = [
        Path(workspace) / ".wisp" / "mcp.json",
        Path.home() / ".config" / "wisp" / "mcp.json",
    ]

    for cfg_path in paths_to_check:
        if not cfg_path.exists():
            continue
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            servers = data if isinstance(data, list) else data.get("mcpServers", data.get("servers", []))
            for s in servers:
                name = s.get("name", "")
                if not name or name in seen_names:
                    continue
                if s.get("disabled", False):
                    continue
                seen_names.add(name)
                configs.append(MCPServerConfig(
                    name=name,
                    command=s.get("command"),
                    args=s.get("args", []),
                    url=s.get("url"),
                    env=s.get("env", {}),
                    disabled=s.get("disabled", False),
                ))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read MCP config %s: %s", cfg_path, e)

    return configs


# ── Server lifecycle ─────────────────────────────────────────────────


def connect_server(config: MCPServerConfig) -> MCPServer:
    """Connect to an MCP server and initialize it.

    For stdio servers, spawns the process and sends initialize request.
    For HTTP servers, sends initialize request via HTTP.
    """
    server = MCPServer(config=config)

    if config.command:
        _connect_stdio(server)
    elif config.url:
        _connect_http(server)
    else:
        raise ValueError(f"MCP server '{config.name}' has neither command nor url")

    # Initialize
    _send_request(server, "initialize", {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {
            "name": "wisp",
            "version": "0.1.0",
        },
    })

    # List tools
    response = _send_request(server, "tools/list", {})
    tools_data = response.get("tools", [])
    for t in tools_data:
        server.tools.append(MCPTool(
            name=t["name"],
            description=t.get("description", ""),
            input_schema=t.get("inputSchema", {}),
            server_name=config.name,
        ))

    logger.info(
        "Connected to MCP server '%s' (%d tools)",
        config.name, len(server.tools),
    )
    return server


def disconnect_server(server: MCPServer):
    """Disconnect from an MCP server."""
    if server.process:
        try:
            server.process.terminate()
            server.process.wait(timeout=5)
        except Exception as e:
            logger.warning("Failed to terminate MCP server '%s': %s", server.config.name, e)
            if server.process:
                try:
                    server.process.kill()
                except Exception:
                    pass
        server.process = None


def call_tool(server: MCPServer, tool_name: str, arguments: dict[str, Any]) -> str:
    """Call a tool on an MCP server and return the result as a string."""
    response = _send_request(server, "tools/call", {
        "name": tool_name,
        "arguments": arguments,
    })

    # MCP tool results can contain content items (text, images, resources, etc.)
    content = response.get("content", [])
    parts = []
    for item in content:
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif item.get("type") == "resource":
            parts.append(str(item.get("resource", {})))
        else:
            parts.append(str(item))

    is_error = response.get("isError", False)
    result = "\n".join(parts) if parts else "(no output)"

    if is_error:
        return f"[MCP Error: {result}]"
    return result


# ── JSON-RPC communication ───────────────────────────────────────────


def _connect_stdio(server: MCPServer):
    """Connect to a stdio-based MCP server by spawning its process."""
    env = {**server.config.env}
    if env:
        full_env = dict(subprocess.os.environ)
        full_env.update(env)
        env = full_env
    else:
        env = None

    server.process = subprocess.Popen(
        [server.config.command] + server.config.args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )


def _connect_http(server: MCPServer):
    """Connect to an HTTP-based MCP server."""
    # HTTP MCP servers use SSE for streaming, but for simplicity
    # we use the POST-based JSON-RPC endpoint
    pass  # Connection is stateless for HTTP


def _send_request(server: MCPServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send a JSON-RPC request to an MCP server and return the response."""
    request_id = server._next_request_id()
    request = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }

    if server.config.url:
        return _send_http_request(server.config.url, request)
    elif server.process:
        return _send_stdio_request(server.process, request)
    else:
        raise RuntimeError(f"MCP server '{server.config.name}' is not connected")


def _send_stdio_request(process: subprocess.Popen, request: dict) -> dict:
    """Send a JSON-RPC request via stdin/stdout."""
    request_str = json.dumps(request) + "\n"
    process.stdin.write(request_str)
    process.stdin.flush()

    response_line = process.stdout.readline()
    if not response_line:
        # Check stderr for errors
        stderr_output = ""
        try:
            stderr_output = process.stderr.read()
        except Exception:
            pass
        raise RuntimeError(
            f"MCP server process died. Stderr: {stderr_output[:500]}"
        )

    response = json.loads(response_line)

    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"MCP error: {error.get('message', str(error))}")

    return response.get("result", {})


def _send_http_request(url: str, request: dict) -> dict:
    """Send a JSON-RPC request via HTTP POST."""
    resp = requests.post(
        url,
        json=request,
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    response = resp.json()

    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"MCP error: {error.get('message', str(error))}")

    return response.get("result", {})


# ── Integration with Wisp ────────────────────────────────────────────


class MCPManager:
    """Manages MCP server connections and exposes their tools to Wisp."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.servers: list[MCPServer] = []
        self._initialized = False

    def initialize(self):
        """Discover and connect to all MCP servers."""
        if self._initialized:
            return

        configs = discover_mcp_configs(self.workspace)
        for config in configs:
            try:
                server = connect_server(config)
                self.servers.append(server)
            except Exception as e:
                logger.error("Failed to connect MCP server '%s': %s", config.name, e)

        self._initialized = True
        logger.info("MCP: %d server(s) connected", len(self.servers))

    def shutdown(self):
        """Disconnect all MCP servers."""
        for server in self.servers:
            try:
                disconnect_server(server)
            except Exception as e:
                logger.warning("Error disconnecting MCP server '%s': %s", server.config.name, e)
        self.servers.clear()
        self._initialized = False

    def get_all_tools(self) -> list[MCPTool]:
        """Get all tools from all connected MCP servers."""
        self.initialize()
        tools = []
        for server in self.servers:
            tools.extend(server.tools)
        return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool by name across all connected servers.

        Searches all servers for a tool with the given name.
        """
        self.initialize()
        for server in self.servers:
            for tool in server.tools:
                if tool.name == tool_name:
                    return call_tool(server, tool_name, arguments)
        raise ValueError(f"MCP tool '{tool_name}' not found on any connected server")

    def get_tool_schemas(self) -> list[dict]:
        """Convert MCP tools to Ollama-compatible tool schemas."""
        self.initialize()
        schemas = []
        for server in self.servers:
            for tool in server.tools:
                schema = {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": f"[MCP/{tool.server_name}] {tool.description}",
                        "parameters": tool.input_schema,
                    },
                }
                schemas.append(schema)
        return schemas
