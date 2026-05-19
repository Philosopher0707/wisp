"""Workspace router.

Handles workspace operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/workspace")
async def get_workspace():
    return {"workspace": "."}


@router.post("/api/workspace", dependencies=[Depends(verify_api_key)])
async def set_workspace():
    return {"workspace": "."}
