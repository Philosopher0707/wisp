"""Diagnostics router.

Handles diagnostics and sandbox status.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT
from wisp.server.routes.files import _resolve_path

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/diagnostics", dependencies=[Depends(verify_api_key)])
async def get_diagnostics(path: str):
    """Return LSP diagnostics for a specific file."""
    target = _resolve_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        from wisp.lsp.manager import get_lsp_manager
        lsp = get_lsp_manager(str(WORKSPACE_ROOT))
        diags = await asyncio.to_thread(lsp.get_diagnostics, str(target))
        return {"path": path, "diagnostics": diags, "count": len(diags)}
    except Exception as e:
        return {"path": path, "diagnostics": [], "count": 0, "error": str(e)}


@router.get("/api/sandbox/status", dependencies=[Depends(verify_api_key)])
async def sandbox_status():
    """Return current sandbox provider info."""
    from wisp.sandbox import get_sandbox
    sandbox = get_sandbox(str(WORKSPACE_ROOT))
    return {
        "type": sandbox.name,
        "available": sandbox.is_available(),
    }
