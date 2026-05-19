"""Runs router.

Handles background runs.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


class BackgroundRunRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str | None = None
    permission_mode: str = Field(default="auto_edit")


@router.post("/api/run/background", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def start_background_run(req: BackgroundRunRequest):
    """Start an agent run in the background. Returns run ID for polling."""
    from wisp.background_agent import get_runner
    runner = get_runner(str(WORKSPACE_ROOT))

    model = req.model or os.environ.get("WISP_DEFAULT_MODEL", "claude-sonnet-4-6")
    run = runner.create(
        prompt=req.prompt,
        model=model,
        workspace=str(WORKSPACE_ROOT),
        permission_mode=req.permission_mode,
    )
    runner.start(run.id)
    return {"ok": True, "run_id": run.id, "status": "running"}


@router.get("/api/run/{run_id}", dependencies=[Depends(verify_api_key)])
async def get_run(run_id: str):
    from wisp.background_agent import get_runner
    runner = get_runner(str(WORKSPACE_ROOT))
    run = runner.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run.to_dict() if hasattr(run, "to_dict") else {"id": run_id}}


@router.get("/api/runs", dependencies=[Depends(verify_api_key)])
async def list_runs():
    from wisp.background_agent import get_runner
    runner = get_runner(str(WORKSPACE_ROOT))
    runs = runner.list()
    return {"runs": [r.to_dict() if hasattr(r, "to_dict") else {"id": str(r)} for r in runs]}


@router.delete("/api/run/{run_id}", dependencies=[Depends(verify_api_key)])
async def delete_run(run_id: str):
    from wisp.background_agent import get_runner
    runner = get_runner(str(WORKSPACE_ROOT))
    runner.delete(run_id)
    return {"deleted": True}
