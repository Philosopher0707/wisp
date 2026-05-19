"""Prompt router.

Handles prompt execution.
"""

import logging

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key
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


@router.post("/api/prompt", dependencies=[Depends(verify_api_key)])
async def execute_prompt(req: PromptRequest, request: Request):
    """Non-interactive/headless prompt execution with JSON response."""
    root = request.app.state.root
    config = root.config

    if req.model:
        config.model = req.model
    config.permission_mode = req.permission_mode
    config.auto_approve = True

    transport = HeadlessTransport()
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
        return result

    except Exception as exc:
        logger.exception("Prompt execution failed")
        return {"ok": False, "error": str(exc), "prompt": req.prompt}
