"""Primitive routes through ToolExecutor (GH#25).

Thin-harness names (exec_sandbox / fs_mutate / git_checkpoint) must run
the SAME gated path as 42-tool names: authority consult, hooks, plan /
permission guards, approval, metrics. These pins lock the routing and
the write-classification; the implementations themselves are pinned in
test_harness_scaffolding.py.
"""

import asyncio
import json

from wisp.config import WispConfig, PermissionMode
from wisp.tool_executor import (
    _DEFAULT_WRITE_TOOLS,
    _SPECIAL_TOOL_ROUTES,
    ToolExecutor,
)


def _mk_executor(tmp_path, **overrides):
    base = dict(workspace=str(tmp_path), permission_mode=PermissionMode.FULL,
                auto_approve=True)
    base.update(overrides)
    return ToolExecutor(config=WispConfig().replace(**base))


def _run(executor, name, args, workspace):
    async def _go():
        return [e async for e in executor.execute(name, args, workspace)]
    return asyncio.run(_go())


def _result_text(events):
    data = getattr(events[-1], "data", events[-1])
    payload = data.get("result", data) if isinstance(data, dict) else data
    if isinstance(payload, dict):
        inner = payload.get("data", payload)
        return inner if isinstance(inner, str) else json.dumps(inner)
    return str(payload)


class TestPrimitiveRouting:
    def test_routes_registered(self):
        for name in ("exec_sandbox", "fs_mutate", "git_checkpoint"):
            assert name in _SPECIAL_TOOL_ROUTES

    def test_write_classification(self):
        # exec/fs parity with run_bash + write_file; git_checkpoint stays
        # ungated, matching the 42-tool rewind precedent.
        assert "exec_sandbox" in _DEFAULT_WRITE_TOOLS
        assert "fs_mutate" in _DEFAULT_WRITE_TOOLS
        assert "git_checkpoint" not in _DEFAULT_WRITE_TOOLS

    def test_exec_sandbox_end_to_end(self, tmp_path):
        events = _run(_mk_executor(tmp_path), "exec_sandbox",
                      {"command": "echo routed"}, str(tmp_path))
        assert "routed" in _result_text(events)

    def test_fs_mutate_write_roundtrip(self, tmp_path):
        ex = _mk_executor(tmp_path)
        _run(ex, "fs_mutate",
             {"op": "write", "path": "note.txt", "content": "hello"},
             str(tmp_path))
        assert (tmp_path / "note.txt").read_text() == "hello"
        events = _run(ex, "fs_mutate",
                      {"op": "read", "path": "note.txt"}, str(tmp_path))
        assert "hello" in _result_text(events)

    def test_tool_error_normalized_to_executor_shape(self, tmp_path):
        events = _run(_mk_executor(tmp_path), "fs_mutate",
                      {"op": "bogus", "path": "x.txt"}, str(tmp_path))
        text = _result_text(events)
        assert "ToolError" in text and "invalid arguments" in text

    def test_danger_list_still_blocks(self, tmp_path):
        events = _run(_mk_executor(tmp_path), "exec_sandbox",
                      {"command": "rm -rf /"}, str(tmp_path))
        assert "Blocked" in _result_text(events) or "Dangerous" in _result_text(events)


class TestPrimitiveGating:
    def test_plan_mode_blocks_fs_mutate(self, tmp_path):
        events = _run(_mk_executor(tmp_path, plan_mode=True), "fs_mutate",
                      {"op": "write", "path": "x.txt", "content": "y"},
                      str(tmp_path))
        assert "plan mode" in _result_text(events)
        assert not (tmp_path / "x.txt").exists()

    def test_read_only_blocks_exec_sandbox(self, tmp_path):
        events = _run(
            _mk_executor(tmp_path, permission_mode=PermissionMode.READ_ONLY),
            "exec_sandbox", {"command": "echo hi"}, str(tmp_path))
        # Denied by the layered authority consult the thin branch never had.
        assert "Denied" in _result_text(events)

    def test_ask_all_without_handler_blocks_fs_mutate(self, tmp_path):
        events = _run(
            _mk_executor(tmp_path, permission_mode=PermissionMode.ASK_ALL,
                         auto_approve=False),
            "fs_mutate", {"op": "write", "path": "x.txt", "content": "y"},
            str(tmp_path))
        assert "approval" in _result_text(events).lower()

    def test_git_checkpoint_diff_reads_state(self, tmp_path):
        (tmp_path / "a.txt").write_text("v1")
        events = _run(_mk_executor(tmp_path), "git_checkpoint",
                      {"action": "list"}, str(tmp_path))
        assert events  # ungated read path, never approval-blocked
