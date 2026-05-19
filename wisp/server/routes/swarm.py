"""Swarm router.

Handles swarm runs and status.
"""

import asyncio
import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_SWARM_ROLES = ["coder", "reviewer", "tester"]
_SWARM_TTL_SECONDS = 3600  # 1 hour
_swarm_store: dict[str, dict] = {}


class SwarmRunRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    roles: list[str] | None = None
    count_per_role: dict[str, int] | None = None
    max_retries: int = Field(default=2, ge=0, le=5)
    max_parallel: int = Field(default=3, ge=1, le=10)
    model: str | None = None


def _evict_stale_swarms() -> None:
    """Remove finished swarm runs older than TTL."""
    now = time.monotonic()
    stale = [
        rid for rid, e in _swarm_store.items()
        if e.get("finished") and (now - e.get("end_time", 0)) > _SWARM_TTL_SECONDS
    ]
    for rid in stale:
        del _swarm_store[rid]


@router.post("/api/swarm/run", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def run_swarm(req: SwarmRunRequest):
    """Run a multi-agent swarm asynchronously. Returns run_id for polling."""
    from wisp.multi_agent.task import OrchestratorEvent as SwarmEvent

    roles = req.roles or DEFAULT_SWARM_ROLES

    from wisp.config import WispConfig
    config = WispConfig()
    if req.model:
        config.model = req.model
    config.workspace = str(WORKSPACE_ROOT)
    config.auto_approve = True

    try:
        from wisp.multi_agent.orchestrator import SwarmOrchestrator
    except ImportError:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"error": "Swarm subsystem unavailable"},
        )

    orch = SwarmOrchestrator(config, max_parallel=req.max_parallel)
    run_id = f"swarm-{secrets.token_hex(6)}"
    event_log: list[dict] = []

    async def collect_events(evt: SwarmEvent) -> None:
        ws_msg = evt.to_ws_message()
        entry = {
            "event_type": evt.event_type,
            "task_id": evt.task_id,
            "payload": evt.payload,
        }
        if ws_msg:
            entry["ws_message"] = ws_msg
        event_log.append(entry)

    _swarm_store[run_id] = {
        "orchestrator": orch,
        "event_log": event_log,
        "goal": req.goal,
        "roles": roles,
        "start_time": time.monotonic(),
    }

    async def _run():
        try:
            await orch.arun(
                req.goal,
                roles=roles,
                count_per_role=req.count_per_role,
                max_retries=req.max_retries,
                progress_callback=collect_events,
            )
        except Exception as e:
            logger.error("Swarm run %s error: %s", run_id, e)
        finally:
            entry = _swarm_store.get(run_id)
            if entry:
                entry["finished"] = True
                entry["end_time"] = time.monotonic()

    asyncio.create_task(_run())
    return {"run_id": run_id, "status": "running", "roles": roles}


@router.get("/api/swarm/status/{run_id}", dependencies=[Depends(verify_api_key)])
async def swarm_status(run_id: str):
    """Get status of a swarm run: agent list, counts, elapsed."""
    _evict_stale_swarms()
    entry = _swarm_store.get(run_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Swarm run not found")

    orch = entry["orchestrator"]
    registry_data = orch.registry.to_dict()
    elapsed = time.monotonic() - entry["start_time"]

    return {
        "run_id": run_id,
        "goal": entry["goal"],
        "roles": entry["roles"],
        "elapsed_seconds": round(elapsed, 1),
        "finished": entry.get("finished", False),
        "agents": registry_data["agents"],
        "total_agents": registry_data["total"],
        "active_agents": registry_data["active"],
    }


@router.get("/api/swarm/events/{run_id}", dependencies=[Depends(verify_api_key)])
async def swarm_events(run_id: str):
    """Get accumulated event log for a swarm run (for polling clients)."""
    entry = _swarm_store.get(run_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Swarm run not found")

    return {
        "run_id": run_id,
        "goal": entry["goal"],
        "finished": entry.get("finished", False),
        "events": list(entry["event_log"]),
    }
