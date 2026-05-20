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

router = APIRouter()


class WorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)


@router.get("/api/workspace")
async def get_workspace():
    return {"path": str(WORKSPACE_ROOT)}


@router.post("/api/workspace", dependencies=[Depends(verify_api_key)])
async def set_workspace(req: WorkspaceRequest, request: Request):
    global WORKSPACE_ROOT
    new_root = Path(req.path).resolve()
    if not new_root.exists():
        raise HTTPException(status_code=400, detail="Directory does not exist")
    if not new_root.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory")
    WORKSPACE_ROOT = new_root
    # Update CompositionRoot config if available
    root = getattr(request.app.state, "root", None)
    if root is not None:
        root.config.workspace = str(new_root)
        root.runtime.invalidate_core_cache()
    logger.info("Workspace changed to %s", WORKSPACE_ROOT)
    return {"path": str(WORKSPACE_ROOT)}
