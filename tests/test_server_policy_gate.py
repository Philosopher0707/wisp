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


def _root_authorised(mode):
    return SimpleNamespace(config=SimpleNamespace(permission_mode=mode, auto_approve=True))


def test_file_write_denied_in_auto_edit_until_the_server_authorises_it(tmp_path, monkeypatch):
    """No approver, no yes (0c6bcf2, ADR-0061 R4): the agent path denies `write_file` in `auto_edit` when
    nobody could be asked, and REST, where nobody can, agrees. Until this test changed it asserted the
    opposite, because REST used a model that auto-approved file writes."""
    import wisp.server.routes.files as files_mod
    from wisp.server.routes.files import router

    monkeypatch.setattr(files_mod, "WORKSPACE_ROOT", tmp_path)
    r = TestClient(_app(_root("auto_edit"), router)).post(
        "/api/files", params={"path": "gate-probe.txt"}, json={"content": "x"})
    assert r.status_code == 403
    assert "no approver is present over REST" in r.json()["detail"]
    assert not (tmp_path / "gate-probe.txt").exists()


def test_file_write_allowed_in_auto_edit_when_the_server_authorises_it(tmp_path, monkeypatch):
    import wisp.server.routes.files as files_mod
    from wisp.server.routes.files import router

    monkeypatch.setattr(files_mod, "WORKSPACE_ROOT", tmp_path)
    r = TestClient(_app(_root_authorised("auto_edit"), router)).post(
        "/api/files", params={"path": "gate-probe.txt"}, json={"content": "x"})
    assert r.status_code == 200
    assert (tmp_path / "gate-probe.txt").read_text() == "x"


def test_file_write_allowed_in_full_without_auto_approve(tmp_path, monkeypatch):
    import wisp.server.routes.files as files_mod
    from wisp.server.routes.files import router

    monkeypatch.setattr(files_mod, "WORKSPACE_ROOT", tmp_path)
    r = TestClient(_app(_root("full"), router)).post(
        "/api/files", params={"path": "gate-probe.txt"}, json={"content": "x"})
    assert r.status_code == 200


# ── Target C: executable-config routes carry the same gate ───────────
#
# POST /api/hooks, POST /api/mcp/servers and POST /api/plugins/install reach
# host execution and were previously API-key-only. They now consult the same
# policy layer, so read_only refuses them while full/auto_edit/ask_all keep
# working — which is why the shipped desktop client is unaffected.


def _client(mode, module_name, tmp_path, monkeypatch):
    import importlib

    mod = importlib.import_module(f"wisp.server.routes.{module_name}")
    monkeypatch.setattr(mod, "WORKSPACE_ROOT", tmp_path)
    return TestClient(_app(_root(mode), mod.router))


def test_hook_create_denied_in_read_only(tmp_path, monkeypatch):
    r = _client("read_only", "hooks", tmp_path, monkeypatch).post(
        "/api/hooks",
        json={"name": "probe", "event": "PRE_TOOL_USE", "command": "echo hi"},
    )
    assert r.status_code == 403
    assert not (tmp_path / ".wisp" / "hooks" / "probe.json").exists()


def test_hook_create_allowed_in_full(tmp_path, monkeypatch):
    r = _client("full", "hooks", tmp_path, monkeypatch).post(
        "/api/hooks",
        json={"name": "probe", "event": "PRE_TOOL_USE", "command": "echo hi"},
    )
    assert r.status_code == 200
    assert (tmp_path / ".wisp" / "hooks" / "probe.json").exists()


def test_mcp_add_denied_in_read_only(tmp_path, monkeypatch):
    r = _client("read_only", "mcp", tmp_path, monkeypatch).post(
        "/api/mcp/servers", json={"name": "probe", "command": "echo"}
    )
    assert r.status_code == 403


def test_mcp_delete_denied_in_read_only(tmp_path, monkeypatch):
    r = _client("read_only", "mcp", tmp_path, monkeypatch).delete(
        "/api/mcp/servers/probe"
    )
    assert r.status_code == 403


def test_plugin_install_denied_in_read_only(tmp_path, monkeypatch):
    r = _client("read_only", "plugins", tmp_path, monkeypatch).post(
        "/api/plugins/install", json={"path": "some-plugin"}
    )
    assert r.status_code == 403


def test_plugin_toggle_denied_in_read_only(tmp_path, monkeypatch):
    r = _client("read_only", "plugins", tmp_path, monkeypatch).post(
        "/api/plugins/probe/toggle", json={"enable": True}
    )
    assert r.status_code == 403


def test_plugin_uninstall_denied_in_read_only(tmp_path, monkeypatch):
    r = _client("read_only", "plugins", tmp_path, monkeypatch).delete(
        "/api/plugins/probe"
    )
    assert r.status_code == 403


def test_plugin_toggle_passes_the_gate_in_full(tmp_path, monkeypatch):
    """404 (not installed), not 403 — proves the gate let it through."""
    r = _client("full", "plugins", tmp_path, monkeypatch).post(
        "/api/plugins/probe/toggle", json={"enable": True}
    )
    assert r.status_code == 404


def test_plugin_install_passes_the_gate_in_full(tmp_path, monkeypatch):
    """404 (path not found), not 403 — proves the gate let it through."""
    r = _client("full", "plugins", tmp_path, monkeypatch).post(
        "/api/plugins/install", json={"path": "some-plugin"}
    )
    assert r.status_code == 404

