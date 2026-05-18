"""Git router.

Handles git operations.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/git")
async def git_status():
    return {"status": "clean"}


@router.post("/api/git/commit")
async def git_commit():
    return {"committed": True}
