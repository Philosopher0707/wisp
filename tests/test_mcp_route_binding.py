"""L5: MCP route CRUD must mutate the manager the tools dispatch through.

The routes module kept a private module-global MCPManager while
tool_executor dispatched through CompositionRoot's instance — added
servers connected in the wrong twin (tools invisible until restart),
deletes left the live twin untouched, and after a workspace switch the
two managers read entirely different config files.
"""

from fastapi.testclient import TestClient


def _fresh_module():
    import importlib

    from wisp.server.routes import mcp as mcp_routes
    importlib.reload(mcp_routes)
    return mcp_routes


def test_lifespan_binds_root_manager_into_routes():
    """Structural pin: server startup must register the root's manager
    into the routes module (full-lifespan startup is too heavy to run
    here; this guards against the binding being dropped silently)."""
    from pathlib import Path

    main_text = Path("wisp/server/main.py").read_text()
    assert "set_mcp_manager(root._mcp_manager)" in main_text, (
        "lifespan no longer binds the live manager into MCP routes"
    )
    routes_text = Path("wisp/server/routes/mcp.py").read_text()
    assert "def set_mcp_manager" in routes_text


def test_set_manager_routes_crud_to_live_instance(tmp_path, monkeypatch):
    """add_mcp_server must write through the BOUND manager."""
    import wisp.server as ws_server
    ws_server._auth.disable()

    mcp_routes = _fresh_module()
    calls = {"saved": 0}

    class _Config:
        name = "acme"
        command = "echo"
        args = []
        url = None
        env = {}
        transport = "stdio"
        always_load = False
        auth = "none"
        timeout_seconds = 30
        headers = {}
        disabled_tools = []

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class _LiveManager:
        def __init__(self):
            self.servers = []
            self._server_configs = {}
            self.saved = False

        def load_server_configs(self):
            return []

        def save_server_configs(self):
            self.saved = True
            calls["saved"] += 1

    live = _LiveManager()
    mcp_routes.set_mcp_manager(live)

    monkeypatch.setattr(
        "wisp.mcp.MCPServerConfig", _Config, raising=False)
    # MCPServerConfig imported INSIDE the handler from wisp.mcp; patch there.
    import wisp.mcp as mcp_pkg
    monkeypatch.setattr(mcp_pkg, "MCPServerConfig", _Config)

    from wisp.server import app
    client = TestClient(app)
    r = client.post("/api/mcp/servers", json={
        "name": "acme", "command": "echo",
    })
    assert r.status_code == 200, r.text
    assert live.saved, "CRUD wrote somewhere other than the live manager"
    assert "acme" in live._server_configs
