"""Runs router.

Handles background runs.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/run/background")
async def start_background_run():
    return {"run_id": "run-1"}


@router.get("/api/run/{run_id}")
async def get_run(run_id: str):
    return {"run_id": run_id}


@router.get("/api/runs")
async def list_runs():
    return {"runs": []}


@router.delete("/api/run/{run_id}")
async def delete_run(run_id: str):
    return {"deleted": True}
