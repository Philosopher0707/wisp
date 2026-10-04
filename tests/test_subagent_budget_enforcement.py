"""A subagent's resource budget is enforced in the run loop, on both execution paths.

Budget *construction* was tested; the loop that spends it was not, and the string `[BUDGET EXHAUSTED]` appeared in no
test. That is how the meter came to count only tool results while a child streaming 200k characters with
`max_tokens=500` finished successfully. These tests drive the real `SubagentRunner` with scripted event streams, on
both paths: a directly built `WispAgentCore` and an injected `AgentRuntime` (`_run_via_runtime`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wisp.config import WispConfig
from wisp.multi_agent._runner import SubagentRunner
from wisp.multi_agent.resource_budget import ResourceBudget
from wisp.multi_agent.task import SubagentContract

PATHS = ["core", "runtime"]


def _config() -> WispConfig:
    return WispConfig().replace(
        model="test-model", provider="ollama", workspace="/tmp", chars_per_token=4,
        max_context_tokens=128000, permission_mode="full", ollama_url="http://localhost:11434",
    )


def _contract(**kw) -> SubagentContract:
    return SubagentContract(
        name="t", task="do it", role="coder", tools=["read_file"], timeout_seconds=30,
        max_iterations=50, auto_approve=True, **kw,
    )


class _Script:
    """A scripted turn that also records how far the runner consumed it."""

    def __init__(self, events):
        self.events = list(events)
        self.consumed = 0

    async def play(self):
        for ev in self.events:
            self.consumed += 1
            yield ev


def _drive(path: str, script: _Script, **contract_kw):
    contract = _contract(**contract_kw)
    if path == "core":
        class Core:
            def __init__(self, **_):
                pass

            def turn(self, session, task):
                return script.play()

        runner = SubagentRunner(_config(), Path("/tmp"))
        with patch("wisp.providers.factory.ProviderFactory") as pf, patch("wisp.core.engine.WispAgentCore", Core):
            pf.return_value.from_config.return_value = MagicMock()
            return asyncio.run(runner.run(contract, "/tmp", "prompt"))

    class Runtime:
        async def get_or_create_session(self, sid, model, ws):
            return {"id": sid, "model": model, "workspace": ws, "messages": [], "compaction_history": [],
                    "created_at": "2024-01-01T00:00:00", "updated_at": "2024-01-01T00:00:00"}

        def run_turn(self, session, prompt):
            return script.play()

    runner = SubagentRunner(_config(), Path("/tmp"), agent_runtime=Runtime())
    return asyncio.run(runner.run(contract, "/tmp", "prompt"))


def _tool_round(i: int, size: int = 4000):
    return [
        {"type": "tool_call", "name": "read_file", "arguments": {"path": f"f{i}"}},
        {"type": "tool_result", "name": "read_file", "result": "x" * size},
    ]


@pytest.mark.parametrize("path", PATHS)
class TestToolResultsAreMetered:
    def test_results_over_the_token_budget_stop_the_child(self, path):
        script = _Script([ev for i in range(10) for ev in _tool_round(i)] + [{"type": "content", "text": "done"}, {"type": "done"}])
        r = _drive(path, script, max_tokens=2500)  # ~1000 tokens per result at 4 chars/token
        assert not r.success
        assert r.output.startswith("[BUDGET EXHAUSTED]")
        assert "Token budget exhausted" in r.output
        assert script.consumed < len(script.events), "the child kept running after its budget was spent"

    def test_the_tool_call_cap_stops_the_child(self, path):
        script = _Script([ev for i in range(10) for ev in _tool_round(i, 40)] + [{"type": "done"}])
        r = _drive(path, script, metadata={"_budget": ResourceBudget(max_tool_calls=3)})
        assert not r.success
        assert "Tool call budget exhausted: 3/3" in r.output


@pytest.mark.parametrize("path", PATHS)
class TestStreamedOutputIsMetered:
    def test_a_child_that_only_streams_is_stopped_at_its_token_budget(self, path):
        """The reproduced gap: max_tokens=500 and ~50,000 tokens of streamed text finished successfully."""
        script = _Script([{"type": "content", "text": "y" * 4000} for _ in range(50)] + [{"type": "done"}])
        r = _drive(path, script, max_tokens=500)
        assert not r.success
        assert "[BUDGET EXHAUSTED]" in r.output
        assert script.consumed <= 3, f"consumed {script.consumed} of 51 events"

    def test_many_tiny_deltas_still_add_up(self, path):
        """Streaming providers send small deltas; a per-delta `len // 4` floors every one of them to zero."""
        script = _Script([{"type": "content", "text": "z"} for _ in range(3000)] + [{"type": "done"}])
        r = _drive(path, script, max_tokens=100)  # ~400 characters
        assert not r.success
        assert "[BUDGET EXHAUSTED]" in r.output
        assert script.consumed < 600

    def test_the_text_already_produced_is_not_thrown_away(self, path):
        script = _Script([{"type": "content", "text": "finding: auth bypass in login.py. "}] +
                         [{"type": "content", "text": "y" * 4000} for _ in range(10)] + [{"type": "done"}])
        r = _drive(path, script, max_tokens=500)
        assert "finding: auth bypass in login.py" in r.output
        assert "[BUDGET EXHAUSTED]" in r.output

    def test_a_child_inside_its_budget_is_unaffected(self, path):
        script = _Script([{"type": "content", "text": "short report"}, {"type": "done"}])
        r = _drive(path, script, max_tokens=5000)
        assert r.success and "short report" in r.output

    def test_no_budget_means_no_limit(self, path):
        script = _Script([{"type": "content", "text": "y" * 4000} for _ in range(50)] + [{"type": "done"}])
        r = _drive(path, script)
        assert r.success
        assert script.consumed == 51
