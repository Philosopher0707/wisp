"""Sessions router.

Handles session CRUD operations.
"""

from fastapi import APIRouter, Depends, Request

from wisp.server.deps import verify_api_key

router = APIRouter()


def _get_store(request: Request):
    """Get the UnifiedStore from the CompositionRoot."""
    root = getattr(request.app.state, "root", None)
    if root is not None:
        return root.store
    # Fallback to legacy adapter
    from wisp.adapters import get_store
    return get_store()


@router.get("/api/sessions", dependencies=[Depends(verify_api_key)])
async def list_sessions(request: Request):
    sm = _get_store(request)
    sessions = sm.list_sessions()
    for s in sessions:
        s.pop("file", None)
    return {"sessions": sessions}


@router.get("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def get_session(session_id: str, request: Request):
    sm = _get_store(request)
    session = sm.load_session(session_id)
    if session is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session}


@router.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def delete_session(session_id: str, request: Request):
    sm = _get_store(request)
    sm.delete_session(session_id)
    return {"deleted": True}


@router.patch("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def patch_session(session_id: str):
    return {"session_id": session_id}


@router.post("/api/sessions/fork", dependencies=[Depends(verify_api_key)])
async def fork_session():
    return {"forked": True}
