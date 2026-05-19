"""Hooks router.

Handles hook operations.
"""

import json as _json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wisp.server.deps import verify_api_key
from wisp.server.routes.workspace import WORKSPACE_ROOT

logger = logging.getLogger(__name__)

router = APIRouter()


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
    from wisp.adapters import HookManager
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


@router.post("/api/hooks", dependencies=[Depends(verify_api_key)])
async def create_hook(req: HookCreateRequest):
    from wisp.adapters import HookConfig, HookEvent

    hooks_dir = WORKSPACE_ROOT / ".wisp" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    try:
        event = HookEvent[req.event.upper()]
    except KeyError:
        valid = [e.name for e in HookEvent]
        raise HTTPException(status_code=400, detail=f"Invalid event '{req.event}'. Must be one of: {', '.join(valid)}")

    hook = HookConfig(
        name=req.name,
        event=event,
        command=req.command,
        timeout_seconds=req.timeout_seconds,
        enabled=req.enabled,
        matcher=req.matcher,
        working_dir=req.working_dir,
    )

    hook_file = hooks_dir / f"{req.name}.json"
    hook_file.write_text(_json.dumps(hook.to_dict(), indent=2) + "\n", encoding="utf-8")

    return {"ok": True, "hook": hook.to_dict()}


@router.post("/api/hooks/{name}/test", dependencies=[Depends(verify_api_key)])
async def test_hook(name: str, request: dict):
    from wisp.adapters import HookManager, build_hook_context
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
    except Exception as e:
        logger.exception("Hook test failed")
        raise HTTPException(status_code=500, detail=f"Hook test failed: {e}")


@router.delete("/api/hooks/{name}", dependencies=[Depends(verify_api_key)])
async def delete_hook(name: str):
    hooks_dir = WORKSPACE_ROOT / ".wisp" / "hooks"
    hook_file = hooks_dir / f"{name}.json"
    if not hook_file.exists():
        raise HTTPException(status_code=404, detail=f"Hook '{name}' not found")
    hook_file.unlink()
    return {"ok": True, "message": f"Hook '{name}' deleted"}
