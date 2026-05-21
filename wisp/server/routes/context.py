"""Context router.

Handles context operations.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


class ContextUpdateRequest(BaseModel):
    content: str = Field(..., min_length=1)


@router.get("/api/context", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def get_context():
    """Get loaded project context for display in UI."""
    from wisp.config import WispConfig
    config = WispConfig()
    config.workspace = str(WORKSPACE_ROOT)
    content = config.load_context_files()
    files_found: list[str] = list(config._context_mtimes.keys()) if content else []
    return {
        "content": content,
        "files_found": files_found,
        "context_files_setting": config.context_files,
    }


@router.post("/api/context", dependencies=[Depends(verify_api_key)])
async def update_context(req: ContextUpdateRequest):
    """Update or create .wisp/rules.md with the provided content."""
    wisp_dir = WORKSPACE_ROOT / ".wisp"
    wisp_dir.mkdir(parents=True, exist_ok=True)
    rules_path = wisp_dir / "rules.md"
    try:
        rules_path.write_text(req.content, encoding="utf-8")
        logger.info("Updated %s (%d chars)", rules_path, len(req.content))
        return {
            "ok": True,
            "path": str(rules_path.relative_to(WORKSPACE_ROOT)),
            "bytes": len(req.content.encode("utf-8")),
        }
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))
