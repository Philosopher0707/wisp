"""Sessions router.

Handles session CRUD operations.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from wisp.server.session_sources import import_session as import_foreign_session
from wisp.server.session_sources import list_foreign, load_foreign

from wisp.server.deps import verify_api_key

router = APIRouter()


def _get_store(request: Request):
    """Get the UnifiedStore from the CompositionRoot."""
    root = getattr(request.app.state, "root", None)
    if root is not None:
        return root.store
    # Fallback to legacy adapter
    from wisp.infra.store import get_store
    return get_store()


MAX_LISTED = 200


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


def _own_db(sm) -> Path:
    return Path(sm.db_path)


@router.get("/api/sessions", dependencies=[Depends(verify_api_key)])
async def list_sessions(request: Request):
    """The app's own sessions plus those found read-only in the machine's other Wisp stores, newest first."""
    sm = _get_store(request)
    own = sm.list_sessions(limit=MAX_LISTED)
    for s in own:
        s.pop("file", None)
        s["source"] = "app"
    seen = {s["id"] for s in own}
    foreign = [s for s in list_foreign(_own_db(sm), MAX_LISTED) if s["id"] not in seen]
    merged = sorted(own + foreign, key=lambda s: s.get("updated_at") or "", reverse=True)[:MAX_LISTED]
    return {"sessions": merged}


@router.get("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def get_session(session_id: str, request: Request):
    sm = _get_store(request)
    session = sm.load_session(session_id)
    if session is not None:
        session["source"] = "app"
    else:
        session = load_foreign(_own_db(sm), session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session": session}


@router.post("/api/sessions/{session_id}/import", dependencies=[Depends(verify_api_key)])
async def import_session(session_id: str, request: Request):
    """Copy a session from another Wisp store into the app's own so it can be continued. The source is not touched."""
    sm = _get_store(request)
    source = import_foreign_session(sm, session_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"imported": source != "app", "source": source}


def _refuse_foreign(sm, session_id: str, action: str) -> None:
    """Sessions that only exist in another store are read-only here: this app never edits or deletes someone else's history."""
    if sm.load_session(session_id) is None:
        foreign = load_foreign(_own_db(sm), session_id)
        if foreign is not None:
            raise HTTPException(
                status_code=409,
                detail=f"This session lives in the '{foreign['source']}' store. Open it once to copy it into the app, then {action} the copy.",
            )
        raise HTTPException(status_code=404, detail="Session not found")


@router.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def delete_session(session_id: str, request: Request):
    sm = _get_store(request)
    _refuse_foreign(sm, session_id, "delete")
    sm.delete_session(session_id)
    return {"deleted": True}


@router.patch("/api/sessions/{session_id}", dependencies=[Depends(verify_api_key)])
async def patch_session(session_id: str, body: RenameRequest, request: Request):
    sm = _get_store(request)
    _refuse_foreign(sm, session_id, "rename")
    session = sm.load_session(session_id)
    session["title"] = body.title.strip()
    sm.save_session(session)
    return {"ok": True, "session_id": session_id}


@router.post("/api/sessions/fork", dependencies=[Depends(verify_api_key)])
async def fork_session():
    return {"forked": True}
