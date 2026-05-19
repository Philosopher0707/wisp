"""Hooks router.

Handles hook operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/hooks", dependencies=[Depends(verify_api_key)])
async def list_hooks():
    return {"hooks": []}


@router.get("/api/hooks/logs", dependencies=[Depends(verify_api_key)])
async def hook_logs():
    return {"logs": []}


@router.post("/api/hooks", dependencies=[Depends(verify_api_key)])
async def create_hook():
    return {"created": True}


@router.post("/api/hooks/{name}/test", dependencies=[Depends(verify_api_key)])
async def test_hook(name: str):
    return {"name": name, "tested": True}


@router.delete("/api/hooks/{name}", dependencies=[Depends(verify_api_key)])
async def delete_hook(name: str):
    return {"name": name, "deleted": True}
