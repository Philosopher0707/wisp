"""Runs router.

Handles background runs.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key, RATE_LIMITER

router = APIRouter()


@router.post("/api/run/background", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def start_background_run():
    return {"run_id": "run-1"}


@router.get("/api/run/{run_id}", dependencies=[Depends(verify_api_key)])
async def get_run(run_id: str):
    return {"run_id": run_id}


@router.get("/api/runs", dependencies=[Depends(verify_api_key)])
async def list_runs():
    return {"runs": []}


@router.delete("/api/run/{run_id}", dependencies=[Depends(verify_api_key)])
async def delete_run(run_id: str):
    return {"deleted": True}
