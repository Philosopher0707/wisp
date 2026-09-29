"""A workspace's `.wisp/mcp.json` runs nothing unless the workspace is EXPLICITLY trusted.

An MCP server entry is a command line. A cloned repository can ship `.wisp/mcp.json`, and
the existence of `.wisp/` auto-trusts a workspace (the session root even creates it), so
"trusted" in the auto sense proves nothing about who wrote the file. C2a gated
`MCPManager.connect_always_load_servers` on explicit trust, but a session never calls it:
`MCPExtension.start()` auto-connects from `load_server_configs()` with no trust check, and
the lazy `MCPManager.initialize()` path used the auto-trust shortcut. Both spawned the
repository's command.

These pins run a real `CompositionRoot` (the production observation point) against a
workspace whose server writes a marker file when executed.
"""

from __future__ import annotations

import json
import sys

import pytest


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from wisp.trust import WorkspaceTrustManager

    home = tmp_path / "home"
    (home / ".config" / "wisp").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("WISP_TRUST_ALL_WORKSPACES", raising=False)
    monkeypatch.setattr(WorkspaceTrustManager, "TRUST_FILE", tmp_path / "trusted_workspaces.json")
    return tmp_path


def _server(marker, name="evil", always_load=True):
    payload = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')"
    return {"name": name, "command": sys.executable, "args": ["-c", payload], "always_load": always_load}


def _repo(root, *servers):
    repo = root / "cloned-repo"
    (repo / ".wisp").mkdir(parents=True)
    (repo / ".wisp" / "mcp.json").write_text(json.dumps({"mcpServers": list(servers)}))
    return repo


def _session(workspace, monkeypatch):
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    monkeypatch.chdir(workspace)
    return CompositionRoot(config=WispConfig().replace(workspace=str(workspace)))


def test_session_start_runs_no_untrusted_workspace_server(isolated, monkeypatch):
    marker = isolated / "MARKER"
    root = _session(_repo(isolated, _server(marker)), monkeypatch)
    root.shutdown()
    assert not marker.exists(), "an auto-trusted workspace's always_load server ran at session start"


def test_first_mcp_use_runs_no_untrusted_workspace_server(isolated, monkeypatch):
    marker = isolated / "MARKER"
    root = _session(_repo(isolated, _server(marker, always_load=False)), monkeypatch)
    try:
        root._mcp_manager.get_all_tools()
    finally:
        root.shutdown()
    assert not marker.exists(), "the lazy initialize() path ran an auto-trusted workspace's server"


def test_untrusted_workspace_servers_are_not_even_listed(isolated):
    from wisp.mcp.manager import MCPManager, discover_mcp_configs

    repo = _repo(isolated, _server(isolated / "MARKER"))
    assert [c.name for c in MCPManager(str(repo)).load_server_configs()] == []
    assert [c.name for c in discover_mcp_configs(str(repo))] == []


def test_an_explicitly_trusted_workspace_still_loads(isolated, monkeypatch):
    from wisp.trust import WorkspaceTrustManager

    marker = isolated / "MARKER"
    repo = _repo(isolated, _server(marker))
    WorkspaceTrustManager.trust_workspace(repo)
    root = _session(repo, monkeypatch)
    root.shutdown()
    assert marker.exists(), "explicit trust is the operator's decision and must keep working"


def test_user_level_servers_are_unaffected(isolated, monkeypatch):
    marker = isolated / "MARKER"
    (isolated / "home" / ".config" / "wisp" / "mcp.json").write_text(
        json.dumps({"mcpServers": [_server(marker, name="mine")]}))
    repo = isolated / "plain"
    repo.mkdir()
    root = _session(repo, monkeypatch)
    root.shutdown()
    assert marker.exists(), "servers the operator configured in ~/.config/wisp/mcp.json still auto-load"
