"""C3b: REST tool execution goes through the permission policy (fail closed).

Keyed REST has no human to approve, so approval-required verdicts deny and
mode denials deny: read_only/auto_edit must refuse run_bash, read_only must
refuse file writes, full mode keeps working. Uses throwaway FastAPI apps
with stub roots — never the repo workspace.
"""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app(root, router):
    app = FastAPI()
    app.include_router(router)
    app.state.root = root
    return app


def _root(mode):
    return SimpleNamespace(config=SimpleNamespace(permission_mode=mode))


def _bash_client(mode):
    from wisp.server.routes.bash import router

    return TestClient(_app(_root(mode), router))


def test_bash_denied_in_read_only():
    r = _bash_client("read_only").post("/api/bash", json={"command": "echo hi"})
    assert r.status_code == 403


def test_bash_denied_in_auto_edit_no_approver():
    r = _bash_client("auto_edit").post("/api/bash", json={"command": "echo hi"})
    assert r.status_code == 403


def test_bash_allowed_in_full(tmp_path, monkeypatch):
    import wisp.server.routes.bash as bash_mod

    monkeypatch.setattr(bash_mod, "WORKSPACE_ROOT", tmp_path)
    r = _bash_client("full").post("/api/bash", json={"command": "echo hi"})
    assert r.status_code == 200
    assert "hi" in r.json()["stdout"]


def test_file_write_denied_in_read_only(tmp_path, monkeypatch):
    import wisp.server.routes.files as files_mod
    from wisp.server.routes.files import router

    monkeypatch.setattr(files_mod, "WORKSPACE_ROOT", tmp_path)
    r = TestClient(_app(_root("read_only"), router)).post(
        "/api/files", params={"path": "gate-probe.txt"}, json={"content": "x"})
    assert r.status_code == 403
    assert not (tmp_path / "gate-probe.txt").exists()


def test_file_write_allowed_in_auto_edit(tmp_path, monkeypatch):
    import wisp.server.routes.files as files_mod
    from wisp.server.routes.files import router

    monkeypatch.setattr(files_mod, "WORKSPACE_ROOT", tmp_path)
    r = TestClient(_app(_root("auto_edit"), router)).post(
        "/api/files", params={"path": "gate-probe.txt"}, json={"content": "x"})
    assert r.status_code == 200
    assert (tmp_path / "gate-probe.txt").read_text() == "x"
