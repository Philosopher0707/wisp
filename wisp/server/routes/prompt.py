"""Prompt router.

Handles prompt execution.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT
from wisp.transport.headless import HeadlessTransport

logger = logging.getLogger(__name__)

router = APIRouter()


class PromptRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str | None = None
    session_id: str | None = None
    skill: str | None = None
    permission_mode: str = Field(default="auto_edit")
    images: list[str] | None = None
    auto_approve: bool = False
    """Opt-in flag to auto-approve tool calls without interactive approval."""


@router.post("/api/prompt", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def execute_prompt(req: PromptRequest, request: Request):
    """Non-interactive/headless prompt execution with JSON response."""
    import copy
    root = request.app.state.root
    # Deep-copy config to prevent cross-request mutation
    config = copy.deepcopy(root.config)

    if req.model:
        config.model = req.model
    config.permission_mode = req.permission_mode
    # Security: default is False; operator can override via env var for backward compat.
    env_auto_approve = os.environ.get("WISP_HEADLESS_AUTO_APPROVE", "").lower() == "true"
    # Per-request flag wins, then env var, then safe default (False).
    effective_auto_approve = req.auto_approve if req.auto_approve else env_auto_approve
    config.auto_approve = effective_auto_approve

    transport = HeadlessTransport(auto_approve=req.auto_approve)
    transport.start()

    try:
        session = await root.runtime.get_or_create_session(
            session_id=req.session_id or "headless",
            model=config.model,
            workspace=str(WORKSPACE_ROOT),
        )

        async for event in root.runtime.run_turn(session, req.prompt):
            await transport.send(event)

        result = transport.collect_result()
        result["session_id"] = session.get("id", req.session_id)
        result["prompt"] = req.prompt
        result["model"] = config.model
        # Surface tool-call denial in the response so callers know
        result["auto_approve"] = req.auto_approve
        return result

    except HTTPException:
        raise
    except Exception:
        logger.exception("Prompt execution failed")
        raise HTTPException(status_code=500, detail="Prompt execution failed")
