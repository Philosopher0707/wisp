"""Sessions router.

Handles session CRUD operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/sessions", dependencies=[Depends(verify_api_key)])
async def list_sessions():
    return {"sessions": []}


@router.get("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def get_session(session_id: str):
    return {"session_id": session_id}


@router.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def delete_session(session_id: str):
    return {"deleted": True}


@router.patch("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def patch_session(session_id: str):
    return {"session_id": session_id}


@router.post("/api/sessions/fork", dependencies=[Depends(verify_api_key)])
async def fork_session():
    return {"forked": True}
