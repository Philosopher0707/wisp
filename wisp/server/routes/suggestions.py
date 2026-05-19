"""Suggestions router.

Handles suggestions.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/suggestions", dependencies=[Depends(verify_api_key)])
async def get_suggestions():
    return {"suggestions": []}
