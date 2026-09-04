# tests/test_contracts_manifest.py
import json
import pytest
from wisp.contracts.manifest import PluginContract, MCPServerContract


def test_plugin_mirror(tmp_path):
    from wisp.plugins.manifest import PluginManifest
    p = tmp_path / "plugin.json"
    p.write_text(json.dumps({"name": "x", "version": "1.0", "description": "d",
        "author": "a", "license": "MIT", "namespace": "n",
        "commands": [{"name": "c1", "description": "c", "handler": "h"}]}))
    m = PluginManifest.from_file(p)
    c = PluginContract.from_plugin_manifest(m)
    assert c.commands == ("c1",)  # exercises PluginCommand→name extraction
    assert c.name == "x" and c.signature is None  # reserved, unpopulated
    assert PluginContract.from_dict(c.to_dict()) == c


def test_mcp_mirror():
    from wisp.mcp.manager import MCPServerConfig
    cfg = MCPServerConfig(name="s", command="uvx", args=["mcp"])
    c = MCPServerContract.from_server_config(cfg)
    assert c.transport == "stdio" and c.origin is None
    assert MCPServerContract.from_dict(c.to_dict()) == c


def test_from_dict_unknown_fields_rejected():
    with pytest.raises(ValueError, match="unknown plugin fields"):
        PluginContract.from_dict({"name": "x", "version": "1", "description": "d",
            "author": "a", "license": "M", "namespace": "n", "bogus": 1})
    with pytest.raises(ValueError, match="unknown mcp fields"):
        MCPServerContract.from_dict({"name": "s", "bogus": 1})


def test_from_dict_requires_name():
    with pytest.raises(ValueError, match="required field"):
        PluginContract.from_dict({"version": "1"})
    with pytest.raises(ValueError, match="required field"):
        MCPServerContract.from_dict({})
