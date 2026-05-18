"""Diff router.

Handles diff and inline editing.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/diff")
async def create_diff():
    return {"diff": ""}


@router.post("/api/edit/inline")
async def inline_edit():
    return {"edited": True}
