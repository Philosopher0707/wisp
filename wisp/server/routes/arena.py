"""Arena router.

Handles arena comparisons and voting.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/arena/entries", dependencies=[Depends(verify_api_key)])
async def list_arena_entries():
    return {"entries": []}


@router.get("/api/arena/leaderboard", dependencies=[Depends(verify_api_key)])
async def arena_leaderboard():
    return {"leaderboard": []}


@router.post("/api/arena/compare", dependencies=[Depends(verify_api_key)])
async def compare_arena():
    return {"comparison": {}}


@router.post("/api/arena/vote", dependencies=[Depends(verify_api_key)])
async def vote_arena():
    return {"voted": True}
