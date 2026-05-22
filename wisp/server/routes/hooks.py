"""Hooks router.

Handles hook operations.
"""

import json as _json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key, RATE_LIMITER
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()

# Strict allowlist for hook names to prevent path traversal
_SAFE_HOOK_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_MAX_HOOK_NAME_LEN = 128


def _validate_hook_name(name: str) -> str:
    """Sanitize and validate a hook name."""
    if not name or not isinstance(name, str):
        raise ValueError("Hook name must be a non-empty string")
    if len(name) > _MAX_HOOK_NAME_LEN:
        raise ValueError(f"Hook name too long (max {_MAX_HOOK_NAME_LEN})")
    if not _SAFE_HOOK_NAME_RE.match(name):
        raise ValueError(
            "Hook name may only contain letters, numbers, underscores, and hyphens"
        )
    return name


class HookCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    event: str = Field(..., description="PRE_TOOL_USE | POST_TOOL_USE | PRE_BASH | POST_BASH | PRE_FILE_WRITE | SESSION_START | SESSION_END")
    command: str = Field(..., min_length=1)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    enabled: bool = Field(default=True)
    matcher: str | None = Field(default=None)
    working_dir: str | None = Field(default=None)


@router.get("/api/hooks", dependencies=[Depends(verify_api_key)])
async def list_hooks():
    from wisp.infra.hook_types import HookManager
    manager = HookManager(workspace=WORKSPACE_ROOT)
    manager.load_project_hooks()
    hooks = manager.list_hooks()
    return {
        "hooks": [
            {
                "name": h.name,
                "event": h.event.value if hasattr(h.event, "value") else str(h.event),
                "command": h.command,
                "timeout_seconds": h.timeout_seconds,
                "enabled": h.enabled,
                "matcher": h.matcher,
                "working_dir": h.working_dir,
            }
            for h in hooks
        ]
    }


@router.get("/api/hooks/logs", dependencies=[Depends(verify_api_key)])
async def hook_logs():
    return {"logs": [], "message": "Hook execution logging not yet implemented"}


@router.post("/api/hooks", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def create_hook(req: HookCreateRequest):
    from wisp.infra.hook_types import HookConfig, HookEvent

    hooks_dir = WORKSPACE_ROOT / ".wisp" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    event_map = {
        "PRE_TOOL_USE": HookEvent.TOOL_CALL,
        "POST_TOOL_USE": HookEvent.TOOL_RESULT,
        "PRE_BASH": HookEvent.BASH_COMMAND,
        "POST_BASH": HookEvent.BASH_COMMAND,
        "PRE_FILE_WRITE": HookEvent.FILE_WRITE,
        "SESSION_START": HookEvent.SESSION_START,
        "SESSION_END": HookEvent.SESSION_END,
    }
    event = event_map.get(req.event.upper())
    if event is None:
        valid = list(event_map.keys())
        raise HTTPException(status_code=400, detail=f"Invalid event '{req.event}'. Must be one of: {', '.join(valid)}")

    try:
        safe_name = _validate_hook_name(req.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    hook = HookConfig(
        name=safe_name,
        event=event,
        command=req.command,
        timeout_seconds=req.timeout_seconds,
        enabled=req.enabled,
        matcher=req.matcher,
        working_dir=req.working_dir,
    )

    hook_file = hooks_dir / f"{safe_name}.json"
    hook_file.write_text(_json.dumps(hook.to_dict(), indent=2) + "\n", encoding="utf-8")

    return {"ok": True, "hook": hook.to_dict()}


@router.post("/api/hooks/{name}/test", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def test_hook(name: str, request: dict):
    from wisp.infra.hook_types import HookManager, build_hook_context
    import asyncio

    manager = HookManager(workspace=WORKSPACE_ROOT)
    manager.load_project_hooks()
    hook = manager.get_hook(name)
    if hook is None:
        raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")

    event_type = hook.event
    tool_name = request.get("tool_name", "run_bash")
    tool_args = request.get("tool_args", {"command": "echo 'hook test'"})

    context = build_hook_context(
        event=event_type,
        tool_name=tool_name,
        tool_args=tool_args,
        workspace=str(WORKSPACE_ROOT),
        session_id="test-session",
    )

    try:
        results = await manager.run_hooks(event_type, context)
        return {
            "ok": True,
            "hook": name,
            "results": [r.to_dict() for r in results],
        }
    except Exception:
        logger.exception("Hook test failed")
        raise HTTPException(status_code=500, detail="Hook test failed")


@router.delete("/api/hooks/{name}", dependencies=[Depends(verify_api_key)])
async def delete_hook(name: str):
    try:
        safe_name = _validate_hook_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    hooks_dir = WORKSPACE_ROOT / ".wisp" / "hooks"
    hook_file = hooks_dir / f"{safe_name}.json"
    if not hook_file.exists():
        raise HTTPException(status_code=404, detail=f"Hook '{safe_name}' not found")
    hook_file.unlink()
    return {"ok": True, "message": f"Hook '{safe_name}' deleted"}
