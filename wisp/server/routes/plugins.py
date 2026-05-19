"""Plugins router.

Handles plugin operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/plugins", dependencies=[Depends(verify_api_key)])
async def list_plugins():
    return {"plugins": []}


@router.get("/api/plugins/marketplace", dependencies=[Depends(verify_api_key)])
async def plugin_marketplace():
    return {"plugins": []}


@router.post("/api/plugins/install", dependencies=[Depends(verify_api_key)])
async def install_plugin():
    return {"installed": True}


@router.post("/api/plugins/{name}/toggle", dependencies=[Depends(verify_api_key)])
async def toggle_plugin(name: str):
    return {"name": name, "toggled": True}


@router.delete("/api/plugins/{name}", dependencies=[Depends(verify_api_key)])
async def delete_plugin(name: str):
    return {"name": name, "deleted": True}
