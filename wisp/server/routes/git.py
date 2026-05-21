"""Git router.

Handles git operations.
"""

import logging
import subprocess

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


class GitCommitRequest(BaseModel):
    message: str | None = None


@router.get("/api/git", dependencies=[Depends(verify_api_key)])
async def git_status():
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        return {"git": False}

    result: dict = {"git": True, "branch": "", "dirty": False, "ahead": 0, "behind": 0, "changed_files": []}

    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=5,
        )
        if branch.returncode == 0:
            result["branch"] = branch.stdout.strip()
    except Exception:
        pass

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=5,
        )
        if status.returncode == 0:
            lines = [l for l in status.stdout.strip().split("\n") if l]
            result["dirty"] = len(lines) > 0
            result["changed_files"] = [l[3:].strip() for l in lines]
    except Exception:
        pass

    return result


@router.post("/api/git/commit", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def git_commit(req: GitCommitRequest):
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository")

    add = subprocess.run(
        ["git", "add", "-A"],
        cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=10,
    )
    if add.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git add failed: {add.stderr}")

    commit = subprocess.run(
        ["git", "commit", "-m", req.message or "Wisp auto-commit"],
        cwd=str(WORKSPACE_ROOT), capture_output=True, text=True, timeout=10,
    )
    if commit.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git commit failed: {commit.stderr}")

    return {"committed": True, "message": req.message or "Wisp auto-commit"}
