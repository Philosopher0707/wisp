"""MCP (Model Context Protocol) client for Wisp.

Connects to MCP servers (via stdio or HTTP) and exposes their tools
to the Wisp agent. MCP servers can provide additional capabilities
like database access, API integrations, file system operations, etc.

Config file: ~/.config/wisp/mcp.json or .wisp/mcp.json in workspace
"""

import asyncio
import json
import logging
import os
import stat
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ── Data types ───────────────────────────────────────────────────────


class MCPAuthMethod(Enum):
    """Authentication method for MCP server connections."""

    NONE = "none"
    BEARER_TOKEN = "bearer_token"
    OAUTH_CLIENT_CREDENTIALS = "oauth_client_credentials"
    X509_CERTIFICATE = "x509_certificate"


@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str
    def prefixed_name(self) -> str:
        """Canonical prefixed name: mcp:server/name."""
        return f"mcp:{self.server_name}/{self.name}"


# Set of built-in tool names that an MCP tool must NOT shadow.
_SHADOW_BUILTIN_TOOLS: frozenset[str] = frozenset({
    "read_file", "write_file", "edit_file", "edit_file_multi",
    "run_bash", "list_files", "web_fetch", "web_search",
    "search_symbols", "search_codebase",
    "remember", "recall",
    "spawn_subagent",
    "git_status", "git_diff", "git_branch", "git_commit", "git_push",
    "gh_pr_create",
    "lsp_diagnostics", "lsp_definition", "lsp_references",
    "lsp_hover", "lsp_symbols",
    "diagnose", "run_tests",
    "plan_task", "mark_step_done", "update_plan",
})


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: Optional[str] = None  # stdio server command
    args: list[str] = field(default_factory=list)
    url: Optional[str] = None  # HTTP server URL
    env: dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    # ── New fields (backward compatible) ──
    transport: str = "stdio"  # "stdio" | "sse" | "streamable-http"
    always_load: bool = False  # auto-connect on agent start
    auth: MCPAuthMethod = MCPAuthMethod.NONE
    auth_config: Optional[dict[str, Any]] = None
    timeout_seconds: int = 30
    headers: Optional[dict[str, str]] = None
    disabled_tools: Optional[list[str]] = None  # tools to exclude


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

    from wisp.trust import WorkspaceTrustManager

    paths_to_check = []
    workspace_mcp_path = Path(workspace) / ".wisp" / "mcp.json"
    if workspace_mcp_path.exists():
        if WorkspaceTrustManager.is_workspace_trusted(workspace):
            paths_to_check.append(workspace_mcp_path)
        else:
            logger.warning(
                "Skipping loading workspace-local MCP server configuration because the workspace is untrusted: %s. "
                "To trust this workspace, add its path to trusted_workspaces.json.",
                workspace
            )

    paths_to_check.append(Path.home() / ".config" / "wisp" / "mcp.json")

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

                # Parse auth method
                auth_raw = s.get("auth", "none")
                try:
                    auth_method = MCPAuthMethod(auth_raw)
                except ValueError:
                    logger.warning(
                        "Unknown auth method '%s' for server '%s', defaulting to none",
                        auth_raw, name,
                    )
                    auth_method = MCPAuthMethod.NONE

                configs.append(MCPServerConfig(
                    name=name,
                    command=s.get("command"),
                    args=s.get("args", []),
                    url=s.get("url"),
                    env=s.get("env", {}),
                    disabled=s.get("disabled", False),
                    transport=s.get("transport", "stdio"),
                    always_load=s.get("always_load", s.get("alwaysLoad", False)),
                    auth=auth_method,
                    auth_config=s.get("auth_config", s.get("authConfig")),
                    timeout_seconds=s.get("timeout_seconds", s.get("timeoutSeconds", 30)),
                    headers=s.get("headers"),
                    disabled_tools=s.get("disabled_tools", s.get("disabledTools")),
                ))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read MCP config %s: %s", cfg_path, e)

    return configs


# ── Token storage ────────────────────────────────────────────────────

