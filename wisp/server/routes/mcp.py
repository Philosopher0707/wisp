"""MCP router.

Handles MCP server operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/mcp/servers", dependencies=[Depends(verify_api_key)])
async def list_mcp_servers():
    return {"servers": []}


@router.post("/api/mcp/servers", dependencies=[Depends(verify_api_key)])
async def create_mcp_server():
    return {"created": True}


@router.post("/api/mcp/servers/{name}/test", dependencies=[Depends(verify_api_key)])
async def test_mcp_server(name: str):
    return {"name": name, "tested": True}


@router.delete("/api/mcp/servers/{name}", dependencies=[Depends(verify_api_key)])
async def delete_mcp_server(name: str):
    return {"name": name, "deleted": True}
