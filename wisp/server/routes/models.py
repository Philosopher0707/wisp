"""Models router.

Handles model listing.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/models", dependencies=[Depends(verify_api_key)])
async def list_models():
    return {"models": []}
