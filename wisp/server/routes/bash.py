"""Bash router.

Handles bash execution.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key, RATE_LIMITER

router = APIRouter()


@router.post("/api/bash", dependencies=[Depends(verify_api_key), Depends(RATE_LIMITER)])
async def run_bash():
    return {"output": ""}
