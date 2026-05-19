"""Codebase router.

Handles codebase search and indexing.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key, RATE_LIMITER

router = APIRouter()


@router.get("/api/codebase/search", dependencies=[Depends(verify_api_key)])
async def search_codebase():
    return {"results": []}


@router.get("/api/codebase/stats", dependencies=[Depends(verify_api_key)])
async def codebase_stats():
    return {"stats": {}}


@router.post("/api/codebase/index", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def index_codebase():
    return {"indexed": True}
