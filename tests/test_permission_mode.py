"""Tests for permission_mode enforcement — verifies all four modes block/allow correctly."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from wisp.config import PermissionMode, WispConfig
from wisp.tool_executor import ToolExecutor
from wisp.core.events import TYPE_TOOL_RESULT, TYPE_APPROVAL_REQUEST


# ── Helper ────────────────────────────────────────────────────────────

def _make_executor(permission_mode: str, auto_approve: bool = True) -> ToolExecutor:
    """Create a ToolExecutor with the given permission mode."""
    config = MagicMock(spec=WispConfig)
    config.permission_mode = PermissionMode(permission_mode)
    config.auto_approve = auto_approve
    config.plan_mode = False
    config.workspace = "/tmp"
    executor = ToolExecutor(config=config)
    return executor


async def _collect(executor, tool_name, tool_args=None):
    """Run a tool through the executor and return the list of events."""
    events = []
    async for event in executor.execute(
        tool_name=tool_name,
        tool_args=tool_args or {},
        workspace="/tmp",
    ):
        events.append(event)
    return events


def _find_result(events):
    """Find the tool_result event (last event that has it)."""
    for event in reversed(events):
        if event.type == TYPE_TOOL_RESULT:
            return event
    return None


# ── read_only mode ────────────────────────────────────────────────────

class TestReadOnlyMode:
    """read_only should block all write tools and allow read tools."""

    @pytest.mark.parametrize("tool", [
        "write_file", "edit_file", "edit_file_multi",
        "run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create",
        "plan_task", "mark_step_done", "update_plan",
    ])
    @pytest.mark.asyncio
    async def test_read_only_blocks_writes(self, tool):
        """read_only blocks every tool in _WRITE_TOOLS."""
        executor = _make_executor("read_only")
        events = await _collect(executor, tool)
        result = _find_result(events)
        assert result is not None, f"Expected a tool_result for {tool}"
        assert "Blocked" in result.data.get("result", ""), \
            f"{tool} should be blocked in read_only mode: {result.data}"

    @pytest.mark.parametrize("tool", [
        "read_file", "list_files", "search_symbols", "search_codebase",
        "web_fetch", "web_search",
        "git_status", "git_diff",
        "lsp_diagnostics", "lsp_symbols",
        "remember", "recall",
    ])
    @pytest.mark.asyncio
    async def test_read_only_allows_reads(self, tool):
        """read_only allows read-only tools to pass through to execution."""
        executor = _make_executor("read_only")
        events = await _collect(executor, tool)
        result = _find_result(events)
        assert result is not None
        # Should NOT be blocked (but may fail execution because there's no real impl)
        data = result.data.get("result", "")
        assert "Blocked" not in data, f"{tool} should NOT be blocked in read_only: {data}"


# ── auto_edit mode ────────────────────────────────────────────────────

class TestAutoEditMode:
    """auto_edit should allow file ops, force-require approval for bash/git."""

    @pytest.mark.parametrize("tool", ["write_file", "edit_file", "edit_file_multi"])
    @pytest.mark.asyncio
    async def test_auto_edit_allows_file_ops(self, tool):
        """auto_edit allows file edits even when auto_approve=True."""
        executor = _make_executor("auto_edit", auto_approve=True)
        events = await _collect(executor, tool)
        result = _find_result(events)
        assert result is not None
        data = result.data.get("result", "")
        assert "Blocked" not in data, f"{tool} should be allowed: {data}"

    @pytest.mark.parametrize("tool", ["run_bash", "git_branch", "git_commit", "git_push", "gh_pr_create"])
    @pytest.mark.asyncio
    async def test_auto_edit_blocks_bash_and_git_without_handler(self, tool):
        """auto_edit blocks bash/git when auto_approve=True and no approval_handler."""
        executor = _make_executor("auto_edit", auto_approve=True)
        events = await _collect(executor, tool)
        result = _find_result(events)
        assert result is not None, f"Expected a result for {tool}"
        data = result.data.get("result", "")
        assert "requires approval" in data or "Blocked" in data, \
            f"{tool} should require approval in auto_edit: {data}"

    @pytest.mark.asyncio
    async def test_auto_edit_bash_goes_to_approval_handler_when_available(self):
        """auto_edit routes bash through approval_handler when one is provided."""
        executor = _make_executor("auto_edit", auto_approve=True)
        handler = AsyncMock(return_value=(True, None))
        events = []
        async for event in executor.execute(
            tool_name="run_bash",
            tool_args={"command": "echo hello"},
            workspace="/tmp",
            approval_handler=handler,
        ):
            events.append(event)

        # Should have yielded an approval_request
        approval_events = [e for e in events if e.type == TYPE_APPROVAL_REQUEST]
        assert len(approval_events) >= 1, \
            "Should have yielded approval_request for bash in auto_edit mode"
        handler.assert_called_once()


# ── ask_all mode ──────────────────────────────────────────────────────

class TestAskAllMode:
    """ask_all should force all writes through approval_handler."""

    @pytest.mark.parametrize("tool", ["write_file", "edit_file", "run_bash", "git_commit"])
    @pytest.mark.asyncio
    async def test_ask_all_blocks_all_writes_without_handler(self, tool):
        """ask_all blocks all writes when auto_approve=True and no approval_handler."""
        executor = _make_executor("ask_all", auto_approve=True)
        events = await _collect(executor, tool)
        result = _find_result(events)
        assert result is not None
        data = result.data.get("result", "")
        assert "requires approval" in data or "Blocked" in data, \
            f"{tool} should require approval in ask_all: {data}"

    @pytest.mark.asyncio
    async def test_ask_all_routes_to_approval_handler(self):
        """ask_all routes writes through approval_handler when available."""
        executor = _make_executor("ask_all", auto_approve=True)
        handler = AsyncMock(return_value=(True, None))
        events = []
        async for event in executor.execute(
            tool_name="write_file",
            tool_args={"path": "test.txt", "content": "hello"},
            workspace="/tmp",
            approval_handler=handler,
        ):
            events.append(event)

        approval_events = [e for e in events if e.type == TYPE_APPROVAL_REQUEST]
        assert len(approval_events) >= 1
        handler.assert_called_once()


# ── full mode ─────────────────────────────────────────────────────────

class TestFullMode:
    """full mode should not add any restrictions beyond auto_approve."""

    @pytest.mark.parametrize("tool", [
        "write_file", "edit_file", "run_bash", "read_file", "list_files",
    ])
    @pytest.mark.asyncio
    async def test_full_allows_with_auto_approve(self, tool):
        """full mode + auto_approve=True allows all tools through."""
        executor = _make_executor("full", auto_approve=True)
        events = await _collect(executor, tool)
        result = _find_result(events)
        assert result is not None
        data = result.data.get("result", "")
        assert "Blocked" not in data, f"{tool} should not be blocked in full mode: {data}"

    @pytest.mark.asyncio
    async def test_full_skips_approval_handler_when_auto_approve_false(self):
        """full mode + auto_approve=False should NOT route writes to approval_handler."""
        executor = _make_executor("full", auto_approve=False)
        handler = AsyncMock(return_value=(True, None))
        events = []
        async for event in executor.execute(
            tool_name="write_file",
            tool_args={"path": "test.txt", "content": "hello"},
            workspace="/tmp",
            approval_handler=handler,
        ):
            events.append(event)

        approval_events = [e for e in events if e.type == TYPE_APPROVAL_REQUEST]
        assert len(approval_events) == 0


# ── PermissionMode enum ──────────────────────────────────────────────

class TestPermissionModeEnum:
    """PermissionMode StrEnum should have the correct values."""

    def test_enum_values(self):
        assert PermissionMode.FULL == "full"
        assert PermissionMode.ASK_ALL == "ask_all"
        assert PermissionMode.AUTO_EDIT == "auto_edit"
        assert PermissionMode.READ_ONLY == "read_only"

    def test_all_values_valid(self):
        for mode in PermissionMode:
            assert mode.value in ("full", "ask_all", "auto_edit", "read_only")

    def test_config_default_is_auto_edit(self):
        """Default permission_mode should be auto_edit for safety."""
        from wisp.config import SETTINGS_SCHEMA
        assert SETTINGS_SCHEMA["permission_mode"]["default"] == PermissionMode.AUTO_EDIT
