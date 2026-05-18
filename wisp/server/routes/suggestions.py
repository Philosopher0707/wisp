"""Suggestions router.

Handles suggestions.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/suggestions")
async def get_suggestions():
    return {"suggestions": []}
