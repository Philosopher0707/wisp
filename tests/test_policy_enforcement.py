# tests/test_policy_enforcement.py — bundle enforced at execution (seam closure).
import asyncio
from unittest.mock import AsyncMock, MagicMock

from wisp.config import PermissionMode, WispConfig
from wisp.policy.loader import EffectivePolicy
from wisp.tool_executor import ToolExecutor


def _mk_config(workspace, permission_mode=PermissionMode.FULL):
    cfg = WispConfig()
    return cfg.replace(workspace=workspace, permission_mode=permission_mode)


def _hook_mgr():
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


def _policy(**overrides):
    base = {"mcp_allowlist": (), "approval_matrix": {}, "provenance": {}}
    base.update(overrides)
    return EffectivePolicy(**base)


async def _collect(agen):
    return [e async for e in agen]


def _texts(events):
    out = []
    for e in events:
        data = e.data if hasattr(e, "data") else e.get("data", e)
        out.append(str(data.get("result", data) if isinstance(data, dict) else data))
    return out


async def _approve_all(name, args, reason):
    return True, None


def test_matrix_deny_blocks_execution(tmp_path):
    pol = _policy(approval_matrix={"run_bash": "deny"},
                  provenance={"approval:run_bash": "organization"})
    te = ToolExecutor(config=_mk_config(str(tmp_path)), hook_manager=_hook_mgr(),
                      policy=pol)
    events = asyncio.run(_collect(te.execute(
        "run_bash", {"command": "echo hi"}, str(tmp_path), tool_call_id="t1",
        approval_handler=_approve_all)))
    assert any("Denied" in t and "organization" in t for t in _texts(events)), _texts(events)


def test_matrix_approve_forces_handler(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    pol = _policy(approval_matrix={"read_file": "approve"})
    te = ToolExecutor(config=_mk_config(str(tmp_path)), hook_manager=_hook_mgr(),
                      policy=pol)
    events = asyncio.run(_collect(te.execute(
        "read_file", {"path": str(tmp_path / "a.txt")}, str(tmp_path),
        tool_call_id="t2", approval_handler=_approve_all)))
    texts = _texts(events)
    assert any("hello" in t for t in texts), texts  # approved → executed


def test_matrix_approve_without_handler_blocks(tmp_path):
    pol = _policy(approval_matrix={"read_file": "approve"})
    te = ToolExecutor(config=_mk_config(str(tmp_path)), hook_manager=_hook_mgr(),
                      policy=pol)
    events = asyncio.run(_collect(te.execute(
        "read_file", {"path": str(tmp_path / "a.txt")}, str(tmp_path),
        tool_call_id="t3")))
    assert any("requires approval" in t for t in _texts(events)), _texts(events)


def test_mcp_allowlist_blocks_unlisted_server(tmp_path):
    pol = _policy(mcp_allowlist=("git",))
    te = ToolExecutor(config=_mk_config(str(tmp_path)), hook_manager=_hook_mgr(),
                      policy=pol)
    events = asyncio.run(_collect(te.execute(
        "mcp:evil/exfiltrate", {}, str(tmp_path), tool_call_id="t4",
        approval_handler=_approve_all)))
    assert any("allowlist" in t for t in _texts(events)), _texts(events)


def test_no_policy_unchanged(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    te = ToolExecutor(config=_mk_config(str(tmp_path)), hook_manager=_hook_mgr())
    events = asyncio.run(_collect(te.execute(
        "read_file", {"path": str(tmp_path / "a.txt")}, str(tmp_path),
        tool_call_id="t5")))
    assert any("hello" in t for t in _texts(events)), _texts(events)
