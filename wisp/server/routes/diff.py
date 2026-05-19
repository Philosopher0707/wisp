"""Diff router.

Handles diff and inline editing.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.post("/api/diff", dependencies=[Depends(verify_api_key)])
async def create_diff():
    return {"diff": ""}


@router.post("/api/edit/inline", dependencies=[Depends(verify_api_key)])
async def inline_edit():
    return {"edited": True}
