"""TDD tests for tool_executor bugs found during code review.

Bug 2: FULL permission mode forces approval when auto_approve=False.
Bug 3: PRE_FILE_WRITE / PRE_BASH hooks fire twice (in execute() + _execute_tool()).
Bug 4: _run_write_verify calls sync tool_run_tests, blocking the event loop.
"""

import asyncio
import time as time_mod
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wisp.config import WispConfig, PermissionMode
from wisp.tool_executor import ToolExecutor


def _mk_config(workspace: str, permission_mode=PermissionMode.FULL, auto_approve=False):
    cfg = WispConfig()
    cfg.workspace = workspace
    cfg.permission_mode = permission_mode
    cfg.auto_approve = auto_approve
    return cfg


def _make_async_hook_mgr():
    """Create a hook_manager mock where arun_hooks can be awaited."""
    mgr = MagicMock()
    mgr.arun_hooks = AsyncMock(return_value=[])
    mgr.maybe_reload_hooks = MagicMock()
    mgr.load_project_hooks = MagicMock()
    return mgr


def _patch_to_thread():
    """Patch asyncio.to_thread so real tool execution is skipped but
    _execute_tool's real code path (hooks, write_verify) runs."""
    return patch("asyncio.to_thread", new_callable=AsyncMock,
                 return_value='{"status": "ok"}'.replace("'", '"'))


# ── Bug 2: FULL permission mode ────────────────────────────────────────


class TestFullPermissionMode:
    """In FULL mode, write tools should auto-approve — user chose 'no restrictions'."""

    def test_needs_forced_approval_returns_false_in_full_mode(self):
        """FULL mode: _needs_forced_approval should return False for all tools."""
        cfg = _mk_config("/tmp/test", permission_mode=PermissionMode.FULL)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        assert te._needs_forced_approval("write_file") is False
        assert te._needs_forced_approval("edit_file") is False
        assert te._needs_forced_approval("edit_file_multi") is False
        assert te._needs_forced_approval("run_bash") is False
        assert te._needs_forced_approval("git_push") is False

    def test_needs_forced_approval_bash_in_auto_edit_mode(self):
        """AUTO_EDIT mode: bash and git writes need approval, file edits don't."""
        cfg = _mk_config("/tmp/test", permission_mode=PermissionMode.AUTO_EDIT)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        assert te._needs_forced_approval("run_bash") is True
        assert te._needs_forced_approval("git_push") is True
        assert te._needs_forced_approval("write_file") is False
        assert te._needs_forced_approval("edit_file") is False

    def test_needs_forced_approval_all_in_ask_all_mode(self):
        """ASK_ALL mode: all write tools need approval."""
        cfg = _mk_config("/tmp/test", permission_mode=PermissionMode.ASK_ALL)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        assert te._needs_forced_approval("write_file") is True
        assert te._needs_forced_approval("edit_file") is True
        assert te._needs_forced_approval("run_bash") is True

    @pytest.mark.asyncio
    async def test_full_mode_skips_approval_handler(self, tmp_path):
        """FULL mode: write tools should NOT invoke approval handler.
        FULL = no restrictions, even when auto_approve=False."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=False)
        approval_handler = AsyncMock(return_value=(True, None))
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        with _patch_to_thread() as mock_thread:
            with patch.object(te, "_run_write_verify", new_callable=AsyncMock, return_value=""):
                with patch.object(te, "_run_post_tool_hooks", new_callable=AsyncMock):
                    events = []
                    async for ev in te.execute(
                        "edit_file",
                        {"path": str(tmp_path / "test.py"), "old_string": "a", "new_string": "b"},
                        str(tmp_path),
                        approval_handler=approval_handler,
                    ):
                        events.append(ev)

        # In FULL mode, the approval handler should NOT have been called
        approval_handler.assert_not_called()
        # Tool should have executed (asyncio.to_thread was called)
        mock_thread.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_edit_mode_calls_approval_for_bash(self, tmp_path):
        """AUTO_EDIT mode: bash should call approval handler."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.AUTO_EDIT, auto_approve=False)
        approval_handler = AsyncMock(return_value=(True, None))
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        with _patch_to_thread() as mock_thread:
            with patch.object(te, "_run_post_tool_hooks", new_callable=AsyncMock):
                events = []
                async for ev in te.execute(
                    "run_bash",
                    {"command": "echo hi"},
                    str(tmp_path),
                    approval_handler=approval_handler,
                ):
                    events.append(ev)

        # In AUTO_EDIT mode, bash should call approval handler
        approval_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_edit_mode_skips_approval_for_file_edit(self, tmp_path):
        """AUTO_EDIT mode: file edits auto-approved, bash needs approval."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.AUTO_EDIT, auto_approve=True)
        approval_handler = AsyncMock(return_value=(True, None))
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        with _patch_to_thread() as mock_thread:
            with patch.object(te, "_run_write_verify", new_callable=AsyncMock, return_value=""):
                with patch.object(te, "_run_post_tool_hooks", new_callable=AsyncMock):
                    events = []
                    async for ev in te.execute(
                        "edit_file",
                        {"path": str(tmp_path / "test.py"), "old_string": "a", "new_string": "b"},
                        str(tmp_path),
                        approval_handler=approval_handler,
                    ):
                        events.append(ev)

        # In AUTO_EDIT mode, file edits should NOT call approval handler
        approval_handler.assert_not_called()
        mock_thread.assert_called_once()


# ── Bug 3: duplicate hook calls ────────────────────────────────────────


class TestNoDuplicateHooks:
    """PRE_FILE_WRITE and PRE_BASH hooks must fire exactly once per tool call.

    We patch asyncio.to_thread (not _execute_tool) so the real _execute_tool
    code runs, including any duplicate hook calls.
    """

    @pytest.mark.asyncio
    async def test_pre_file_hooks_fire_once_for_edit_file(self, tmp_path):
        """edit_file: _run_pre_file_hooks called once in full execute() flow."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        hook_mgr = _make_async_hook_mgr()
        te = ToolExecutor(config=cfg, hook_manager=hook_mgr)

        with _patch_to_thread():
            with patch.object(te, "_run_write_verify", new_callable=AsyncMock, return_value=""):
                events = []
                async for ev in te.execute(
                    "edit_file",
                    {"path": str(tmp_path / "test.py"), "old_string": "a", "new_string": "b"},
                    str(tmp_path),
                ):
                    events.append(ev)

        pre_file_calls = [
            c for c in hook_mgr.arun_hooks.call_args_list
            if c.args and "pre_file_write" in str(c.args[0]).lower()
        ]
        assert len(pre_file_calls) == 1, (
            f"Expected 1 PRE_FILE_WRITE hook call, got {len(pre_file_calls)}"
        )

    @pytest.mark.asyncio
    async def test_pre_bash_hooks_fire_once_for_run_bash(self, tmp_path):
        """run_bash: _run_pre_bash_hooks called once in full execute() flow."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        hook_mgr = _make_async_hook_mgr()
        te = ToolExecutor(config=cfg, hook_manager=hook_mgr)

        with _patch_to_thread():
            events = []
            async for ev in te.execute(
                "run_bash",
                {"command": "echo hi"},
                str(tmp_path),
            ):
                events.append(ev)

        pre_bash_calls = [
            c for c in hook_mgr.arun_hooks.call_args_list
            if c.args and "pre_bash" in str(c.args[0]).lower()
        ]
        assert len(pre_bash_calls) == 1, (
            f"Expected 1 PRE_BASH hook call, got {len(pre_bash_calls)}"
        )

    @pytest.mark.asyncio
    async def test_pre_tool_hooks_fire_once_for_read_tool(self, tmp_path):
        """Read tools: only PRE_TOOL_USE fires, once."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        hook_mgr = _make_async_hook_mgr()
        te = ToolExecutor(config=cfg, hook_manager=hook_mgr)

        with _patch_to_thread():
            events = []
            async for ev in te.execute(
                "read_file",
                {"path": str(tmp_path / "test.py")},
                str(tmp_path),
            ):
                events.append(ev)

        pre_tool_calls = [
            c for c in hook_mgr.arun_hooks.call_args_list
            if c.args and "pre_tool_use" in str(c.args[0]).lower()
        ]
        assert len(pre_tool_calls) == 1, (
            f"Expected 1 PRE_TOOL_USE hook call, got {len(pre_tool_calls)}"
        )


