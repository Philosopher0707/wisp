"""Tests for spawn and fanout tools — role-driven subagent execution."""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from wisp.config import WispConfig
from wisp.tool_executor import ToolExecutor
from wisp.multi_agent.task import SubagentContract, SubagentResult


def _mock_orchestrator(success=True, files=None, output="done", elapsed=1.5,
                       tokens=100, task_id="spawn-generalist"):
    """Build a mock orchestrator that returns a controlled SubagentResult."""
    orch = MagicMock()
    orch.run = AsyncMock(return_value=SubagentResult(
        task_id=task_id,
        success=success,
        output=output,
        files_changed=files or [],
        elapsed_seconds=elapsed,
        tokens_used=tokens,
        error=None if success else "mock failure",
        timed_out=False,
    ))
    orch._run_with_retry = AsyncMock(return_value=SubagentResult(
        task_id=task_id,
        success=success,
        output=output,
        files_changed=files or [],
        elapsed_seconds=elapsed,
        tokens_used=tokens,
        error=None if success else "mock failure",
        timed_out=False,
    ))
    orch.run_parallel = AsyncMock(return_value=[
        SubagentResult(
            task_id=f"fanout-{i}-generalist",
            success=success,
            output=output,
            files_changed=files or [],
            elapsed_seconds=elapsed,
            tokens_used=tokens,
        )
        for i in range(2)
    ])
    return orch


def _mk_te(tmp_path, orch=None):
    """Build a ToolExecutor with mock orchestrator."""
    cfg = WispConfig()
    cfg.workspace = str(tmp_path)
    cfg.auto_approve = True
    return ToolExecutor(
        config=cfg,
        hook_manager=MagicMock(),
        subagent_orchestrator=orch or _mock_orchestrator(),
    )


class TestSpawnTool:
    """Tests for the spawn tool via ToolExecutor._spawn()."""

    @pytest.mark.asyncio
    async def test_spawn_requires_task(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._spawn({}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "requires a 'task'" in data["data"]

    @pytest.mark.asyncio
    async def test_spawn_unknown_role(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._spawn({"task": "do stuff", "role": "nonexistent"}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "Unknown role" in data["data"]

    @pytest.mark.asyncio
    async def test_spawn_no_orchestrator(self, tmp_path):
        cfg = WispConfig()
        cfg.workspace = str(tmp_path)
        te = ToolExecutor(config=cfg)
        result = await te._spawn({"task": "do stuff"}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not available" in data["data"]

    @pytest.mark.asyncio
    async def test_spawn_success_returns_structured_result(self, tmp_path):
        orch = _mock_orchestrator(success=True, files=["a.py"], output="All done")
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"task": "write a.py", "role": "coder"}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "ok"
        inner = data["data"]
        assert inner["ok"] is True
        assert inner["summary"] == "All done"
        assert inner["files"] == ["a.py"]
        assert inner["role"] == "coder"
        assert inner["elapsed_seconds"] == 1.5
        assert inner["error"] is None

    @pytest.mark.asyncio
    async def test_spawn_failure_returns_error(self, tmp_path):
        orch = _mock_orchestrator(success=False, output="")
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"task": "break things"}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "ok"  # tool call succeeded, subagent failed
        inner = data["data"]
        assert inner["ok"] is False
        assert inner["error"] == "mock failure"

    @pytest.mark.asyncio
    async def test_spawn_role_defaults_to_generalist(self, tmp_path):
        orch = _mock_orchestrator()
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"task": "do stuff"}, str(tmp_path))
        data = json.loads(result)
        assert data["data"]["role"] == "generalist"

    @pytest.mark.asyncio
    async def test_spawn_passes_advanced_params(self, tmp_path):
        orch = _mock_orchestrator()
        te = _mk_te(tmp_path, orch)
        await te._spawn({
            "task": "audit",
            "role": "reviewer",
            "timeout_seconds": 90,
            "max_iterations": 5,
            "tools": ["read_file", "git_diff"],
            "output_format": "json",
            "output_schema": {"type": "object"},
            "max_tokens": 2000,
            "auto_retry": False,
        }, str(tmp_path))

        # Verify contract was built correctly
        contract = orch._run_with_retry.call_args[0][0]
        assert contract.role == "reviewer"
        assert contract.timeout_seconds == 90
        assert contract.max_iterations == 5
        assert contract.tools == ["read_file", "git_diff"]
        assert contract.output_format == "json"
        assert contract.output_schema == {"type": "object"}
        assert contract.max_tokens == 2000
        assert contract.max_retries == 0  # auto_retry=False

    @pytest.mark.asyncio
    async def test_spawn_legacy_prompt_alias(self, tmp_path):
        """spawn_subagent used 'prompt' key — verify it works as alias for 'task'."""
        orch = _mock_orchestrator(success=True, output="ok")
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"prompt": "old style task"}, str(tmp_path))
        data = json.loads(result)
        assert data["data"]["ok"] is True

    @pytest.mark.asyncio
    async def test_spawn_wires_hook_manager(self, tmp_path):
        orch = _mock_orchestrator()
        orch.hook_manager = None  # Explicit None — orchestrators start without hooks
        hm = MagicMock()
        cfg = WispConfig()
        cfg.workspace = str(tmp_path)
        cfg.auto_approve = True
        te = ToolExecutor(config=cfg, hook_manager=hm, subagent_orchestrator=orch)
        await te._spawn({"task": "test"}, str(tmp_path))
        assert orch.hook_manager is hm


