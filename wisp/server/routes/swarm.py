"""Swarm router.

Handles swarm runs and status.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/swarm/run")
async def run_swarm():
    return {"run_id": "swarm-1"}


@router.get("/api/swarm/status/{run_id}")
async def swarm_status(run_id: str):
    return {"run_id": run_id, "status": "running"}


@router.get("/api/swarm/events/{run_id}")
async def swarm_events(run_id: str):
    return {"run_id": run_id, "events": []}
