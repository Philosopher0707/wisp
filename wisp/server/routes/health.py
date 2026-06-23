"""Health router.

Handles health checks.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
async def health_check():
    # SECURITY (audit P2 #37): liveness probe stays unauthenticated, but
    # do not leak the build version to anonymous callers. Version remains
    # available on authenticated routes (e.g. /api/diagnostics).
    return {"status": "ok"}
