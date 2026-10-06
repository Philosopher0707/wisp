"""Switching the workspace must reach every route module, refuse paths outside the allowed roots, and reset what was built
for the old folder. Before: ~20 modules held a stale copy of the root, so a switch changed only `GET /api/workspace`."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wisp.server.deps import verify_api_key
from wisp.server.routes import workspace as ws_mod


async def _noop_auth():
    return ""


def _git(cwd, *a):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(cwd)})


@pytest.fixture
def world(tmp_path, monkeypatch):
    # Import the consumers so they exist, then snapshot and restore every module's root: the switch rebinds real modules.
    import wisp.server.routes.files  # noqa: F401
    import wisp.server.routes.git  # noqa: F401
    import wisp.server.routes.bash  # noqa: F401
    import wisp.server.routes.plugins  # noqa: F401

    original = {n: m.WORKSPACE_ROOT for n, m in sys.modules.items()
                if n.startswith("wisp.server") and m is not None and hasattr(m, "WORKSPACE_ROOT")}
    home = tmp_path / "home"
    a, b = home / "a", home / "projects" / "b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    for n in original:
        monkeypatch.setattr(sys.modules[n], "WORKSPACE_ROOT", a.resolve())
    monkeypatch.setenv("WISP_ALLOWED_WORKSPACE_ROOTS", str(home))
    monkeypatch.setattr(ws_mod, "_WORKSPACE_MUTABLE", True)

    calls = SimpleNamespace(invalidated=0)
    runtime = SimpleNamespace(invalidate_core_cache=lambda: setattr(calls, "invalidated", calls.invalidated + 1))
    config = SimpleNamespace(replace=lambda **kw: SimpleNamespace(**kw, replace=config.replace))
    app = FastAPI()
    app.include_router(ws_mod.router)
    app.state.root = SimpleNamespace(config=config, runtime=runtime)
    app.dependency_overrides[verify_api_key] = _noop_auth
    yield SimpleNamespace(client=TestClient(app), a=a.resolve(), b=b.resolve(), outside=outside, calls=calls, home=home)


def _roots():
    return {n: sys.modules[n].WORKSPACE_ROOT for n in
            ("wisp.server.routes.workspace", "wisp.server.routes.git", "wisp.server.routes.files",
             "wisp.server.routes.bash", "wisp.server.routes.plugins")}


def test_switch_reaches_every_route_module(world):
    r = world.client.post("/api/workspace", json={"path": str(world.b)})
    assert r.status_code == 200 and r.json()["path"] == str(world.b)
    assert set(_roots().values()) == {world.b}
    assert world.client.get("/api/workspace").json()["path"] == str(world.b)
    assert world.calls.invalidated == 1


def test_diff_follows_the_new_workspace(world):
    from wisp.server.routes import git as git_routes

    _git(world.b, "init", "-q")
    (world.b / "f.txt").write_text("one\n")
    _git(world.b, "add", "-A")
    _git(world.b, "commit", "-q", "-m", "i")
    (world.b / "f.txt").write_text("two\n")
    assert asyncio.run(git_routes.git_diff())["git"] is False  # still on "a", which is not a repo
    world.client.post("/api/workspace", json={"path": str(world.b)})
    out = asyncio.run(git_routes.git_diff())
    assert [f["path"] for f in out["files"]] == ["f.txt"]


def test_a_path_outside_the_allowed_roots_is_refused_and_changes_nothing(world):
    r = world.client.post("/api/workspace", json={"path": str(world.outside)})
    assert r.status_code == 400 and "allowed roots" in r.json()["detail"]
    assert set(_roots().values()) == {world.a}
    assert world.calls.invalidated == 0


def test_traversal_missing_and_file_paths_are_refused(world):
    assert world.client.post("/api/workspace", json={"path": f"{world.a}/../outside"}).status_code == 400
    assert world.client.post("/api/workspace", json={"path": str(world.home / "nope")}).status_code == 400
    f = world.home / "file.txt"
    f.write_text("x")
    assert world.client.post("/api/workspace", json={"path": str(f)}).status_code == 400
    assert set(_roots().values()) == {world.a}


def test_caches_built_for_the_old_folder_are_cleared(world, monkeypatch):
    import wisp.server.routes.codebase as codebase
    import wisp.server.routes.mcp as mcp

    monkeypatch.setattr(codebase, "_semantic_index", object())
    monkeypatch.setattr(mcp, "_mcp_manager", object())
    world.client.post("/api/workspace", json={"path": str(world.b)})
    assert codebase._semantic_index is None and mcp._mcp_manager is None


def test_a_module_deliberately_given_another_path_is_left_alone(world, monkeypatch, tmp_path):
    import wisp.server.routes.hooks as hooks

    other = (tmp_path / "other").resolve()
    other.mkdir()
    monkeypatch.setattr(hooks, "WORKSPACE_ROOT", other)
    world.client.post("/api/workspace", json={"path": str(world.b)})
    assert hooks.WORKSPACE_ROOT == other


def test_mutation_can_be_disabled(world, monkeypatch):
    monkeypatch.setattr(ws_mod, "_WORKSPACE_MUTABLE", False)
    assert world.client.post("/api/workspace", json={"path": str(world.b)}).status_code == 403
