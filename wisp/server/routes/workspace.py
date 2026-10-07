"""Workspace router.

Handles workspace operations.
"""

import logging
import os
import sys
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

# Route modules that lazily build something bound to the workspace (an index, a manager, a watcher). After a switch they must
# be rebuilt for the new folder, so each cache is cleared and rebuilds on its next use.
_WORKSPACE_BOUND_CACHES = {
    "wisp.server.routes.codebase": "_semantic_index",
    "wisp.server.routes.mcp": "_mcp_manager",
    "wisp.server.routes.suggestions": "_app_suggestion_watcher",
}


def _rebind_workspace_root(old: Path, new: Path) -> int:
    """Point every server module at the new workspace.

    About twenty route modules did `from ...workspace import WORKSPACE_ROOT`, which copies the Path at import time, so
    reassigning it here never reached them: after a switch, Diff, Files, git and the shell routes kept operating on the
    OLD folder. Rebinding by identity (only modules that still hold the old value) is explicit and does not touch anything
    that was deliberately given another path. Returns how many modules were rebound.
    """
    rebound = 0
    for name, mod in list(sys.modules.items()):
        if mod is None or not name.startswith("wisp.server"):
            continue
        if name != __name__ and getattr(mod, "WORKSPACE_ROOT", None) == old:
            mod.WORKSPACE_ROOT = new
            rebound += 1
    for mod_name, attr in _WORKSPACE_BOUND_CACHES.items():
        mod = sys.modules.get(mod_name)
        if mod is not None and getattr(mod, attr, None) is not None:
            setattr(mod, attr, None)
    return rebound


class WorkspaceRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=1000)


@router.get("/api/workspace", dependencies=[Depends(verify_api_key)])
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

    old_root = WORKSPACE_ROOT
    WORKSPACE_ROOT = new_root
    _rebind_workspace_root(old_root, new_root)
    # Update CompositionRoot config if available
    root = getattr(request.app.state, "root", None)
    if root is not None:
        root.config = root.config.replace(workspace=str(new_root))
        root.runtime.invalidate_core_cache()
    logger.warning("Workspace changed to %s (client %s)", WORKSPACE_ROOT,
                   request.client.host if request.client else "?")
    return {"path": str(WORKSPACE_ROOT)}
