"""Review router.

Handles code review operations.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/review/best-of-n")
async def review_best_of_n():
    return {"review": {}}


@router.post("/api/review/diff")
async def review_diff():
    return {"review": {}}


@router.post("/api/review/pr")
async def review_pr():
    return {"review": {}}