_TOKEN_STORE_PATH = Path.home() / ".config" / "wisp" / "mcp_tokens.json"
_TOKEN_LOCK = threading.Lock()


def _ensure_token_dir() -> None:
    """Create token directory with restricted permissions if it doesn't exist."""
    token_dir = _TOKEN_STORE_PATH.parent
    token_dir.mkdir(parents=True, exist_ok=True)
    # Restrict directory permissions to owner only
    try:
        os.chmod(token_dir, stat.S_IRWXU)
    except OSError:
        pass


def _read_tokens() -> dict[str, Any]:
    """Read stored tokens from disk. Returns empty dict if no tokens exist."""
    _ensure_token_dir()
    if not _TOKEN_STORE_PATH.exists():
        return {}
    try:
        with open(_TOKEN_STORE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read MCP tokens: %s", e)
        return {}


def _write_tokens(tokens: dict[str, Any]) -> None:
    """Write tokens to disk with restricted permissions (0600)."""
    _ensure_token_dir()
    try:
        tmp_path = _TOKEN_STORE_PATH.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(tokens, f, indent=2)
        os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
        tmp_path.replace(_TOKEN_STORE_PATH)
    except OSError as e:
        logger.error("Failed to write MCP tokens: %s", e)


def _get_stored_token(server_name: str) -> Optional[dict[str, Any]]:
    """Get a stored token entry for a server."""
    with _TOKEN_LOCK:
        tokens = _read_tokens()
        entry = tokens.get(server_name)
        if entry is None:
            return None
        # Check expiry
        expires_at = entry.get("expires_at")
        if expires_at:
            expiry = datetime.fromisoformat(expires_at)
            if datetime.now(timezone.utc) > expiry:
                logger.debug("Token for '%s' has expired", server_name)
                return None
        return entry


def _store_token(server_name: str, token_data: dict[str, Any]) -> None:
    """Store a token entry for a server."""
    with _TOKEN_LOCK:
        tokens = _read_tokens()
        tokens[server_name] = token_data
        _write_tokens(tokens)


def _clear_token(server_name: str) -> None:
    """Remove a stored token for a server."""
    with _TOKEN_LOCK:
        tokens = _read_tokens()
        tokens.pop(server_name, None)
        _write_tokens(tokens)


# ── OAuth helpers ────────────────────────────────────────────────────


def _resolve_auth_headers(config: MCPServerConfig) -> dict[str, str]:
    """Build authentication headers based on the server's auth config."""
    headers: dict[str, str] = {}

    if config.headers:
        headers.update(config.headers)

    if config.auth == MCPAuthMethod.NONE:
        return headers

    if config.auth == MCPAuthMethod.BEARER_TOKEN:
        token = _resolve_bearer_token(config)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    if config.auth == MCPAuthMethod.OAUTH_CLIENT_CREDENTIALS:
        token = _resolve_oauth_token(config)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    return headers


def _resolve_bearer_token(config: MCPServerConfig) -> Optional[str]:
    """Resolve a bearer token from stored tokens or auth_config."""
    # Check stored token first
    stored = _get_stored_token(config.name)
    if stored:
        return stored.get("access_token")

    # Fall back to config-provided token
    if config.auth_config:
        return config.auth_config.get("token")
    return None


def _resolve_oauth_token(config: MCPServerConfig) -> Optional[str]:
    """Resolve an OAuth2 client credentials token, refreshing if needed."""
    if not config.auth_config:
        logger.warning("OAuth config missing for server '%s'", config.name)
        return None

    # Check stored token
    stored = _get_stored_token(config.name)
    if stored and stored.get("access_token"):
        return stored["access_token"]

    # Perform client credentials flow
    token_url = config.auth_config.get("token_url")
    client_id = config.auth_config.get("client_id")
    client_secret = config.auth_config.get("client_secret")
    scopes = config.auth_config.get("scopes", [])

    if not token_url or not client_id or not client_secret:
        logger.error(
            "Incomplete OAuth config for server '%s': need token_url, client_id, client_secret",
            config.name,
        )
        return None

    try:
        scope_str = " ".join(scopes) if scopes else ""
        data: dict[str, str] = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scope_str:
            data["scope"] = scope_str

        resp = requests.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=config.timeout_seconds,
        )
        resp.raise_for_status()
        token_response = resp.json()

        access_token = token_response.get("access_token")
        if not access_token:
            logger.error("OAuth token response missing access_token for '%s'", config.name)
            return None

        expires_in = token_response.get("expires_in", 3600)
        expires_at = datetime.now(timezone.utc).timestamp() + expires_in

        # Store token
        token_data = {
            "access_token": access_token,
            "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
            "token_type": token_response.get("token_type", "bearer"),
        }
        if "refresh_token" in token_response:
            token_data["refresh_token"] = token_response["refresh_token"]

        _store_token(config.name, token_data)
        logger.info("OAuth token acquired for server '%s'", config.name)
        return access_token

    except (requests.RequestException, json.JSONDecodeError) as e:
        logger.error("OAuth token request failed for '%s': %s", config.name, e)
        return None