class TestFanoutTool:
    """Tests for the fanout tool via ToolExecutor._fanout()."""

    @pytest.mark.asyncio
    async def test_fanout_requires_tasks_array(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "requires a 'tasks'" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_empty_tasks(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({"tasks": []}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "requires a 'tasks'" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_task_must_be_dict(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({"tasks": ["not a dict"]}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "must be an object" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_task_requires_task_field(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({"tasks": [{"role": "coder"}]}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "requires a 'task'" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_unknown_role(self, tmp_path):
        te = _mk_te(tmp_path)
        result = await te._fanout({"tasks": [{"task": "do", "role": "invalid"}]}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "unknown role" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_no_orchestrator(self, tmp_path):
        cfg = WispConfig()
        cfg.workspace = str(tmp_path)
        te = ToolExecutor(config=cfg)
        result = await te._fanout({"tasks": [{"task": "do"}]}, str(tmp_path))
        data = json.loads(result)
        assert data["status"] == "error"
        assert "not available" in data["data"]

    @pytest.mark.asyncio
    async def test_fanout_success_returns_aggregated_result(self, tmp_path):
        orch = _mock_orchestrator(success=True, files=["a.py", "b.py"])
        te = _mk_te(tmp_path, orch)
        result = await te._fanout({
            "tasks": [
                {"task": "do A", "role": "coder"},
                {"task": "do B", "role": "tester"},
            ],
            "max_concurrent": 2,
        }, str(tmp_path))

        data = json.loads(result)
        assert data["status"] == "ok"
        inner = data["data"]
        assert inner["ok"] is True
        assert len(inner["results"]) == 2
        assert inner["results"][0]["ok"] is True
        assert "2/2 subagents succeeded" in inner["summary"]
        assert inner["total_elapsed_seconds"] == 3.0  # 2 × 1.5

    @pytest.mark.asyncio
    async def test_fanout_partial_failure(self, tmp_path):
        orch = MagicMock()
        orch.run_parallel = AsyncMock(return_value=[
            SubagentResult(task_id="fanout-0-coder", success=True, output="ok",
                           elapsed_seconds=1.0, tokens_used=50),
            SubagentResult(task_id="fanout-1-tester", success=False, output="",
                           error="test failed", elapsed_seconds=2.0, tokens_used=30),
        ])
        te = _mk_te(tmp_path, orch)
        result = await te._fanout({
            "tasks": [{"task": "do A"}, {"task": "do B"}],
        }, str(tmp_path))

        data = json.loads(result)
        inner = data["data"]
        assert inner["ok"] is False
        assert "1/2 subagents succeeded" in inner["summary"]
        assert inner["results"][0]["ok"] is True
        assert inner["results"][1]["ok"] is False
        assert inner["results"][1]["error"] == "test failed"

    @pytest.mark.asyncio
    async def test_fanout_passes_role_defaults_to_contracts(self, tmp_path):
        orch = MagicMock()
        orch.run_parallel = AsyncMock(return_value=[
            SubagentResult(task_id="fanout-0-coder", success=True, output="ok",
                           elapsed_seconds=1.0),
        ])
        orch.hook_manager = None
        te = _mk_te(tmp_path, orch)
        await te._fanout({
            "tasks": [
                {"task": "write feature", "role": "coder",
                 "timeout_seconds": 300, "max_iterations": 20},
            ],
        }, str(tmp_path))

        contracts = orch.run_parallel.call_args[0][0]
        assert len(contracts) == 1
        c = contracts[0]
        assert c.role == "coder"
        assert c.timeout_seconds == 300
        assert c.max_iterations == 20
        from wisp.multi_agent.roles import ROLE_CONFIGS
        assert c.tools == ROLE_CONFIGS["coder"].allowed_tools


class TestSpawnSubagentLegacy:
    """spawn_subagent tool name routes through _spawn()."""

    @pytest.mark.asyncio
    async def test_legacy_name_works(self, tmp_path):
        orch = _mock_orchestrator(success=True, output="legacy works")
        te = _mk_te(tmp_path, orch)
        result = await te._spawn({"task": "old tool name"}, str(tmp_path))
        data = json.loads(result)
        assert data["data"]["ok"] is True
        assert data["data"]["summary"] == "legacy works"
