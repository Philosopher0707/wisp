"""Review router.

Handles code review operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.post("/api/review/best-of-n", dependencies=[Depends(verify_api_key)])
async def review_best_of_n():
    return {"review": {}}


@router.post("/api/review/diff", dependencies=[Depends(verify_api_key)])
async def review_diff():
    return {"review": {}}


@router.post("/api/review/pr", dependencies=[Depends(verify_api_key)])
async def review_pr():
    return {"review": {}}
