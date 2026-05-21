"""Workspace router.

Handles workspace operations.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = Path(os.environ.get("WISP_WORKSPACE", "./workspace")).resolve()
WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

# Security: allow disabling workspace mutation via env var
_WORKSPACE_MUTABLE = os.environ.get("WISP_WORKSPACE_MUTABLE", "true").lower() == "true"

router = APIRouter()


class WorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)


@router.get("/api/workspace")
async def get_workspace():
    return {"path": str(WORKSPACE_ROOT)}


@router.post("/api/workspace", dependencies=[Depends(verify_api_key)])
async def set_workspace(req: WorkspaceRequest, request: Request):
    global WORKSPACE_ROOT

    if not _WORKSPACE_MUTABLE:
        raise HTTPException(status_code=403, detail="Workspace changes are disabled. Set WISP_WORKSPACE_MUTABLE=true to enable.")

    # Security: reject paths containing traversal sequences
    if ".." in req.path:
        raise HTTPException(status_code=400, detail="Directory traversal not allowed")

    new_root = Path(req.path).resolve()
    if not new_root.exists():
        raise HTTPException(status_code=400, detail="Directory does not exist")
    if not new_root.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")

    # Security: resolve the new workspace and verify it's not outside allowed boundaries
    import os
    allowed_roots_raw = os.environ.get("WISP_ALLOWED_WORKSPACE_ROOTS", str(WORKSPACE_ROOT))
    allowed_roots = [Path(p.strip()).resolve() for p in allowed_roots_raw.split(",")]
    if not any(
        new_root == allowed or str(new_root).startswith(str(allowed) + os.sep)
        for allowed in allowed_roots
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Workspace path must be within allowed roots: {allowed_roots_raw}"
        )

    WORKSPACE_ROOT = new_root
    # Update CompositionRoot config if available
    root = getattr(request.app.state, "root", None)
    if root is not None:
        root.config.workspace = str(new_root)
        root.runtime.invalidate_core_cache()
    logger.info("Workspace changed to %s", WORKSPACE_ROOT)
    return {"path": str(WORKSPACE_ROOT)}
