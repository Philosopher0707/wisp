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
