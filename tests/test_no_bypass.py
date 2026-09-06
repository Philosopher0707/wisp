# tests/test_no_bypass.py — structural: every action path honors authority.
from wisp.config import PermissionMode, WispConfig


def _mk_config(workspace: str, permission_mode=PermissionMode.FULL):
    cfg = WispConfig()
    return cfg.replace(workspace=workspace, permission_mode=permission_mode)


def _hook_mgr():
    from unittest.mock import AsyncMock, MagicMock
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


async def _collect(agen):
    return [e async for e in agen]


def _texts(events):
    out = []
    for e in events:
        data = getattr(e, "data", e) if not isinstance(e, dict) else e.get("data", e)
        out.append(str(data.get("result", data) if isinstance(data, dict) else data))
    return out


def test_executor_denies_quarantined_write(tmp_path):
    """Even FULL mode cannot write into a quarantined workspace (M2 I1)."""
    from wisp.tool_executor import ToolExecutor
    (tmp_path / ".wisp-quarantine").write_text("untrusted")
    te = ToolExecutor(config=_mk_config(str(tmp_path)), hook_manager=_hook_mgr())
    import asyncio
    events = asyncio.run(_collect(te.execute(
        "write_file", {"path": str(tmp_path / "evil.py"), "content": "x"},
        str(tmp_path), tool_call_id="t1")))
    assert any("Denied by workspace" in t for t in _texts(events)), _texts(events)


def test_fallback_denies_write_without_executor(tmp_path):
    """No-executor fallback executes safe reads only; writes are denied (M2 I2)."""
    import asyncio
    from wisp.core.stateless import WispAgentCore
    core = WispAgentCore()
    assert core.tool_executor is None
    target = tmp_path / "should_not_exist.py"
    events = asyncio.run(_collect(core._execute_tool(
        {"name": "write_file",
         "arguments": {"path": str(target), "content": "x"}, "id": "t1"},
        {"workspace": str(tmp_path)})))
    assert any("Denied" in t or "denied" in t for t in _texts(events)), _texts(events)
    assert not target.exists()


def test_fallback_read_still_works(tmp_path):
    """I2 preserves the fallback's legitimate use: safe reads."""
    import asyncio
    from wisp.core.stateless import WispAgentCore
    (tmp_path / "a.txt").write_text("hello")
    core = WispAgentCore()
    events = asyncio.run(_collect(core._execute_tool(
        {"name": "read_file", "arguments": {"path": str(tmp_path / "a.txt")},
         "id": "t2"},
        {"workspace": str(tmp_path)})))
    assert any("hello" in t for t in _texts(events)), _texts(events)


def test_registry_denies_quarantined_write_direct(tmp_path):
    """Direct registry calls honor workspace authority (M2 I1).

    Regression pin for the unauthenticated entrypoint audit finding:
    wisp.tools.registry.execute_tool must consult authorize() instead of
    executing blindly.
    """
    import json
    from wisp.tools.registry import execute_tool
    (tmp_path / ".wisp-quarantine").write_text("untrusted")
    out = json.loads(execute_tool(
        "write_file", {"path": str(tmp_path / "evil.py"), "content": "x"},
        str(tmp_path)))
    assert out["status"] == "error", out
    assert "Denied by workspace" in out["data"], out
    assert not (tmp_path / "evil.py").exists()


def test_registry_read_direct_still_works(tmp_path):
    """Direct reads are unaffected by the registry authority gate."""
    import json
    from wisp.tools.registry import execute_tool
    (tmp_path / "a.txt").write_text("hello")
    out = json.loads(execute_tool(
        "read_file", {"path": str(tmp_path / "a.txt")}, str(tmp_path)))
    assert out["status"] == "ok", out
    assert "hello" in out["data"], out


def test_registry_hook_dir_write_denied_direct(tmp_path):
    """L4 hook-directory guard applies to direct registry calls."""
    import json
    from wisp.tools.registry import execute_tool
    out = json.loads(execute_tool(
        "write_file",
        {"path": str(tmp_path / ".wisp" / "hooks" / "evil.sh"), "content": "x"},
        str(tmp_path)))
    assert out["status"] == "error", out
    assert "hook" in out["data"].lower(), out
