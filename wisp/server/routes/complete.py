"""Complete router.

Handles code completion.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key, RATE_LIMITER

router = APIRouter()


@router.post("/api/complete", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def complete():
    return {"completion": ""}
