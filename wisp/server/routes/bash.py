"""Bash router.

Handles bash execution.
"""

from fastapi import APIRouter

router = APIRouter()


@router.post("/api/bash")
async def run_bash():
    return {"output": ""}
