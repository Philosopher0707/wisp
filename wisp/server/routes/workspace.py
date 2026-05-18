"""Workspace router.

Handles workspace operations.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/workspace")
async def get_workspace():
    return {"workspace": "."}


@router.post("/api/workspace")
async def set_workspace():
    return {"workspace": "."}
