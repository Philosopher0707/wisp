"""Prompt router.

Handles prompt execution.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/prompt")
async def execute_prompt():
    return {"result": ""}