# ── Bug 4: _run_write_verify blocks event loop ─────────────────────────


class TestWriteVerifyNonBlocking:
    """_run_write_verify must not block the asyncio event loop."""

    @pytest.mark.asyncio
    async def test_write_verify_does_not_block_event_loop(self, tmp_path):
        """Concurrent tasks make progress while _run_write_verify runs."""
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        task_started = asyncio.Event()
        task_done = asyncio.Event()

        async def concurrent_task():
            task_started.set()
            await asyncio.sleep(0.01)
            task_done.set()
            return "concurrent_result"

        with patch("wisp.tools.lsp.tool_lsp_diagnostics", return_value="✓ No issues found."):
            with patch("wisp.tools.tests.tool_run_tests", return_value="## Test Results (1/1 passed)"):
                result, concurrent = await asyncio.gather(
                    te._run_write_verify("test.py", str(tmp_path)),
                    concurrent_task(),
                )

        assert task_started.is_set()
        assert task_done.is_set()
        assert concurrent == "concurrent_result"

    @pytest.mark.asyncio
    async def test_write_verify_delegates_sync_to_thread_pool(self, tmp_path):
        """_run_write_verify must wrap slow sync calls in asyncio.to_thread.

        Before fix: tool_lsp_diagnostics and tool_run_tests called directly
        on the event loop thread, blocking it.
        After fix: delegated to asyncio.to_thread.
        """
        cfg = _mk_config(str(tmp_path), permission_mode=PermissionMode.FULL, auto_approve=True)
        te = ToolExecutor(config=cfg, hook_manager=_make_async_hook_mgr())

        test_file = tmp_path / "test.py"
        test_file.write_text("x = 1")

        real_to_thread = asyncio.to_thread
        to_thread_calls = []

        async def tracking_to_thread(fn, *args, **kwargs):
            to_thread_calls.append(fn.__name__ if hasattr(fn, '__name__') else str(fn))
            return await real_to_thread(fn, *args, **kwargs)

        with patch("wisp.tools.lsp.tool_lsp_diagnostics", return_value="✓ No issues found."):
            with patch("wisp.tools.tests.tool_run_tests", return_value="## Test Results (1/1 passed)"):
                with patch("asyncio.to_thread", side_effect=tracking_to_thread):
                    await te._run_write_verify(str(test_file), str(tmp_path))

        assert len(to_thread_calls) >= 1, (
            f"Expected asyncio.to_thread calls for sync operations, got {len(to_thread_calls)}. "
            "Sync functions must be delegated to thread pool to avoid blocking event loop."
        )
