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
            {"tasks": [{"task": "Analyze autopipe/core", "role": "coder"}],
             "mode": "blocking"},
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


class TestChildToolFiltering:
    """Children must not be advertised tools their mode will hard-block."""

    def test_auto_edit_drops_bash_git_spawn(self):
        from wisp.infra.policy_engine import filter_allowed_for_mode

        allowed = filter_allowed_for_mode(
            "auto_edit",
            ["read_file", "write_file", "run_bash", "git_commit",
             "spawn", "fanout", "web_search"],
        )
        assert "run_bash" not in allowed
        assert "git_commit" not in allowed
        assert "spawn" not in allowed
        assert "fanout" not in allowed
        assert "read_file" in allowed
        assert "web_search" in allowed

    def test_full_mode_keeps_everything(self):
        from wisp.infra.policy_engine import filter_allowed_for_mode

        tools = ["read_file", "run_bash", "fanout"]
        assert filter_allowed_for_mode("full", tools) == tools

    def test_read_only_reduces_to_safe_reads(self):
        from wisp.infra.policy_engine import filter_allowed_for_mode

        allowed = filter_allowed_for_mode(
            "read_only", ["read_file", "write_file", "run_bash", "recall"],
        )
        assert allowed == ["read_file", "recall"]

    def test_effective_child_tools_all_expands_and_filters(self):
        from wisp.multi_agent._runner import _effective_child_tools

        tools = _effective_child_tools(None, "auto_edit")
        assert tools, "all-tools path must expand to schema names"
        assert "run_bash" not in tools
        assert "read_file" in tools
        assert "web_search" in tools

    def test_effective_child_tools_explicit_list_still_filtered(self):
        from wisp.multi_agent._runner import _effective_child_tools

        tools = _effective_child_tools(["read_file", "run_bash"], "auto_edit")
        assert tools == ["read_file"]


class TestStartedLineDetail:
    """task_started lines must show task intent, not preamble boilerplate."""

    def _started_detail(self, description: str) -> str:
        from wisp.multi_agent.task import OrchestratorEvent
        from wisp.tool_executor import orchestrator_event_to_agent_event

        ev = orchestrator_event_to_agent_event(
            OrchestratorEvent(
                event_type="task_started",
                task_id="child-0",
                payload={"role": "researcher", "description": description},
            )
        )
        return str(ev.data.get("detail", ""))

    def test_grounding_preamble_stripped_not_truncated_mid_sentence(self):
        grounded = (
            "[Workspace root: /Users/philosopher] Paths are relative to this "
            "root, exactly as they appear in the parent conversation. If a "
            "path is not found, list_files from the workspace root to locate "
            "it before proceeding.\n\nResearch Vespa ranking phases"
        )
        detail = self._started_detail(grounded)
        assert "[Workspace root" not in detail
        assert "Paths are relative" not in detail
        assert detail.startswith("Research Vespa ranking phases")

    def test_long_task_gets_ellipsis_not_hard_cut(self):
        detail = self._started_detail("word " * 60)
        assert len(detail) <= 101
        assert detail.endswith("…")

    def test_short_task_verbatim(self):
        assert self._started_detail("Tiny task") == "Tiny task"

    def test_whitespace_collapsed(self):
        detail = self._started_detail("a\n\n  b\tc")
        assert detail == "a b c"
