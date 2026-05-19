"""Context router.

Handles context operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/context", dependencies=[Depends(verify_api_key)])
async def get_context():
    return {"context": {}}


@router.post("/api/context", dependencies=[Depends(verify_api_key)])
async def set_context():
    return {"context": {}}
