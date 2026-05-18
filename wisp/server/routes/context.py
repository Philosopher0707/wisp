"""Context router.

Handles context operations.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/context")
async def get_context():
    return {"context": {}}


@router.post("/api/context")
async def set_context():
    return {"context": {}}
