"""Plugins router.

Handles plugin operations.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/plugins")
async def list_plugins():
    return {"plugins": []}


@router.get("/api/plugins/marketplace")
async def plugin_marketplace():
    return {"plugins": []}


@router.post("/api/plugins/install")
async def install_plugin():
    return {"installed": True}


@router.post("/api/plugins/{name}/toggle")
async def toggle_plugin(name: str):
    return {"name": name, "toggled": True}


@router.delete("/api/plugins/{name}")
async def delete_plugin(name: str):
    return {"name": name, "deleted": True}
