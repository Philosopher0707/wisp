"""Complete router.

Handles code completion.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


class CompletionRequest(BaseModel):
    path: str = Field(default="", description="File path relative to workspace")
    file_content: str = Field(..., min_length=1, description="Full file content")
    cursor_line: int = Field(..., ge=0, description="0-based cursor line")
    cursor_char: int = Field(..., ge=0, description="0-based cursor character")
    language: str = Field(default="", description="Programming language")


@router.post("/api/complete", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def complete(req: CompletionRequest):
    """Generate a code completion using the configured LLM provider."""
    from wisp.completion import generate_completion, CompletionRequest as CR
    from wisp.config import WispConfig

    config = WispConfig()
    config = config.replace(workspace=str(WORKSPACE_ROOT))

    result = await generate_completion(
        CR(
            file_content=req.file_content,
            cursor_line=req.cursor_line,
            cursor_char=req.cursor_char,
            path=req.path,
            language=req.language,
        ),
        config,
    )
    return {"completion": result.text, "finish_reason": result.finish_reason}
