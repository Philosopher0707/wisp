"""Codebase router.

Handles codebase search and indexing.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/codebase/search")
async def search_codebase():
    return {"results": []}


@router.get("/api/codebase/stats")
async def codebase_stats():
    return {"stats": {}}


@router.post("/api/codebase/index")
async def index_codebase():
    return {"indexed": True}
