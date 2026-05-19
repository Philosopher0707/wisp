"""Sessions router.

Handles session CRUD operations.
"""

from fastapi import APIRouter, Depends

from wisp.server.deps import verify_api_key

router = APIRouter()


@router.get("/api/sessions", dependencies=[Depends(verify_api_key)])
async def list_sessions():
    from wisp.adapters import get_store
    sm = get_store()
    sessions = sm.list_sessions()
    for s in sessions:
        s.pop("file", None)
    return {"sessions": sessions}


@router.get("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def get_session(session_id: str):
    from wisp.adapters import get_store
    sm = get_store()
    session = sm.load(session_id)
    if session is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session}


@router.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def delete_session(session_id: str):
    from wisp.adapters import get_store
    sm = get_store()
    sm.delete(session_id)
    return {"deleted": True}


@router.patch("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def patch_session(session_id: str):
    return {"session_id": session_id}


@router.post("/api/sessions/fork", dependencies=[Depends(verify_api_key)])
async def fork_session():
    return {"forked": True}
