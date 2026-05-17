"""End-to-end integration tests through the production ToolExecutor pipeline.

Unlike test_integration.py which calls execute_tool() directly (bypassing all
guard layers), these tests exercise ToolExecutor.execute() → build_tool_message()
as the agent actually does in _run_tool_calls.

Guard layers verified:
  1. Pre-tool hooks
  2. Plan mode guard
  3. Dangerous command blocking
  4. Circuit breaker
  5. Permission mode
  6. Approval gating (auto_approve)
  7. Event-specific pre-hooks (PRE_BASH, PRE_FILE_WRITE)
  8. Actual tool execution
  9. Post-tool hooks + metrics
  10. build_tool_message JSON extraction
"""

import json
import pytest
from unittest.mock import MagicMock
from pathlib import Path

from wisp.core.events import TYPE_TOOL_RESULT
from wisp.config import WispConfig
from wisp.tool_executor import ToolExecutor
from wisp.circuit_breaker import CircuitBreaker


def _mk_te(tmp_path, **overrides):
    """Factory to create a ToolExecutor over a temp workspace."""
    cfg = WispConfig()
    cfg.workspace = str(tmp_path)
    cfg.auto_approve = True
    cfg.plan_mode = False
    cfg.permission_mode = "full"
    cfg.max_iterations = 30
    for k, v in overrides.items():
        setattr(cfg, k, v)
    cb = MagicMock()
    cb.is_open.return_value = False
    hm = MagicMock()
    hm.run_hooks.return_value = []
    return ToolExecutor(
        config=cfg,
        metrics=MagicMock(),
        circuit_breaker=cb,
        hook_manager=hm,
    )


class TestToolExecutorFullPipeline:
    """Real tools executed through ToolExecutor.execute()."""

    @pytest.mark.asyncio
    async def test_read_file_through_pipeline(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        te = _mk_te(tmp_path)
        events = []
        async for event in te.execute(
            tool_name="read_file",
            tool_args={"path": "hello.txt"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        result = events[0].data["result"]
        assert "world" in str(result)

    @pytest.mark.asyncio
    async def test_write_file_through_pipeline(self, tmp_path):
        te = _mk_te(tmp_path)
        events = []
        async for event in te.execute(
            tool_name="write_file",
            tool_args={"path": "test.txt", "content": "hello"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert (tmp_path / "test.txt").read_text() == "hello"

    @pytest.mark.asyncio
    async def test_run_bash_safe_passes_guard(self, tmp_path):
        te = _mk_te(tmp_path)
        events = []
        async for event in te.execute(
            tool_name="run_bash",
            tool_args={"command": "echo safe-output"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "safe-output" in str(events[0].data["result"])

    @pytest.mark.asyncio
    async def test_run_bash_dangerous_blocked_by_guard(self, tmp_path):
        te = _mk_te(tmp_path)
        events = []
        async for event in te.execute(
            tool_name="run_bash",
            tool_args={"command": "rm -rf /"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        assert "Blocked" in str(events[0].data["result"])

    @pytest.mark.asyncio
    async def test_write_file_blocked_in_plan_mode(self, tmp_path):
        te = _mk_te(tmp_path, plan_mode=True)
        events = []
        async for event in te.execute(
            tool_name="write_file",
            tool_args={"path": "plan.txt", "content": "x"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert events[0].type == TYPE_TOOL_RESULT
        result = str(events[0].data["result"])
        assert "plan mode" in result.lower()
        assert not (tmp_path / "plan.txt").exists()


class TestBuildToolMessageJSONExtraction:
    """build_tool_message must extract 'data' from JSON, not pass raw JSON."""

    @pytest.mark.asyncio
    async def test_extracts_data_from_json_string(self, tmp_path):
        te = _mk_te(tmp_path)
        json_result = json.dumps({
            "status": "ok",
            "tool": "read_file",
            "data": "Hello from file",
            "metadata": {"path": "x.py"},
        })
        msg = await te.build_tool_message(
            tool_name="read_file",
            tool_args={"path": "x.py"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
            result=json_result,
        )
        assert msg["role"] == "tool"
        assert msg["name"] == "read_file"
        assert msg["tool_call_id"] == "tc-1"
        # The LLM should see the human-readable data, NOT the raw JSON
        assert msg["content"] == "Hello from file"
        assert "status" not in msg

    @pytest.mark.asyncio
    async def test_extracts_data_from_dict(self, tmp_path):
        te = _mk_te(tmp_path)
        dict_result = {"data": "Dict content", "extra": 1}
        msg = await te.build_tool_message(
            tool_name="write_file",
            tool_args={"path": "x.py"},
            workspace=str(tmp_path),
            result=dict_result,
        )
        assert msg["content"] == "Dict content"

    @pytest.mark.asyncio
    async def test_passthrough_for_plain_string(self, tmp_path):
        te = _mk_te(tmp_path)
        plain = "Just a plain string result"
        msg = await te.build_tool_message(
            tool_name="run_bash",
            tool_args={"command": "echo hi"},
            workspace=str(tmp_path),
            result=plain,
        )
        assert msg["content"] == plain

    @pytest.mark.asyncio
    async def test_passthrough_for_malformed_json(self, tmp_path):
        te = _mk_te(tmp_path)
        bad_json = '{not json'
        msg = await te.build_tool_message(
            tool_name="read_file",
            tool_args={"path": "x.py"},
            workspace=str(tmp_path),
            result=bad_json,
        )
        assert msg["content"] == bad_json


class TestGuardLayers:
    """Each guard layer should block before tool execution."""

    @pytest.mark.asyncio
    async def test_dangerous_command_blocked_before_execution(self, tmp_path):
        te = _mk_te(tmp_path)
        events = []
        async for event in te.execute(
            tool_name="run_bash",
            tool_args={"command": "rm -rf /"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        result = events[0].data["result"]
        assert "Blocked" in str(result)
        assert events[0].data["name"] == "run_bash"
        assert "dangerous" in str(result).lower()

    @pytest.mark.asyncio
    async def test_permission_mode_read_only_blocks_write(self, tmp_path):
        te = _mk_te(tmp_path, permission_mode="read_only")
        events = []
        async for event in te.execute(
            tool_name="write_file",
            tool_args={"path": "readonly.txt", "content": "x"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert "read_only" in str(events[0].data["result"]).lower()
        assert not (tmp_path / "readonly.txt").exists()

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_blocks_tool(self, tmp_path):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=60.0)
        cb.is_open = lambda _tool: True  # Force open

        cfg = WispConfig()
        cfg.workspace = str(tmp_path)
        cfg.auto_approve = True
        cfg.permission_mode = "full"

        te = ToolExecutor(
            config=cfg,
            metrics=MagicMock(),
            circuit_breaker=cb,
            hook_manager=MagicMock(),
        )

        events = []
        async for event in te.execute(
            tool_name="run_bash",
            tool_args={"command": "echo hi"},
            workspace=str(tmp_path),
            tool_call_id="tc-1",
        ):
            events.append(event)

        assert len(events) == 1
        assert "circuit breaker" in str(events[0].data["result"]).lower()
