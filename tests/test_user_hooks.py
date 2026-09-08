"""GH#16: user hooks end-to-end (P1.2 productize pin).

A JSON file in .wisp/hooks/ must gate real tool calls through a real
ToolExecutor: exit 2 blocks (command never runs), exit 1 warns (runs),
exit 0 + tool_args JSON rewrites arguments. Plus /hooks discovery.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from wisp.config import WispConfig
from wisp.infra.hook_types import ToolHookManager
from wisp.tool_executor import ToolExecutor


def _mgr(ws: Path, *hooks: dict) -> ToolHookManager:
    d = ws / ".wisp" / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    for i, h in enumerate(hooks):
        (d / f"hook-{i}.json").write_text(json.dumps(h))
    mgr = ToolHookManager(workspace=str(ws))
    mgr.load_hooks()
    return mgr


def _cfg(ws: Path) -> WispConfig:
    from wisp.config import PermissionMode

    return WispConfig().replace(workspace=str(ws),
                                permission_mode=PermissionMode.FULL)


def _run(te: ToolExecutor, name: str, args: dict, ws: Path) -> list:
    async def go():
        return [ev async for ev in te.execute(name, args, str(ws))]
    return asyncio.run(go())


def _result_text(events: list) -> str:
    for ev in reversed(events):
        data = getattr(ev, "data", {}) or {}
        if isinstance(data, dict) and "result" in data:
            return str(data["result"])
    return ""


class TestUserHooksE2E:
    def test_block_hook_stops_command(self, tmp_path) -> None:
        mgr = _mgr(tmp_path, {
            "name": "no-bash", "event": "pre_tool_use", "matcher": "run_bash",
            "command": "echo blocked-by-user-hook >&2; exit 2"})
        te = ToolExecutor(config=_cfg(tmp_path), hook_manager=mgr)
        marker = tmp_path / "should-not-exist.txt"
        text = _run(te, "run_bash", {"command": f"touch {marker}"}, tmp_path)
        assert "Blocked by hook" in _result_text(text)
        assert "blocked-by-user-hook" in _result_text(text)
        assert not marker.exists()

    def test_warn_hook_lets_command_run(self, tmp_path) -> None:
        mgr = _mgr(tmp_path, {
            "name": "warn-bash", "event": "pre_tool_use", "matcher": "run_bash",
            "command": "echo careful >&2; exit 1"})
        te = ToolExecutor(config=_cfg(tmp_path), hook_manager=mgr)
        marker = tmp_path / "warn-ran.txt"
        _run(te, "run_bash", {"command": f"touch {marker}"}, tmp_path)
        assert marker.exists()

    def test_rewrite_hook_replaces_args(self, tmp_path) -> None:
        rewritten = {"tool_args": {"command": "echo REWRITTEN-ARGS"}}
        mgr = _mgr(tmp_path, {
            "name": "rewrite-bash", "event": "pre_tool_use", "matcher": "run_bash",
            "command": f"echo '{json.dumps(rewritten)}'; exit 0"})
        te = ToolExecutor(config=_cfg(tmp_path), hook_manager=mgr)
        text = _run(te, "run_bash", {"command": "echo ORIGINAL"}, tmp_path)
        assert "REWRITTEN-ARGS" in _result_text(text)
        assert "ORIGINAL" not in _result_text(text)

    def test_hooks_command_lists_loaded(self, tmp_path) -> None:
        from wisp.cli.dispatcher import CommandResult, Dispatcher, ReplContext

        mgr = _mgr(tmp_path, {
            "name": "list-me", "event": "post_tool_use", "matcher": "",
            "command": "exit 0"})
        import types
        transport = types.SimpleNamespace(hook_manager=mgr)
        ctx = ReplContext(runtime=None, transport=transport, session={},
                          config={"workspace": str(tmp_path)}, out=[])
        assert Dispatcher().dispatch(ctx, "/hooks") is CommandResult.CONSUMED
        assert any("list-me" in line and "post_tool_use" in line for line in ctx.out)

    def test_hooks_command_empty(self, tmp_path) -> None:
        from wisp.cli.dispatcher import CommandResult, Dispatcher, ReplContext

        import types
        transport = types.SimpleNamespace(
            hook_manager=ToolHookManager(workspace=str(tmp_path)))
        ctx = ReplContext(runtime=None, transport=transport, session={},
                          config={"workspace": str(tmp_path)}, out=[])
        assert Dispatcher().dispatch(ctx, "/hooks") is CommandResult.CONSUMED
        assert any("No hooks" in line for line in ctx.out)
