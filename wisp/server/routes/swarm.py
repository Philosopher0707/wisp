"""Swarm router.

Handles swarm runs and status.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key, RATE_LIMITER

router = APIRouter()


@router.post("/api/swarm/run", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def run_swarm():
    return {"run_id": "swarm-1"}


@router.get("/api/swarm/status/{run_id}", dependencies=[Depends(verify_api_key)])
async def swarm_status(run_id: str):
    return {"run_id": run_id, "status": "running"}


@router.get("/api/swarm/events/{run_id}", dependencies=[Depends(verify_api_key)])
async def swarm_events(run_id: str):
    return {"run_id": run_id, "events": []}
