"""Search router.

Handles search operations.
"""

import json as _json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER

logger = logging.getLogger(__name__)

router = APIRouter()


class WebSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    num_results: int = Field(default=5, ge=1, le=20)


@router.post("/api/search", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def search(req: WebSearchRequest):
    """Standalone web search — returns structured results with title/url/snippet."""
    from wisp.tools import tool_web_search
    try:
        result = tool_web_search(req.query, req.num_results)
        return _json.loads(result)
    except Exception:
        logger.exception("Web search failed")
        raise HTTPException(status_code=500, detail="Search failed. Please try again later.")
