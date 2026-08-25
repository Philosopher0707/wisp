"""Background agents router.

Observe and manage background subagents over REST: list, detail,
cancel, and conversation continuation. Reads the live registry from
the composition root created in the app lifespan.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_manager(request: Request):
    """Resolve the BackgroundAgentManager from app state (None → 503)."""
    root = getattr(request.app.state, "root", None)
    if root is None:
        return None
    return getattr(root, "background_agents", None)


def _require_manager(request: Request):
    manager = _get_manager(request)
    if manager is None:
        raise HTTPException(status_code=503, detail="Background agents not available")
    return manager


class AgentSendRequest(BaseModel):
    message: str = Field(..., min_length=1, description="Follow-up instruction "
                         "appended to the agent's existing conversation.")


@router.get("/api/agents/background", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def list_background_agents(request: Request, include_finished: bool = True):
    """List all background agents with status and result summaries."""
    manager = _require_manager(request)
    agents = manager.list(include_finished=include_finished)
    return {"agents": agents, "count": len(agents)}


@router.get("/api/agents/background/{agent_id}", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def get_background_agent(agent_id: str, request: Request):
    """Full snapshot of one background agent (404 when unknown)."""
    manager = _require_manager(request)
    entry = manager.get(agent_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}")
    return manager.snapshot(entry)


@router.post("/api/agents/background/{agent_id}/cancel", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def cancel_background_agent(agent_id: str, request: Request):
    """Cancel a running background agent."""
    manager = _require_manager(request)
    outcome = manager.cancel(agent_id)
    if not outcome.get("ok"):
        status_code = 404 if "Unknown" in outcome.get("error", "") else 409
        raise HTTPException(status_code=status_code, detail=outcome.get("error", "cancel failed"))
    return outcome


@router.post("/api/agents/background/{agent_id}/send", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def send_to_background_agent(agent_id: str, req: AgentSendRequest, request: Request):
    """Continue a finished background agent's conversation with a follow-up."""
    manager = _require_manager(request)
    entry = manager.get(agent_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}")
    outcome = await manager.send(agent_id, req.message)
    if not outcome.get("ok"):
        raise HTTPException(status_code=409, detail=outcome.get("error", "send failed"))
    return outcome
