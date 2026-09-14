"""C2a: workspace MCP auto-exec requires explicit trust.

A cloned repo ships .wisp/mcp.json, and .wisp's mere existence auto-trusts
the workspace — so always_load servers would execute attacker commands at
startup. These pins prove auto-exec consults explicit trust (trust file),
never the auto-trust shortcut, while on-demand and global servers are
unaffected.
"""

import json

import pytest


def _ws_with_server(tmp_path, always_load=True):
    ws = tmp_path / "ws"
    (ws / ".wisp").mkdir(parents=True)
    (ws / ".wisp" / "mcp.json").write_text(json.dumps({
        "mcpServers": [{"name": "evil", "command": "evil-cmd-xyz", "always_load": always_load}],
    }))
    return ws


def _explicitly_trust(monkeypatch, tmp_path, ws):
    from wisp.trust import WorkspaceTrustManager

    trust_file = tmp_path / "trusted.json"
    trust_file.write_text(json.dumps([str(ws.resolve())]))
    monkeypatch.setattr(WorkspaceTrustManager, "TRUST_FILE", trust_file)


def test_explicit_trust_distinguishes_auto_trust(tmp_path):
    from wisp.trust import WorkspaceTrustManager

    ws = tmp_path / "ws"
    (ws / ".wisp").mkdir(parents=True)
    assert WorkspaceTrustManager.is_workspace_trusted(str(ws)) is True
    assert WorkspaceTrustManager.is_workspace_trusted(str(ws), allow_auto=False) is False


@pytest.mark.asyncio
async def test_always_load_skipped_without_explicit_trust(tmp_path, monkeypatch):
    from wisp.mcp import manager as mgr_mod
    from wisp.mcp.manager import MCPManager

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws = _ws_with_server(tmp_path)
    attempted = []
    monkeypatch.setattr(mgr_mod, "connect_server",
                        lambda config: attempted.append(config.name) or (_ for _ in ()).throw(
                            AssertionError("must not connect")))
    await MCPManager(str(ws)).connect_always_load_servers()
    assert attempted == []


@pytest.mark.asyncio
async def test_always_load_runs_when_explicitly_trusted(tmp_path, monkeypatch):
    from wisp.mcp import manager as mgr_mod
    from wisp.mcp.manager import MCPManager

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ws = _ws_with_server(tmp_path)
    _explicitly_trust(monkeypatch, tmp_path, ws)
    attempted = []

    class _FakeServer:
        tools = []

    monkeypatch.setattr(mgr_mod, "connect_server",
                        lambda config: attempted.append(config.name) or _FakeServer())
    await MCPManager(str(ws)).connect_always_load_servers()
    assert attempted == ["evil"]
