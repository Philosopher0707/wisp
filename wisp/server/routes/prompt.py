"""Prompt router.

Handles prompt execution.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.post("/api/prompt", dependencies=[Depends(verify_api_key)])
async def execute_prompt():
    return {"result": ""}
