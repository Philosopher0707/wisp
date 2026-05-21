"""Arena router.

Handles arena comparisons and voting.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


class ArenaCompareRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    task: str = Field(default="", max_length=200)
    model_a: str = Field(default="claude-sonnet-4-6")
    model_b: str = Field(default="claude-opus-4-7")


class ArenaVoteRequest(BaseModel):
    entry_id: str = Field(..., min_length=1)
    vote: str = Field(..., pattern="^(a|b|tie)$")


@router.post("/api/arena/compare", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def compare_arena(req: ArenaCompareRequest):
    """Run a blind A/B comparison between two models."""
    from wisp.arena import get_arena, ArenaCompareRequest as AR

    arena = get_arena()
    entry = await arena.run_comparison(AR(
        prompt=req.prompt,
        task=req.task,
        model_a=req.model_a,
        model_b=req.model_b,
        workspace=str(WORKSPACE_ROOT),
    ))

    return {
        "entry_id": entry.id,
        "task": entry.task,
        "side_a": entry.to_blind_dict("a"),
        "side_b": entry.to_blind_dict("b"),
        "voted": False,
    }


@router.post("/api/arena/vote", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def vote_arena(req: ArenaVoteRequest):
    """Vote on an arena comparison. Reveals model identities after voting."""
    from wisp.arena import get_arena

    arena = get_arena()
    entry = arena.vote(req.entry_id, req.vote)
    if not entry:
        raise HTTPException(status_code=404, detail="Arena entry not found")

    return {
        "entry_id": entry.id,
        "model_a": entry.model_a,
        "model_b": entry.model_b,
        "vote": entry.vote,
        "revealed": True,
    }


@router.get("/api/arena/leaderboard", dependencies=[Depends(verify_api_key)])
async def arena_leaderboard():
    """Get the per-project arena leaderboard."""
    from wisp.arena import get_arena

    arena = get_arena()
    lb = arena.get_leaderboard(str(WORKSPACE_ROOT))
    entries = [
        {
            "id": e.id,
            "task": e.task,
            "model_a": e.model_a,
            "model_b": e.model_b,
            "a_duration_ms": e.a_duration_ms,
            "b_duration_ms": e.b_duration_ms,
            "vote": e.vote,
            "created_at": e.created_at,
        }
        for e in arena.list_entries()[:10]
    ]
    return {"leaderboard": lb, "entries": entries}


@router.get("/api/arena/entries", dependencies=[Depends(verify_api_key)])
async def list_arena_entries():
    """List all arena comparison entries."""
    from wisp.arena import get_arena

    arena = get_arena()
    return {
        "entries": [
            {
                "id": e.id,
                "task": e.task,
                "model_a": e.model_a,
                "model_b": e.model_b,
                "vote": e.vote,
                "created_at": e.created_at,
            }
            for e in arena.list_entries()
        ]
    }
