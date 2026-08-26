"""H2-H4 fix pins: REST routes must not block the event loop.

git/models/review routes ran subprocess.run and requests.get inline in
async handlers — one slow git or dead Ollama froze EVERY connection on
the server. All blocking work now goes through asyncio.to_thread; these
tests drive the real routes against a real tmp repo.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import wisp.server as ws_server


@pytest.fixture()
def client(tmp_path: Path):
    ws_server._auth.disable()
    # fresh repo per test
    repo = tmp_path / "repo"
    repo.mkdir()
    def _git(*args):
        subprocess.run(["git", *args], cwd=repo, capture_output=True)
    _git("init", "-q")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")
    (repo / "a.txt").write_text("hello\n")
    _git("add", "-A")
    _git("commit", "-qm", "init")

    import wisp.server.routes.git as git_mod
    import wisp.server.routes.review as review_mod
    originals = (git_mod.WORKSPACE_ROOT, review_mod.WORKSPACE_ROOT)
    git_mod.WORKSPACE_ROOT = repo
    review_mod.WORKSPACE_ROOT = repo
    try:
        from wisp.server import app
        yield TestClient(app), repo
    finally:
        git_mod.WORKSPACE_ROOT, review_mod.WORKSPACE_ROOT = originals


def test_git_status_reads_real_repo(client):
    tc, repo = client
    r = tc.get("/api/git")
    assert r.status_code == 200
    body = r.json()
    assert body["git"] is True
    assert body["branch"] and "main" in body["branch"] or "master" in body["branch"]
    assert body["dirty"] is False


def test_git_commit_picks_up_new_file(client):
    tc, repo = client
    (repo / "b.txt").write_text("new\n")
    r = tc.post("/api/git/commit", json={"message": "add b"})
    assert r.status_code == 200, r.text
    assert r.json()["committed"] is True
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=repo,
        capture_output=True, text=True,
    )
    assert "add b" in log.stdout


def test_models_route_degrades_gracefully_when_provider_unreachable(monkeypatch):
    """Provider-aware catalog: one dead provider yields an empty listing,
    never a route-level 503 — the other providers still answer."""
    ws_server._auth.disable()

    monkeypatch.setattr("wisp.provider_catalog._authed_get",
                        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("down")))

    from wisp.server import app
    # Route needs a root on app state; a null-provider root is enough to
    # prove the CATALOG degrades gracefully (not the 503-uninitialized path).
    fake_root = SimpleNamespace(config=SimpleNamespace(provider="ollama",
                                                       model="x"),
                                runtime=None)
    if not hasattr(app.state, "root"):
        app.state.root = None
    original_root = getattr(app.state, "root", None)
    app.state.root = fake_root
    try:
        tc = TestClient(app)
        r = tc.get("/api/models")
    finally:
        app.state.root = original_root
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["models"], list)
    assert {p["name"] for p in body["providers"]} >= {"ollama"}


def test_review_diff_empty_repo_returns_no_changes(client):
    """Clean repo → early-return path, no agent invocation, still async."""
    tc, repo = client
    r = tc.post("/api/review/diff", json={"target": "uncommitted"})
    assert r.status_code == 200
    assert "No changes" in r.json()["summary"]


def test_routes_never_call_blocking_io_directly():
    """Structural pin: subprocess.run(/requests.get( must not appear as
    CALLS in any async route module — only passed into asyncio.to_thread.
    """
    import re
    routes = Path("wisp/server/routes")
    offenders = []
    for path in sorted(routes.glob("*.py")):
        calls = re.findall(r"(?:subprocess\.run|requests\.get)\s*\(", path.read_text())
        if calls:
            offenders.append(f"{path.name}: {len(calls)} direct call(s)")
    assert not offenders, f"event-loop blockers reintroduced: {offenders}"