def _resolve_cert_paths(config: MCPServerConfig) -> tuple[Optional[str], Optional[str]]:
    """Resolve X.509 certificate and key paths from auth_config."""
    if not config.auth_config:
        return None, None
    cert = config.auth_config.get("cert_path") or config.auth_config.get("cert")
    key = config.auth_config.get("key_path") or config.auth_config.get("key")
    return cert, key


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
    disabled_set = set(config.disabled_tools) if config.disabled_tools else set()
    for t in tools_data:
        if t["name"] in disabled_set:
            logger.debug(
                "Skipping disabled tool '%s' on server '%s'",
                t["name"], config.name,
            )
            continue
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
    env = None
    if server.config.env:
        full_env = dict(subprocess.os.environ)
        full_env.update(server.config.env)
        env = full_env

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


async def _send_request_async(server: MCPServer, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Thread-safe variant of _send_request for use inside async contexts."""
    return await asyncio.to_thread(_send_request, server, method, params)


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
        return _send_http_request(server.config, request)
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

    try:
        response = json.loads(response_line)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"MCP server returned invalid JSON: {response_line[:200]}"
        ) from e

    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"MCP error: {error.get('message', str(error))}")

    return response.get("result", {})


def _send_http_request(config: MCPServerConfig, request: dict) -> dict:
    """Send a JSON-RPC request via HTTP POST with authentication support."""
    headers = {"Content-Type": "application/json"}

    # Add auth headers
    auth_headers = _resolve_auth_headers(config)
    headers.update(auth_headers)

    # Add X.509 mTLS support
    cert_arg = None
    if config.auth == MCPAuthMethod.X509_CERTIFICATE:
        cert_path, key_path = _resolve_cert_paths(config)
        if cert_path:
            cert_arg = (cert_path, key_path) if key_path else cert_path

    resp = requests.post(
        config.url,
        json=request,
        headers=headers,
        timeout=config.timeout_seconds,
        cert=cert_arg,
    )
    resp.raise_for_status()
    response = resp.json()

    if "error" in response:
        error = response["error"]
        raise RuntimeError(f"MCP error: {error.get('message', str(error))}")

    return response.get("result", {})


# ── Module-level singleton (prevents process multiplication) ──────────────────

_GLOBAL_MCP: Optional["MCPManager"] = None
_GLOBAL_MCP_LOCK = threading.Lock()


def get_mcp_manager(workspace: str) -> "MCPManager":
    """Return the module-level singleton MCPManager, creating it if needed.

    This avoids spawning N copies of every MCP server process when multiple
    agents share the same workspace (e.g. subagents in a swarm).
    """
    global _GLOBAL_MCP
    with _GLOBAL_MCP_LOCK:
        if _GLOBAL_MCP is None or _GLOBAL_MCP.workspace != workspace:
            if _GLOBAL_MCP is not None:
                logger.debug(
                    "MCPManager workspace changed (%s -> %s) — shutting down old.",
                    _GLOBAL_MCP.workspace,
                    workspace,
                )
                try:
                    _GLOBAL_MCP.shutdown()
                except Exception:
                    pass
            _GLOBAL_MCP = MCPManager(workspace)
            _GLOBAL_MCP.initialize()
            logger.info("MCPManager singleton created for workspace: %s", workspace)
        return _GLOBAL_MCP


def shutdown_global_mcp_manager() -> None:
    """Shut down the module-level singleton MCPManager.

    Safe to call multiple times (idempotent). Intended for FastAPI
    lifespan teardown, agent ``close()``, and ``atexit`` handlers.
    """
    global _GLOBAL_MCP
    with _GLOBAL_MCP_LOCK:
        if _GLOBAL_MCP is not None:
            try:
                _GLOBAL_MCP.shutdown()
            except Exception:
                pass
            _GLOBAL_MCP = None


# ── Safety: ensure child processes are killed on interpreter exit ──

import atexit
atexit.register(shutdown_global_mcp_manager)


# ── Integration with Wisp ────────────────────────────────────────────


class MCPManager:
    """Manages MCP server connections and exposes their tools to Wisp."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self.servers: list[MCPServer] = []
        self._initialized = False
        self._server_configs: dict[str, MCPServerConfig] = {}

    # ── Existing methods ─────────────────────────────────────────────

    def initialize(self):
        """Discover and connect to all MCP servers."""
        if self._initialized:
            return

        configs = discover_mcp_configs(self.workspace)
        for config in configs:
            try:
                server = connect_server(config)
                self.servers.append(server)
                self._server_configs[config.name] = config
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

        Accepts plain tool names (legacy) or the canonical prefixed form
        ``mcp:server_name/tool_name``.
        """
        self.initialize()
        # Canonical form: mcp:server/tool
        if tool_name.startswith("mcp:"):
            _, rest = tool_name.split(":", 1)
            if "/" in rest:
                target_server, bare_name = rest.split("/", 1)
                for server in self.servers:
                    if server.config.name == target_server:
                        for tool in server.tools:
                            if tool.name == bare_name:
                                return call_tool(server, bare_name, arguments)
                raise ValueError(
                    f"MCP tool '{tool_name}' not found on server '{target_server}'"
                )
        # Legacy plain-name search (deprecated, kept for compatibility)
        for server in self.servers:
            for tool in server.tools:
                if tool.name == tool_name:
                    logger.warning(
                        "MCP tool '%s' called without prefix — prefer '%s'",
                        tool_name,
                        tool.prefixed_name(),
                    )
                    return call_tool(server, tool_name, arguments)
        raise ValueError(f"MCP tool '{tool_name}' not found on any connected server")

    def get_tool_schemas(self) -> list[dict]:
        """Convert MCP tools to Ollama-compatible tool schemas.

        Every MCP tool name is prefixed with ``mcp:server_name/`` so it
        cannot collide with built-in tools.
        """
        self.initialize()
        schemas: list[dict] = []
        for server in self.servers:
            for tool in server.tools:
                # Warn if the bare name shadows a built-in (user won't see
                # the collision because we prefix, but they should know).
                if tool.name in _SHADOW_BUILTIN_TOOLS:
                    logger.warning(
                        "MCP server '%s' exposes tool '%s' which shadows a built-in. "
                        "Invoked via '%s' instead.",
                        tool.server_name,
                        tool.name,
                        tool.prefixed_name(),
                    )
                name = tool.prefixed_name()
                schema = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"[MCP/{tool.server_name}] {tool.description}",
                        "parameters": tool.input_schema,
                    },
                }
                schemas.append(schema)
        return schemas

    # ── alwaysLoad servers ───────────────────────────────────────────

    async def connect_always_load_servers(self):
        """Connect to all servers with always_load=True.

        Loads persisted server configs and connects to every server
        that has always_load enabled. Call this during agent startup
        before the agent begins processing requests.
        """
        configs = self.load_server_configs()
        connected_count = 0

        for config in configs:
            if not config.always_load:
                continue
            if config.disabled:
                logger.debug(
                    "Skipping always_load server '%s': disabled", config.name
                )
                continue

            # Skip if already connected
            existing = self._get_server_by_name(config.name)
            if existing is not None:
                logger.debug(
                    "always_load server '%s' already connected", config.name
                )
                continue

            try:
                server = await asyncio.to_thread(connect_server, config)
                self.servers.append(server)
                self._server_configs[config.name] = config
                connected_count += 1
                logger.info(
                    "always_load: connected to '%s' (%d tools)",
                    config.name, len(server.tools),
                )
            except Exception as e:
                logger.error(
                    "Failed to connect always_load server '%s': %s",
                    config.name, e,
                )

        if connected_count > 0:
            logger.info(
                "always_load: %d server(s) auto-connected", connected_count
            )
        self._initialized = bool(self.servers)

    # ── Server health management ─────────────────────────────────────

    async def health_check(self, server_name: str) -> dict[str, Any]:
        """Check server health: latency, tool count, status.

        Returns:
            {"status": "ok"|"degraded"|"down", "latency_ms": float, "tool_count": int}
        """
        server = self._get_server_by_name(server_name)
        if server is None:
            return {
                "status": "down",
                "latency_ms": 0.0,
                "tool_count": 0,
                "error": f"Server '{server_name}' not found",
            }

        try:
            start = time.monotonic()
            response = await _send_request_async(server, "tools/list", {})
            elapsed_ms = (time.monotonic() - start) * 1000

            tools_data = response.get("tools", [])
            tool_count = len(tools_data)

            status = "ok" if elapsed_ms < 5000 else "degraded"

            logger.debug(
                "Health check '%s': status=%s, latency=%.1fms, tools=%d",
                server_name, status, elapsed_ms, tool_count,
            )
            return {
                "status": status,
                "latency_ms": round(elapsed_ms, 1),
                "tool_count": tool_count,
            }
        except Exception as e:
            logger.warning("Health check failed for '%s': %s", server_name, e)
            return {
                "status": "down",
                "latency_ms": 0.0,
                "tool_count": 0,
                "error": str(e),
            }

    async def auto_retry(
        self,
        server_name: str,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ):
        """Auto-retry failed MCP calls with exponential backoff.

        This is a decorator-style helper. Call it before tool invocations
        that need retry logic. It will attempt to reconnect and retry
        the server on failure.

        Args:
            server_name: Name of the MCP server.
            max_retries: Maximum number of retry attempts.
            backoff_base: Base seconds for exponential backoff (base * 2^attempt).
        """
        server = self._get_server_by_name(server_name)
        if server is None:
            raise ValueError(f"MCP server '{server_name}' not found")

        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                await _send_request_async(server, "tools/list", {})
                logger.debug(
                    "Retry attempt %d for '%s' succeeded",
                    attempt, server_name,
                )
                return
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    delay = backoff_base * (2 ** attempt)
                    logger.warning(
                        "Retry %d/%d for '%s' failed: %s. Waiting %.1fs...",
                        attempt + 1, max_retries, server_name, e, delay,
                    )
                    await asyncio.sleep(delay)
                    # Attempt reconnection
                    try:
                        await self.reconnect_server(server_name)
                    except Exception as reconnect_err:
                        logger.error(
                            "Reconnect failed during retry for '%s': %s",
                            server_name, reconnect_err,
                        )

        raise RuntimeError(
            f"MCP server '{server_name}' failed after {max_retries} retries"
        ) from last_error

    async def reconnect_server(self, server_name: str):
        """Force reconnect to an MCP server after failure.

        Disconnects the existing server instance, clears stale state,
        and establishes a fresh connection.
        """
        old_server = self._get_server_by_name(server_name)

        if old_server is not None:
            logger.info("Reconnecting to MCP server '%s'...", server_name)
            # Remove from servers list and disconnect
            self.servers = [s for s in self.servers if s.config.name != server_name]
            try:
                disconnect_server(old_server)
            except Exception as e:
                logger.warning(
                    "Error during disconnect for reconnect of '%s': %s",
                    server_name, e,
                )

        # Reconnect using stored config or discover fresh
        config = self._server_configs.get(server_name)
        if config is None:
            # Try to discover from disk
            configs = discover_mcp_configs(self.workspace)
            for c in configs:
                if c.name == server_name:
                    config = c
                    break

        if config is None:
            raise ValueError(
                f"Cannot reconnect server '{server_name}': no config found"
            )

        try:
            server = await asyncio.to_thread(connect_server, config)
            self.servers.append(server)
            self._server_configs[config.name] = config
            logger.info(
                "Reconnected to MCP server '%s' (%d tools)",
                server_name, len(server.tools),
            )
        except Exception as e:
            logger.error(
                "Failed to reconnect MCP server '%s': %s", server_name, e,
            )
            raise

    # ── Tool elicitation (search deferral) ───────────────────────────

    async def search_tools(
        self, query: str, server_name: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Search for tools across all connected MCP servers matching a query.

        Without this, the agent only knows tools listed at startup.
        This allows runtime discovery of tools by semantic keyword match.

        Args:
            query: Search query to match against tool names and descriptions.
            server_name: Optional server to limit search to. If None, searches all.

        Returns:
            List of matching tools with their metadata.
        """
        self.initialize()
        query_lower = query.lower()
        results: list[dict[str, Any]] = []

        servers_to_search = self.servers
        if server_name:
            server = self._get_server_by_name(server_name)
            servers_to_search = [server] if server else []

        for server in servers_to_search:
            for tool in server.tools:
                # Match against name and description
                name_match = query_lower in tool.name.lower()
                desc_match = query_lower in tool.description.lower()

                if name_match or desc_match:
                    results.append({
                        "name": tool.name,
                        "description": tool.description,
                        "server_name": tool.server_name,
                        "input_schema": tool.input_schema,
                        "match_type": "name" if name_match else "description",
                    })

        # Sort: name matches first, then by length of description (more specific)
        results.sort(key=lambda r: (
            0 if r["match_type"] == "name" else 1,
            len(r["description"]),
        ))

        logger.debug(
            "Tool search for '%s' returned %d result(s)", query, len(results),
        )
        return results

    async def list_all_tools(self) -> dict[str, list[dict[str, Any]]]:
        """Get all tools from all connected servers with metadata.

        Returns:
            {server_name: [{name, description, schema}]}
        """
        self.initialize()
        result: dict[str, list[dict[str, Any]]] = {}

        for server in self.servers:
            tools_list: list[dict[str, Any]] = []
            for tool in server.tools:
                tools_list.append({
                    "name": tool.name,
                    "description": tool.description,
                    "schema": tool.input_schema,
                })
            result[server.config.name] = tools_list

        logger.debug(
            "list_all_tools: %d server(s), %d total tool(s)",
            len(result), sum(len(t) for t in result.values()),
        )
        return result

    # ── Server config persistence ────────────────────────────────────

    def save_server_configs(self):
        """Save MCP server configs to ~/.config/wisp/mcp_servers.json.

        Persists the current server configurations so they can be
        reloaded on subsequent agent sessions, including always_load
        preferences, auth settings, and disabled tools.
        """
        config_dir = Path.home() / ".config" / "wisp"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / "mcp_servers.json"

        configs_data: list[dict[str, Any]] = []
        for config in self._server_configs.values():
            entry: dict[str, Any] = {
                "name": config.name,
                "command": config.command,
                "args": config.args,
                "url": config.url,
                "env": config.env,
                "disabled": config.disabled,
                "transport": config.transport,
                "always_load": config.always_load,
                "auth": config.auth.value,
                "auth_config": config.auth_config,
                "timeout_seconds": config.timeout_seconds,
                "headers": config.headers,
                "disabled_tools": config.disabled_tools,
            }
            configs_data.append(entry)

        try:
            tmp_path = config_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(configs_data, f, indent=2)
            tmp_path.replace(config_path)
            logger.info(
                "Saved %d MCP server config(s) to %s",
                len(configs_data), config_path,
            )
        except OSError as e:
            logger.error("Failed to save MCP server configs: %s", e)

    def load_server_configs(self) -> list[MCPServerConfig]:
        """Load server configs from config file or .wisp/mcp.json in workspace.

        Checks (in priority order):
        1. In-memory _server_configs (already loaded this session)
        2. ~/.config/wisp/mcp_servers.json (persisted configs)
        3. .wisp/mcp.json in workspace (project-specific)
        4. ~/.config/wisp/mcp.json (user-global)

        Returns:
            List of MCPServerConfig objects.
        """
        # If we already have configs in memory, return those
        if self._server_configs:
            return list(self._server_configs.values())

        configs: list[MCPServerConfig] = []
        seen_names: set[str] = set()

        paths_to_check = [
            Path.home() / ".config" / "wisp" / "mcp_servers.json",
            Path(self.workspace) / ".wisp" / "mcp.json",
            Path.home() / ".config" / "wisp" / "mcp.json",
        ]

        for cfg_path in paths_to_check:
            if not cfg_path.exists():
                continue
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                servers = (
                    data if isinstance(data, list)
                    else data.get("mcpServers", data.get("servers", []))
                )
                for s in servers:
                    name = s.get("name", "")
                    if not name or name in seen_names:
                        continue
                    seen_names.add(name)

                    auth_raw = s.get("auth", "none")
                    try:
                        auth_method = MCPAuthMethod(auth_raw)
                    except ValueError:
                        auth_method = MCPAuthMethod.NONE

                    config = MCPServerConfig(
                        name=name,
                        command=s.get("command"),
                        args=s.get("args", []),
                        url=s.get("url"),
                        env=s.get("env", {}),
                        disabled=s.get("disabled", False),
                        transport=s.get("transport", "stdio"),
                        always_load=s.get("always_load", s.get("alwaysLoad", False)),
                        auth=auth_method,
                        auth_config=s.get("auth_config", s.get("authConfig")),
                        timeout_seconds=s.get("timeout_seconds", s.get("timeoutSeconds", 30)),
                        headers=s.get("headers"),
                        disabled_tools=s.get("disabled_tools", s.get("disabledTools")),
                    )
                    configs.append(config)

            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "Failed to read MCP config %s: %s", cfg_path, e,
                )

        logger.debug("Loaded %d MCP server config(s)", len(configs))
        return configs

    # ── Event hooks for MCP ──────────────────────────────────────────

    async def on_tool_call(
        self, server_name: str, tool_name: str, args: dict[str, Any],
    ):
        """Called before every MCP tool call. Override for logging/monitoring.

        Args:
            server_name: Name of the MCP server being called.
            tool_name: Name of the tool being invoked.
            args: Arguments passed to the tool.
        """
        logger.debug(
            "MCP tool call: server=%s tool=%s args=%s",
            server_name, tool_name, json.dumps(args, default=str)[:500],
        )

    async def on_tool_result(
        self,
        server_name: str,
        tool_name: str,
        result: str,
        duration_ms: float,
    ):
        """Called after every MCP tool result. Override for logging/monitoring.

        Args:
            server_name: Name of the MCP server that was called.
            tool_name: Name of the tool that was invoked.
            result: The result string returned by the tool.
            duration_ms: Time the tool call took in milliseconds.
        """
        result_preview = result[:200] + "..." if len(result) > 200 else result
        logger.debug(
            "MCP tool result: server=%s tool=%s duration=%.1fms result=%s",
            server_name, tool_name, duration_ms, result_preview,
        )

    # ── Internal helpers ─────────────────────────────────────────────

    def _get_server_by_name(self, server_name: str) -> Optional[MCPServer]:
        """Find a connected server by name."""
        for server in self.servers:
            if server.config.name == server_name:
                return server
        return None


# ── Public API ───────────────────────────────────────────────────────

__all__ = [
    # Data types
    "MCPAuthMethod",
    "MCPTool",
    "MCPServerConfig",
    "MCPServer",
    # Config
    "discover_mcp_configs",
    # Lifecycle
    "connect_server",
    "disconnect_server",
    "call_tool",
    # Manager
    "MCPManager",
]
