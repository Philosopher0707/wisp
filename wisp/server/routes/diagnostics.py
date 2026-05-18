"""Diagnostics router.

Handles diagnostics and sandbox status.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/diagnostics")
async def get_diagnostics():
    return {"diagnostics": {}}


@router.get("/api/sandbox/status")
async def sandbox_status():
    return {"status": "ok"}
