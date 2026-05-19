"""MCP router.

Handles MCP server operations.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_mcp_manager = None


def _get_mcp_manager():
    global _mcp_manager
    if _mcp_manager is None:
        from wisp.mcp import MCPManager
        _mcp_manager = MCPManager(str(WORKSPACE_ROOT))
    return _mcp_manager


class MCPServerAddRequest(BaseModel):
    name: str = Field(..., min_length=1)
    command: str | None = None
    args: list[str] | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    transport: str = "stdio"
    always_load: bool = False
    auth: str = "none"
    auth_config: dict | None = None
    timeout_seconds: int = 30
    headers: dict[str, str] | None = None
    disabled_tools: list[str] | None = None


@router.get("/api/mcp/servers", dependencies=[Depends(verify_api_key)])
async def list_mcp_servers():
    manager = _get_mcp_manager()
    configs = manager.load_server_configs()
    return {
        "servers": [
            {
                "name": c.name,
                "command": c.command,
                "args": c.args,
                "url": c.url,
                "transport": c.transport,
                "always_load": c.always_load,
                "auth": c.auth.value if hasattr(c.auth, "value") else str(c.auth),
                "timeout_seconds": c.timeout_seconds,
                "headers": c.headers,
                "disabled_tools": c.disabled_tools,
                "env": c.env,
            }
            for c in configs
        ]
    }


@router.post("/api/mcp/servers", dependencies=[Depends(verify_api_key)])
async def add_mcp_server(req: MCPServerAddRequest):
    from wisp.mcp import MCPAuthMethod, MCPServerConfig
    manager = _get_mcp_manager()
    configs = manager.load_server_configs()
    existing = [c for c in configs if c.name == req.name]
    if existing:
        raise HTTPException(status_code=409, detail=f"MCP server '{req.name}' already exists")

    try:
        auth_method = MCPAuthMethod(req.auth)
    except ValueError:
        auth_method = MCPAuthMethod.NONE

    config = MCPServerConfig(
        name=req.name,
        command=req.command,
        args=req.args or [],
        url=req.url,
        env=req.env or {},
        transport=req.transport,
        always_load=req.always_load,
        auth=auth_method,
        auth_config=req.auth_config or {},
        timeout_seconds=req.timeout_seconds,
        headers=req.headers or {},
        disabled_tools=req.disabled_tools or [],
    )

    manager._server_configs[req.name] = config
    manager.save_server_configs()

    if req.always_load:
        try:
            from wisp.mcp import connect_server
            server = connect_server(config)
            manager.servers.append(server)
        except Exception as e:
            logger.warning("Failed to connect MCP server '%s' during add: %s", req.name, e)

    return {"ok": True, "server": {"name": req.name, "transport": req.transport}}


@router.post("/api/mcp/servers/{name}/test", dependencies=[Depends(verify_api_key)])
async def test_mcp_server(name: str):
    manager = _get_mcp_manager()
    configs = manager.load_server_configs()
    if not any(c.name == name for c in configs):
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    result = await manager.health_check(name)
    return {"ok": result["status"] == "ok", "health": result}


@router.delete("/api/mcp/servers/{name}", dependencies=[Depends(verify_api_key)])
async def delete_mcp_server(name: str):
    manager = _get_mcp_manager()
    configs = manager.load_server_configs()
    if not any(c.name == name for c in configs):
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")

    for server in list(manager.servers):
        if server.config.name == name:
            try:
                from wisp.mcp import disconnect_server
                disconnect_server(server)
            except Exception as e:
                logger.warning("Error disconnecting MCP server '%s': %s", name, e)
            manager.servers.remove(server)

    manager._server_configs.pop(name, None)
    manager.save_server_configs()
    return {"ok": True, "message": f"MCP server '{name}' deleted"}
