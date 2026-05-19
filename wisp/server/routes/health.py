"""Health router.

Handles health checks.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/health")
async def health_check():
    from wisp import __version__
    return {"status": "ok", "version": __version__}
