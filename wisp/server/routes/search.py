"""Search router.

Handles search operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.post("/api/search", dependencies=[Depends(verify_api_key)])
async def search():
    return {"results": []}
