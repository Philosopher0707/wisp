"""Git router.

Handles git operations.
"""

import asyncio
import logging
import subprocess
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


class GitCommitRequest(BaseModel):
    message: str | None = None


async def _git(
        args: list[str], timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    """Run git off the event loop — blocking here froze every WS connection."""
    return await asyncio.to_thread(
        subprocess.run,
        args,
        cwd=str(WORKSPACE_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@router.get("/api/git", dependencies=[Depends(verify_api_key)])
async def git_status() -> dict[str, Any]:
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        return {"git": False}

    result: dict[str, Any] = {"git": True, "branch": "", "dirty": False, "ahead": 0, "behind": 0, "changed_files": []}

    try:
        branch = await _git(["git", "branch", "--show-current"], timeout=5)
        if branch.returncode == 0:
            result["branch"] = branch.stdout.strip()
    except Exception:
        pass

    try:
        status = await _git(["git", "status", "--porcelain"], timeout=5)
        if status.returncode == 0:
            lines = [l for l in status.stdout.strip().split("\n") if l]
            result["dirty"] = len(lines) > 0
            result["changed_files"] = [l[3:].strip() for l in lines]
    except Exception:
        pass

    return result


@router.post("/api/git/commit", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def git_commit(req: GitCommitRequest) -> dict[str, Any]:
    git_dir = WORKSPACE_ROOT / ".git"
    if not git_dir.exists():
        raise HTTPException(status_code=400, detail="No git repository")

    add = await _git(["git", "add", "-A"])
    if add.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git add failed: {add.stderr}")

    commit = await _git(["git", "commit", "-m", req.message or "Wisp auto-commit"])
    if commit.returncode != 0:
        raise HTTPException(status_code=500, detail=f"git commit failed: {commit.stderr}")

    return {"committed": True, "message": req.message or "Wisp auto-commit"}


MAX_DIFF_FILES = 60
MAX_DIFF_BYTES_PER_FILE = 60_000


def _status_label(code: str) -> str:
    if code == "??":
        return "untracked"
    if "D" in code:
        return "deleted"
    if "R" in code:
        return "renamed"
    if "A" in code:
        return "added"
    return "modified"


@router.get("/api/git/diff", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def git_diff() -> dict[str, Any]:
    """Working-tree changes against HEAD, one unified diff per file.

    Takes no path from the client: the file list comes from `git status` itself, so there is no
    user-supplied path to escape the workspace with. Output is capped per file and in file count.
    """
    if not (WORKSPACE_ROOT / ".git").exists():
        return {"git": False, "files": [], "truncated": False}

    status = await _git(["git", "status", "--porcelain", "-z", "-uall"], timeout=10)
    if status.returncode != 0:
        raise HTTPException(status_code=500, detail="git status failed")

    entries: list[tuple[str, str]] = []
    parts = [p for p in status.stdout.split("\0") if p]
    skip_next = False
    for part in parts:
        if skip_next:  # the origin path of a rename follows the entry
            skip_next = False
            continue
        code, path = part[:2], part[3:]
        if "R" in code or "C" in code:
            skip_next = True
        entries.append((code, path))

    files: list[dict[str, Any]] = []
    for code, path in entries[:MAX_DIFF_FILES]:
        if code == "??":
            proc = await _git(["git", "diff", "--no-color", "--no-index", "--", "/dev/null", path], timeout=10)
        else:
            proc = await _git(["git", "diff", "HEAD", "--no-color", "--", path], timeout=10)
        text = proc.stdout if proc.returncode in (0, 1) else ""
        clipped = len(text) > MAX_DIFF_BYTES_PER_FILE
        files.append({
            "path": path,
            "status": _status_label(code),
            "diff": text[:MAX_DIFF_BYTES_PER_FILE],
            "clipped": clipped,
            "binary": "Binary files" in text,
        })
    return {"git": True, "files": files, "truncated": len(entries) > MAX_DIFF_FILES}
