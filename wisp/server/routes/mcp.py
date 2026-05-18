"""MCP router.

Handles MCP server operations.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/mcp/servers")
async def list_mcp_servers():
    return {"servers": []}


@router.post("/api/mcp/servers")
async def create_mcp_server():
    return {"created": True}


@router.post("/api/mcp/servers/{name}/test")
async def test_mcp_server(name: str):
    return {"name": name, "tested": True}


@router.delete("/api/mcp/servers/{name}")
async def delete_mcp_server(name: str):
    return {"name": name, "deleted": True}
