"""Plugins router.

Handles plugin operations.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key

logger = logging.getLogger(__name__)

router = APIRouter()

_plugin_registry = None


def _get_plugin_registry():
    global _plugin_registry
    if _plugin_registry is None:
        from wisp.plugins.registry import PluginRegistry
        _plugin_registry = PluginRegistry()
    return _plugin_registry


class PluginInstallRequest(BaseModel):
    path: str = Field(..., min_length=1)


class PluginToggleRequest(BaseModel):
    enable: bool = True


@router.get("/api/plugins", dependencies=[Depends(verify_api_key)])
async def list_plugins():
    registry = _get_plugin_registry()
    installed = registry.list_installed()
    state = registry._read_state()
    return {
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "author": p.author,
                "license": p.license,
                "namespace": p.namespace,
                "enabled": state.get(p.name, {}).get("enabled", True),
                "installed_at": state.get(p.name, {}).get("installed_at"),
            }
            for p in installed
        ]
    }


@router.get("/api/plugins/marketplace", dependencies=[Depends(verify_api_key)])
async def plugin_marketplace():
    return {
        "plugins": [],
        "message": "Marketplace not yet available. Install plugins locally via POST /api/plugins/install",
    }


@router.post("/api/plugins/install", dependencies=[Depends(verify_api_key)])
async def install_plugin(req: PluginInstallRequest):
    registry = _get_plugin_registry()
    plugin_path = Path(req.path).expanduser().resolve()
    if not plugin_path.exists():
        raise HTTPException(status_code=404, detail=f"Plugin path not found: {req.path}")
    if not plugin_path.is_dir():
        raise HTTPException(status_code=400, detail=f"Plugin path is not a directory: {req.path}")
    try:
        manifest = registry.install(plugin_path)
        return {
            "ok": True,
            "plugin": {
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "namespace": manifest.namespace,
            },
        }
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("Plugin install failed")
        raise HTTPException(status_code=500, detail=f"Install failed: {e}")


@router.post("/api/plugins/{name}/toggle", dependencies=[Depends(verify_api_key)])
async def toggle_plugin(name: str, req: PluginToggleRequest):
    registry = _get_plugin_registry()
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not installed")
    if req.enable:
        registry.enable(name)
    else:
        registry.disable(name)
    return {"ok": True, "plugin": name, "enabled": req.enable}


@router.delete("/api/plugins/{name}", dependencies=[Depends(verify_api_key)])
async def delete_plugin(name: str):
    registry = _get_plugin_registry()
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not installed")
    ok = registry.uninstall(name)
    if not ok:
        raise HTTPException(status_code=500, detail=f"Failed to uninstall plugin '{name}'")
    return {"ok": True, "message": f"Plugin '{name}' uninstalled"}
