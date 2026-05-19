"""Prompt router.

Handles prompt execution.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

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
async def execute_prompt(req: PromptRequest):
    """Non-interactive/headless prompt execution with JSON response."""
    # TODO: integrate with actual headless agent runner
    return {
        "ok": True,
        "prompt": req.prompt,
        "model": req.model,
        "session_id": req.session_id,
    }
