"""Codebase router.

Handles codebase search and indexing.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, Query

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

_semantic_index: object | None = None


def _get_semantic_index():
    """Get or create the semantic index singleton."""
    global _semantic_index
    if _semantic_index is None:
        from wisp.semantic_index import SemanticIndex
        _semantic_index = SemanticIndex(str(WORKSPACE_ROOT))
    return _semantic_index


@router.get("/api/codebase/search", dependencies=[Depends(verify_api_key)])
async def search_codebase(
    q: str = Query(..., min_length=1, max_length=500),
    n: int = Query(default=5, ge=1, le=20),
):
    """Semantic search over the codebase. Returns top-N relevant code chunks."""
    index = _get_semantic_index()
    results = await asyncio.to_thread(index.search, q, top_k=n)
    return {
        "query": q,
        "results": [
            {
                "path": r.path,
                "line": r.line,
                "text": r.text,
                "score": r.score,
            }
            for r in results
        ],
    }


@router.get("/api/codebase/stats", dependencies=[Depends(verify_api_key)])
async def codebase_stats():
    index = _get_semantic_index()
    stats = await asyncio.to_thread(index.stats)
    return {"stats": stats}


@router.post("/api/codebase/index", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def index_codebase():
    index = _get_semantic_index()
    await asyncio.to_thread(index.rebuild)
    return {"indexed": True}
