"""Complete router.

Handles code completion.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/complete")
async def complete():
    return {"completion": ""}
