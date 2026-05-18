"""Arena router.

Handles arena comparisons and voting.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/arena/entries")
async def list_arena_entries():
    return {"entries": []}


@router.get("/api/arena/leaderboard")
async def arena_leaderboard():
    return {"leaderboard": []}


@router.post("/api/arena/compare")
async def compare_arena():
    return {"comparison": {}}


@router.post("/api/arena/vote")
async def vote_arena():
    return {"voted": True}
