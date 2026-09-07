"""Server session routes serve the workspace store (grill follow-up on GH#12).

Live-probed: `wisp server` attaches the CompositionRoot at lifespan
(server/main.py:69), so /api/sessions reads the workspace DB — the same
rows CLI turns write. The home-DB get_store() is a no-root fallback only.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from wisp.infra.store import UnifiedStore
from wisp.server.deps import verify_api_key


async def _noop_auth():
    return ""


def _app_with_root(root) -> TestClient:
    from wisp.server.routes.sessions import router

    app = FastAPI()
    app.include_router(router)
    if root is not None:
        app.state.root = root
    app.dependency_overrides[verify_api_key] = _noop_auth
    return TestClient(app)


def _seeded_store(path, sid: str = "ws-seed-1"):
    store = UnifiedStore(str(path))
    session = store.create_session(sid, "mock-model", "/tmp/ws", "probe")
    session["messages"] = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    store.save_session(session)
    return store


class TestSessionsServeWorkspaceStore:
    def test_list_serves_root_store(self, tmp_path) -> None:
        store = _seeded_store(tmp_path / "ws.db")
        client = _app_with_root(SimpleNamespace(store=store))
        body = client.get("/api/sessions").json()
        ids = [s["id"] for s in body["sessions"]]
        assert "ws-seed-1" in ids

    def test_get_returns_full_session(self, tmp_path) -> None:
        store = _seeded_store(tmp_path / "ws.db")
        client = _app_with_root(SimpleNamespace(store=store))
        body = client.get("/api/sessions/ws-seed-1").json()
        assert body["session"]["id"] == "ws-seed-1"
        assert len(body["session"]["messages"]) == 2

    def test_get_unknown_is_404(self, tmp_path) -> None:
        store = _seeded_store(tmp_path / "ws.db")
        client = _app_with_root(SimpleNamespace(store=store))
        assert client.get("/api/sessions/nope").status_code == 404

    def test_no_root_falls_back_to_get_store(self, tmp_path, monkeypatch) -> None:
        """No-root fallback path still resolves via infra get_store."""
        import wisp.infra.store as store_mod

        fallback = _seeded_store(tmp_path / "home.db", sid="fallback-1")
        monkeypatch.setattr(store_mod, "get_store", lambda *a: fallback)
        client = _app_with_root(None)
        body = client.get("/api/sessions").json()
        assert [s["id"] for s in body["sessions"]] == ["fallback-1"]
