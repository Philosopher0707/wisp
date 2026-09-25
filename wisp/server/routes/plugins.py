"""Plugins router.

Handles plugin operations.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.server.deps import require_tool_allowed, verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

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
async def install_plugin(req: PluginInstallRequest, request: Request):
    # Authority: installing a plugin activates third-party code that runs
    # in-process, so it is an executable-config mutation. It passes the same
    # policy gate as file writes; READ_ONLY sessions cannot install one. The
    # desktop client keeps working because the policy allows this action in
    # full / auto_edit / ask_all and denies only in read_only.
    require_tool_allowed(request, "plugins.install", {"path": req.path},
                         str(WORKSPACE_ROOT))
    registry = _get_plugin_registry()
    raw_path = Path(req.path).expanduser()

    # Security: reject absolute paths and paths that escape the workspace
    if raw_path.is_absolute():
        raise HTTPException(status_code=400, detail="Plugin path must be relative to the workspace")
    # Resolve within workspace boundaries
    plugin_path = (WORKSPACE_ROOT / raw_path).resolve()
    try:
        plugin_path.relative_to(WORKSPACE_ROOT.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Plugin path must be inside the workspace")

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
    except Exception:
        logger.exception("Plugin install failed")
        raise HTTPException(status_code=500, detail="Install failed")


@router.post("/api/plugins/{name}/toggle", dependencies=[Depends(verify_api_key)])
async def toggle_plugin(name: str, req: PluginToggleRequest, request: Request):
    # Enabling a plugin turns its code on in-process — the same authority
    # class as installing one, and the obvious bypass if install were gated
    # alone.
    require_tool_allowed(request, "plugins.toggle", {"name": name},
                         str(WORKSPACE_ROOT))
    registry = _get_plugin_registry()
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not installed")
    if req.enable:
        registry.enable(name)
    else:
        registry.disable(name)
    return {"ok": True, "plugin": name, "enabled": req.enable}


@router.delete("/api/plugins/{name}", dependencies=[Depends(verify_api_key)])
async def delete_plugin(name: str, request: Request):
    # Symmetric with install_plugin: uninstalling removes executable config.
    # Leaving this ungated would let a READ_ONLY session destroy what it
    # cannot create — an asymmetry with no security benefit.
    require_tool_allowed(request, "plugins.uninstall", {"name": name},
                         str(WORKSPACE_ROOT))
    registry = _get_plugin_registry()
    if not registry.get(name):
        raise HTTPException(status_code=404, detail=f"Plugin '{name}' not installed")
    ok = registry.uninstall(name)
    if not ok:
        raise HTTPException(status_code=500, detail=f"Failed to uninstall plugin '{name}'")
    return {"ok": True, "message": f"Plugin '{name}' uninstalled"}
