"""Sessions router.

Handles session CRUD operations.
"""

from fastapi import APIRouter, Depends

router = APIRouter()


@router.get("/api/sessions")
async def list_sessions():
    return {"sessions": []}


@router.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    return {"session_id": session_id}


@router.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    return {"deleted": True}


@router.patch("/api/sessions/{session_id}")
async def patch_session(session_id: str):
    return {"session_id": session_id}


@router.post("/api/sessions/fork")
async def fork_session():
    return {"forked": True}
