"""Diagnostics router.

Handles diagnostics and sandbox status.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/diagnostics", dependencies=[Depends(verify_api_key)])
async def get_diagnostics():
    return {"diagnostics": {}}


@router.get("/api/sandbox/status", dependencies=[Depends(verify_api_key)])
async def sandbox_status():
    return {"status": "ok"}
