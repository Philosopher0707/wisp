"""Models router.

Handles model listing.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/models")
async def list_models():
    return {"models": []}
