"""Files router.

Handles file operations.
"""

import base64
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_path(path: str) -> Path:
    """Resolve a path relative to WORKSPACE_ROOT, with security boundary enforcement."""
    real_ws = os.path.realpath(str(WORKSPACE_ROOT))
    target = WORKSPACE_ROOT / path
    real_target = os.path.realpath(str(target))

    if real_target == real_ws:
        return Path(real_target)

    prefix = real_ws if real_ws.endswith(os.sep) else real_ws + os.sep
    if not real_target.startswith(prefix):
        raise HTTPException(status_code=400, detail="Path traversal blocked")
    return Path(real_target)


@router.get("/api/files", dependencies=[Depends(verify_api_key)])
async def list_or_read_file(path: str = ""):
    target = _resolve_path(path)
    if target.is_dir():
        items = []
        for item in target.iterdir():
            rel = item.relative_to(WORKSPACE_ROOT).as_posix()
            items.append({
                "name": item.name,
                "path": rel,
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"type": "directory", "path": path, "items": items}
    elif target.is_file():
        try:
            try:
                content = target.read_text(encoding="utf-8")
                return {"type": "file", "path": path, "content": content, "encoding": "utf-8"}
            except UnicodeDecodeError:
                data = target.read_bytes()
                return {
                    "type": "file",
                    "path": path,
                    "content_base64": base64.b64encode(data).decode("ascii"),
                    "encoding": "base64",
                    "size": len(data),
                }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/api/files/tree", dependencies=[Depends(verify_api_key)])
async def file_tree():
    """Return a flat list of all files recursively (for quick-open)."""
    files = []
    for root, dirs, filenames in os.walk(WORKSPACE_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in (
            'node_modules', '__pycache__', '.git', 'dist', 'build', 'target', '.next', 'venv', '.venv', 'env'
        )]
        for f in filenames:
            if f.startswith('.'):
                continue
            full = os.path.join(root, f)
            try:
                rel = os.path.relpath(full, WORKSPACE_ROOT)
                files.append({"name": f, "path": rel, "size": os.path.getsize(full)})
            except OSError:
                pass
    return {"files": files}


@router.post("/api/files", dependencies=[Depends(verify_api_key)])
async def create_file():
    return {"created": True}


@router.post("/api/files/edit", dependencies=[Depends(verify_api_key)])
async def edit_file():
    return {"edited": True}


@router.delete("/api/files", dependencies=[Depends(verify_api_key)])
async def delete_file():
    return {"deleted": True}
