"""Fanout resilience and honesty — the live-run failure modes.

From a real NVIDIA session (2026-08-25): six children spawned against
vague relative paths ('autopipe/core' instead of 'active/autopipe/...')
all failed fast, were announced with ✓ anyway, the 429 rate limit killed
the parent turn, and the aggregate rendered as a raw JSON dump. These
tests pin each fix at its seam.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
from wisp.multi_agent.task import SubagentContract


def _child_config():
    from wisp.config import WispConfig

    return WispConfig()


# ═══════════════════════════════════════════════════════════════════
# F1: transient failures (429 / rate limit) retry inside run_parallel
# ═══════════════════════════════════════════════════════════════════


class TestTransientRetry:
    def _orch(self, tmp_path):
        return SubagentOrchestrator(config=_child_config(), workspace=tmp_path)

    def test_429_failure_is_retried_and_succeeds(self, tmp_path):
        o = self._orch(tmp_path)
        contract = SubagentContract(name="f-0-coder", role="coder", task="t")
        calls = {"n": 0}

        async def flaky_run(c):
            calls["n"] += 1
            if calls["n"] == 1:
                return SubagentResult_fail(c.name, "API error 429: Too Many Requests")
            return SubagentResult_ok(c.name)

        o.run = flaky_run  # type: ignore[method-assign]
        results = asyncio.run(o.run_parallel([contract], max_concurrent=2))
        assert calls["n"] == 2, "transient failure was not retried"
        assert results[0].success is True

    def test_permanent_failure_not_retried(self, tmp_path):
        o = self._orch(tmp_path)
        contract = SubagentContract(name="f-0-coder", role="coder", task="t")
        calls = {"n": 0}

        async def failing_run(c):
            calls["n"] += 1
            return SubagentResult_fail(c.name, "Path not found: /nope")

        o.run = failing_run  # type: ignore[method-assign]
        results = asyncio.run(o.run_parallel([contract], max_concurrent=2))
        assert calls["n"] == 1, "permanent failure must not burn retries"
        assert results[0].success is False

    def test_retry_cap_respected(self, tmp_path):
        o = self._orch(tmp_path)
        contract = SubagentContract(name="f-0-coder", role="coder", task="t")
        calls = {"n": 0}

        async def always_429(c):
            calls["n"] += 1
            return SubagentResult_fail(c.name, "API error 429")

        o.run = always_429  # type: ignore[method-assign]
        results = asyncio.run(o.run_parallel([contract], max_concurrent=2))
        assert calls["n"] == 3, f"expected 1 attempt + 2 retries, got {calls['n']}"
        assert results[0].success is False


def SubagentResult_ok(name):
    from wisp.multi_agent.task import SubagentResult

    return SubagentResult(task_id=name, success=True, output="done",
                          elapsed_seconds=1.0)


def SubagentResult_fail(name, error):
    from wisp.multi_agent.task import SubagentResult

    return SubagentResult(task_id=name, success=False, output="",
                          error=error, elapsed_seconds=0.5)


# ═══════════════════════════════════════════════════════════════════
# F2: children get workspace grounding in their task text
# ═══════════════════════════════════════════════════════════════════


class TestChildGrounding:
    @pytest.mark.asyncio
    async def test_fanout_tasks_carry_workspace_root(self, tmp_path):
        from wisp.tool_executor import ToolExecutor

        captured = []

        class FakeOrch:
            async def run_parallel(self, contracts, max_concurrent=4):
                captured.extend(contracts)
                return []

        ex = ToolExecutor(_child_config(), subagent_orchestrator=FakeOrch())
        await ex._fanout(
            {"tasks": [{"task": "Analyze autopipe/core", "role": "coder"}]},
            str(tmp_path),
        )
        assert captured, "run_parallel never called"
        task_text = captured[0].task
        assert str(tmp_path) in task_text, (
            f"child task lacks workspace root: {task_text!r}"
        )
        assert "relative" in task_text.lower()

    @pytest.mark.asyncio
    async def test_schema_description_mentions_full_paths(self):
        from wisp.tools.registry import TOOL_SCHEMAS

        # TOOL_SCHEMAS is OpenAI wire format: {"type": "function",
        # "function": {name, description, parameters}}
        fanout = next(
            s.get("function", s) for s in TOOL_SCHEMAS
            if s.get("function", s).get("name") == "fanout"
        )
        desc = json.dumps(fanout)
        assert "path" in desc.lower()
