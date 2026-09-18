"""Files router.

Handles file operations.
"""

import base64
import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.pathsec import resolve_contained
from wisp.server.deps import require_tool_allowed, verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


class FileWriteRequest(BaseModel):
    content: str


class FileBinaryRequest(BaseModel):
    content_base64: str


class FileEditRequest(BaseModel):
    old_text: str
    new_text: str


class FileRenameRequest(BaseModel):
    new_path: str = Field(..., min_length=1)


def _resolve_path(path: str) -> Path:
    """Resolve a path relative to WORKSPACE_ROOT, with security boundary enforcement.

    Containment semantics live in `wisp.pathsec.resolve_contained` — the single
    authority. This wrapper only maps ValueError onto the HTTP error type; it
    must not re-derive the realpath/prefix comparison (a local copy is how this
    route previously diverged from the canonical helper by accepting control
    characters in paths).
    """
    try:
        return Path(resolve_contained(str(WORKSPACE_ROOT), path))
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal blocked")


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
        except Exception:
            logger.exception("Failed to read file: %s", path)
            raise HTTPException(status_code=500, detail="Failed to read file")
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
async def create_file(path: str, req: FileWriteRequest, request: Request):
    require_tool_allowed(request, "write_file", {"path": path}, str(WORKSPACE_ROOT))
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {"ok": True, "path": path}


@router.post("/api/files/edit", dependencies=[Depends(verify_api_key)])
async def edit_file(path: str, req: FileEditRequest, request: Request):
    require_tool_allowed(request, "edit_file", {"path": path}, str(WORKSPACE_ROOT))
    target = _resolve_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    content = target.read_text(encoding="utf-8")
    if req.old_text not in content:
        raise HTTPException(status_code=400, detail="old_text not found in file")
    new_content = content.replace(req.old_text, req.new_text, 1)
    target.write_text(new_content, encoding="utf-8")
    return {"ok": True, "path": path}


@router.post("/api/files/binary", dependencies=[Depends(verify_api_key)])
async def write_binary_file(path: str, req: FileBinaryRequest, request: Request):
    require_tool_allowed(request, "write_file", {"path": path, "op": "binary"},
                         str(WORKSPACE_ROOT))
    target = _resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(req.content_base64)
    target.write_bytes(data)
    return {"ok": True, "path": path, "size": len(data)}


@router.post("/api/files/rename", dependencies=[Depends(verify_api_key)])
async def rename_file(path: str, req: FileRenameRequest, request: Request):
    require_tool_allowed(request, "edit_file",
                         {"path": path, "op": "rename", "new_path": req.new_path},
                         str(WORKSPACE_ROOT))
    source = _resolve_path(path)
    dest = _resolve_path(req.new_path)
    if not source.exists():
        raise HTTPException(status_code=404, detail="Source not found")
    if dest.exists():
        raise HTTPException(status_code=400, detail="Destination already exists")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    return {"ok": True, "from": path, "to": req.new_path}


@router.delete("/api/files", dependencies=[Depends(verify_api_key)])
async def delete_file(path: str, request: Request):
    require_tool_allowed(request, "edit_file", {"path": path, "op": "delete"},
                         str(WORKSPACE_ROOT))
    target = _resolve_path(path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Not found")
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"ok": True}
    except Exception:
        logger.exception("Failed to delete file: %s", path)
        raise HTTPException(status_code=500, detail="Failed to delete file")
