"""Git router.

Handles git operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/git", dependencies=[Depends(verify_api_key)])
async def git_status():
    return {"status": "clean"}


@router.post("/api/git/commit", dependencies=[Depends(verify_api_key)])
async def git_commit():
    return {"committed": True}
