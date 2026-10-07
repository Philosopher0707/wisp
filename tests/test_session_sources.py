"""The app lists and opens sessions from the machine's other Wisp stores, read-only, and copies on open."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wisp.infra.store import UnifiedStore
from wisp.server.deps import verify_api_key


async def _noop_auth():
    return ""


def _make(path, sid, title, updated, n=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    store = UnifiedStore(str(path))
    s = store.create_session(sid, "m", "/ws", title)
    s["messages"] = [{"role": "user", "content": f"q{i}"} for i in range(n)]
    s["updated_at"] = updated
    store.save_session(s)
    store._get_conn().close()
    return store


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    own = UnifiedStore(str(home / ".wisp" / "workspace" / ".wisp" / "wisp.db"))
    s = own.create_session("own-1", "m", "/ws", "mine")
    s["updated_at"] = "2026-10-05T00:00:00+00:00"
    own.save_session(s)
    glob = home / ".config" / "wisp" / "wisp.db"
    hm = home / ".wisp" / "wisp.db"
    _make(glob, "g-1", "global old", "2026-10-01T00:00:00+00:00")
    _make(hm, "h-1", "home newest", "2026-10-06T00:00:00+00:00", n=3)
    from wisp.server.routes.sessions import router

    app = FastAPI()
    app.include_router(router)
    app.state.root = SimpleNamespace(store=own)
    app.dependency_overrides[verify_api_key] = _noop_auth
    return SimpleNamespace(client=TestClient(app), own=own, glob=glob, hm=hm)


def test_lists_all_stores_newest_first_with_source(world):
    rows = world.client.get("/api/sessions").json()["sessions"]
    assert [(r["id"], r["source"]) for r in rows] == [("h-1", "home"), ("own-1", "app"), ("g-1", "global")]
    assert rows[0]["msg_count"] == 3


def test_own_session_wins_a_duplicate_id(world, tmp_path):
    _make(world.glob, "own-1", "stale copy", "2026-10-09T00:00:00+00:00")
    rows = [r for r in world.client.get("/api/sessions").json()["sessions"] if r["id"] == "own-1"]
    assert len(rows) == 1 and rows[0]["source"] == "app"


def test_foreign_session_opens_read_only(world):
    s = world.client.get("/api/sessions/g-1").json()["session"]
    assert s["source"] == "global" and len(s["messages"]) == 2
    assert world.own.load_session("g-1") is None


def test_import_copies_and_never_touches_the_source(world):
    before = (_sha(world.glob), _sha(world.hm))
    r = world.client.post("/api/sessions/g-1/import").json()
    assert r == {"imported": True, "source": "global"}
    copy = world.own.load_session("g-1")
    assert copy["title"] == "global old" and len(copy["messages"]) == 2
    assert copy["updated_at"] == "2026-10-01T00:00:00+00:00"
    assert (_sha(world.glob), _sha(world.hm)) == before
    assert world.client.post("/api/sessions/g-1/import").json() == {"imported": False, "source": "app"}


def test_listing_never_modifies_sources(world):
    before = (_sha(world.glob), _sha(world.hm))
    world.client.get("/api/sessions")
    world.client.get("/api/sessions/h-1")
    assert (_sha(world.glob), _sha(world.hm)) == before


def test_foreign_sessions_cannot_be_deleted_or_renamed(world):
    d = world.client.delete("/api/sessions/g-1")
    assert d.status_code == 409 and "global" in d.json()["detail"]
    p = world.client.patch("/api/sessions/g-1", json={"title": "x"})
    assert p.status_code == 409
    assert world.client.get("/api/sessions/g-1").status_code == 200


def test_rename_and_delete_own_session(world):
    assert world.client.patch("/api/sessions/own-1", json={"title": "  renamed  "}).json()["ok"] is True
    assert world.own.load_session("own-1")["title"] == "renamed"
    assert world.client.patch("/api/sessions/own-1", json={"title": ""}).status_code == 422
    assert world.client.delete("/api/sessions/own-1").json() == {"deleted": True}
    assert world.own.load_session("own-1") is None


def test_unknown_session_is_404(world):
    assert world.client.get("/api/sessions/nope").status_code == 404
    assert world.client.post("/api/sessions/nope/import").status_code == 404
    assert world.client.delete("/api/sessions/nope").status_code == 404


def test_unreadable_source_is_skipped_not_fatal(world):
    world.hm.write_bytes(b"this is not a database")
    ids = [r["id"] for r in world.client.get("/api/sessions").json()["sessions"]]
    assert ids == ["own-1", "g-1"]


def test_missing_sources_just_mean_only_own(world):
    world.glob.unlink()
    world.hm.unlink()
    assert [r["id"] for r in world.client.get("/api/sessions").json()["sessions"]] == ["own-1"]


def test_untitled_foreign_session_is_labelled_by_its_first_message(world):
    store = UnifiedStore(str(world.glob))
    s = store.create_session("g-2", "m", "/ws", "")
    s["messages"] = [{"role": "user", "content": "fix the\nlogin bug please"}]
    s["updated_at"] = "2026-10-02T00:00:00+00:00"
    store.save_session(s)
    s2 = store.create_session("g-3", "m", "/ws", "")
    s2["messages"] = [{"role": "user", "content": [{"type": "text", "text": "multimodal"}]}]
    s2["updated_at"] = "2026-10-02T00:00:00+00:00"
    store.save_session(s2)
    store._get_conn().close()
    titles = {r["id"]: r["title"] for r in world.client.get("/api/sessions").json()["sessions"]}
    assert titles["g-2"] == "fix the login bug please"
    assert titles["g-3"] == ""
    assert titles["g-1"] == "global old"
