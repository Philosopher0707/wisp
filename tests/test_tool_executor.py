"""Tests for ToolExecutor — extracted tool execution logic from WispAgentCore.

TDD cycle: these tests define the contract that ToolExecutor must satisfy.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path

from wisp.core.events import (
    AgentEvent,
    TYPE_TOOL_RESULT,
    TYPE_APPROVAL_REQUEST,
)
from wisp.config import WispConfig


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def config():
    cfg = WispConfig()
    cfg.workspace = "/tmp"
    cfg.auto_approve = True
    cfg.plan_mode = False
    return cfg


@pytest.fixture
def mock_hook_manager():
    """Hook manager that allows all tools."""
    hm = MagicMock()
    hm.run_hooks = AsyncMock(return_value=[])
    return hm


@pytest.fixture
def mock_metrics():
    return MagicMock()


@pytest.fixture
def mock_circuit_breaker():
    cb = MagicMock()
    cb.is_open.return_value = False
    return cb


@pytest.fixture
def mock_mcp_manager():
    mcp = MagicMock()
    mcp.get_all_tools.return_value = []
    return mcp


@pytest.fixture
def mock_file_lock():
    return MagicMock()


@pytest.fixture
def mock_lsp_manager():
    return MagicMock()


@pytest.fixture
def tool_executor(config, mock_hook_manager, mock_metrics, mock_circuit_breaker,
                   mock_mcp_manager, mock_file_lock, mock_lsp_manager):
    """Build a ToolExecutor with mocked dependencies."""
    from wisp.tool_executor import ToolExecutor

    return ToolExecutor(
        config=config,
        hook_manager=mock_hook_manager,
        metrics=mock_metrics,
        circuit_breaker=mock_circuit_breaker,
        mcp=mock_mcp_manager,
        file_lock=mock_file_lock,
        lsp_manager=mock_lsp_manager,
    )


# ── Construction ─────────────────────────────────────────────────────


class TestToolExecutorConstruction:

    def test_can_be_constructed(self, tool_executor):
        assert tool_executor is not None

    def test_stores_config(self, tool_executor, config):
        assert tool_executor.config == config

    def test_stores_dependencies(self, tool_executor, mock_hook_manager):
        assert tool_executor.hook_manager == mock_hook_manager


# ── Simple tool execution ────────────────────────────────────────────


class TestToolExecutorSimpleExecution:

    @pytest.mark.asyncio
    async def test_execute_read_file(self, tool_executor, tmp_path):
        test_file = tmp_path / "hello.txt"
        test_file.write_text("world")

        events = []
        async for event in tool_executor.execute(
            tool_name="read_file",
            tool_args={"path": str(test_file.name)},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert events[0].data["name"] == "read_file"
        assert "world" in events[0].data["result"]

    @pytest.mark.asyncio
    async def test_execute_list_files(self, tool_executor, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")

        events = []
        async for event in tool_executor.execute(
            tool_name="list_files",
            tool_args={"path": "."},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        result = events[0].data["result"]
        assert "a.txt" in str(result)
        assert "b.txt" in str(result)


# ── Dangerous command blocking ───────────────────────────────────────


class TestToolExecutorDangerousCommandBlocking:

    @pytest.mark.asyncio
    async def test_blocks_rm_rf(self, tool_executor, tmp_path):
        events = []
        async for event in tool_executor.execute(
            tool_name="run_bash",
            tool_args={"command": "rm -rf /"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "Blocked" in events[0].data["result"]
        assert "dangerous" in events[0].data["result"].lower()

    @pytest.mark.asyncio
    async def test_allows_safe_bash(self, tool_executor, tmp_path):
        events = []
        async for event in tool_executor.execute(
            tool_name="run_bash",
            tool_args={"command": "echo hello"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "hello" in events[0].data["result"]


# ── Plan mode blocking ───────────────────────────────────────────────


class TestToolExecutorPlanMode:

    @pytest.mark.asyncio
    async def test_plan_mode_blocks_write_tools(self, tool_executor, tmp_path):
        tool_executor.config.plan_mode = True

        events = []
        async for event in tool_executor.execute(
            tool_name="write_file",
            tool_args={"path": "test.txt", "content": "hello"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "plan mode" in events[0].data["result"].lower()

    @pytest.mark.asyncio
    async def test_plan_mode_allows_read_tools(self, tool_executor, tmp_path):
        tool_executor.config.plan_mode = True
        test_file = tmp_path / "hello.txt"
        test_file.write_text("world")

        events = []
        async for event in tool_executor.execute(
            tool_name="read_file",
            tool_args={"path": str(test_file.name)},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "world" in events[0].data["result"]


# ── Approval gating ──────────────────────────────────────────────────


class TestToolExecutorApprovalGating:

    @pytest.mark.asyncio
    async def test_auto_approve_skips_approval(self, tool_executor, tmp_path):
        tool_executor.config.auto_approve = True

        events = []
        async for event in tool_executor.execute(
            tool_name="write_file",
            tool_args={"path": "auto.txt", "content": "hello"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "Wrote" in events[0].data["result"]

    @pytest.mark.asyncio
    async def test_no_auto_approve_yields_approval_request(self, tool_executor, tmp_path):
        tool_executor.config.auto_approve = False

        approval_handler = AsyncMock(return_value=(True, None))

        events = []
        async for event in tool_executor.execute(
            tool_name="write_file",
            tool_args={"path": "approval.txt", "content": "hello"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
            approval_handler=approval_handler,
        ):
            events.append(event)

        # Should get approval_request then tool_result
        assert len(events) == 2
        assert events[0].type == TYPE_APPROVAL_REQUEST
        assert events[1].type == TYPE_TOOL_RESULT
        approval_handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_user_denial_blocks_execution(self, tool_executor, tmp_path):
        tool_executor.config.auto_approve = False

        approval_handler = AsyncMock(return_value=(False, None))

        events = []
        async for event in tool_executor.execute(
            tool_name="write_file",
            tool_args={"path": "denied.txt", "content": "hello"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
            approval_handler=approval_handler,
        ):
            events.append(event)

        assert len(events) == 2
        assert events[0].type == TYPE_APPROVAL_REQUEST
        assert events[1].type == TYPE_TOOL_RESULT
        assert "declined" in events[1].data["result"]


# ── Hook invocation ──────────────────────────────────────────────────


class TestToolExecutorHooks:

    @pytest.mark.asyncio
    async def test_pre_tool_hook_can_block(self, tool_executor, tmp_path):
        from wisp.hooks import HookResult

        hook_result = HookResult(action="block", message="blocked by test hook")
        tool_executor.hook_manager.run_hooks = AsyncMock(return_value=[hook_result])

        events = []
        async for event in tool_executor.execute(
            tool_name="read_file",
            tool_args={"path": "any.txt"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "blocked by test hook" in events[0].data["result"].lower()

    @pytest.mark.asyncio
    async def test_pre_tool_hook_can_modify_args(self, tool_executor, tmp_path):
        from wisp.hooks import HookResult

        test_file = tmp_path / "modified.txt"
        test_file.write_text("modified content")

        hook_result = HookResult(
            action="modify",
            modified_args={"path": str(test_file.name)},
        )
        tool_executor.hook_manager.run_hooks = AsyncMock(return_value=[hook_result])

        events = []
        async for event in tool_executor.execute(
            tool_name="read_file",
            tool_args={"path": "original.txt"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "modified content" in events[0].data["result"]


# ── Circuit breaker ──────────────────────────────────────────────────


class TestToolExecutorCircuitBreaker:

    @pytest.mark.asyncio
    async def test_open_circuit_blocks_tool(self, tool_executor, tmp_path):
        tool_executor.circuit_breaker.is_open.return_value = True
        tool_executor.circuit_breaker.status.return_value = "3 failures in 60s"

        events = []
        async for event in tool_executor.execute(
            tool_name="read_file",
            tool_args={"path": "any.txt"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "Circuit breaker open" in events[0].data["result"]


# ── MCP tool routing ─────────────────────────────────────────────────


class TestToolExecutorMCPRouting:

    @pytest.mark.asyncio
    async def test_mcp_tool_routed_to_mcp_manager(self, tool_executor, tmp_path):
        mock_tool = MagicMock()
        mock_tool.name = "custom_mcp_tool"
        tool_executor.mcp.get_all_tools.return_value = [mock_tool]
        tool_executor.mcp.call_tool.return_value = "mcp result"

        events = []
        async for event in tool_executor.execute(
            tool_name="custom_mcp_tool",
            tool_args={"key": "value"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert events[0].data["result"] == "mcp result"
        tool_executor.mcp.call_tool.assert_called_once_with("custom_mcp_tool", {"key": "value"})


# ── Metrics recording ────────────────────────────────────────────────


class TestToolExecutorMetrics:

    @pytest.mark.asyncio
    async def test_records_successful_tool(self, tool_executor, tmp_path):
        events = []
        async for event in tool_executor.execute(
            tool_name="read_file",
            tool_args={"path": "any.txt"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        tool_executor.metrics.record_tool.assert_called_once()
        args = tool_executor.metrics.record_tool.call_args
        assert args[0][0] == "read_file"
        assert args[1]["success"] is True

    @pytest.mark.asyncio
    async def test_records_blocked_tool(self, tool_executor, tmp_path):
        tool_executor.circuit_breaker.is_open.return_value = True

        events = []
        async for event in tool_executor.execute(
            tool_name="read_file",
            tool_args={"path": "any.txt"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        tool_executor.metrics.record_tool_block.assert_called_once()


# ── Message building ─────────────────────────────────────────────────


class TestToolExecutorMessageBuilding:

    @pytest.mark.asyncio
    async def test_builds_tool_message(self, tool_executor, tmp_path):
        test_file = tmp_path / "msg.txt"
        test_file.write_text("content")

        msg = await tool_executor.build_tool_message(
            tool_name="read_file",
            tool_args={"path": str(test_file.name)},
            workspace=str(tmp_path),
            tool_call_id="tc-42",
        )

        assert msg["role"] == "tool"
        assert msg["name"] == "read_file"
        assert msg["tool_call_id"] == "tc-42"
        assert "content" in msg

    @pytest.mark.asyncio
    async def test_builds_blocked_tool_message(self, tool_executor, tmp_path):
        tool_executor.config.plan_mode = True

        msg = await tool_executor.build_tool_message(
            tool_name="write_file",
            tool_args={"path": "x.txt", "content": "x"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        )

        assert msg["role"] == "tool"
        assert "plan mode" in msg["content"].lower()
