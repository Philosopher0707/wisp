"""Search router.

Handles search operations.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/search")
async def search():
    return {"results": []}
