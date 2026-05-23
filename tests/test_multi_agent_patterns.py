"""Unit tests for composable patterns (map-reduce, vote, chain).

Uses a mock orchestrator to avoid real agent execution.
"""

import pytest

from wisp.multi_agent._patterns import run_map_reduce, run_vote, run_chain
from wisp.multi_agent.task import SubagentContract, SubagentResult


class MockOrchestrator:
    """Minimal orchestrator that returns canned results."""

    def __init__(self, results=None):
        self._results = results or {}
        self._call_count = 0
        from unittest.mock import MagicMock
        self.config = MagicMock()
        self.config.max_context_tokens = 128000
        self.config.chars_per_token = 4

    async def run(self, contract):
        key = contract.name
        if key in self._results:
            return self._results[key]
        # Default success
        return SubagentResult(task_id=contract.name, success=True, output=f"result for {contract.task}")

    async def run_parallel(self, contracts, max_concurrent=4):
        out = []
        for c in contracts:
            out.append(await self.run(c))
        return out


class TestRunMapReduce:

    @pytest.mark.asyncio
    async def test_empty_items(self):
        orch = MockOrchestrator()
        result = await run_map_reduce(orch, "task", [], lambda x: SubagentContract(task=x), "reduce")
        assert not result.success
        assert "No items" in result.error

    @pytest.mark.asyncio
    async def test_basic_map_reduce(self):
        orch = MockOrchestrator()
        items = ["a.py", "b.py"]
        result = await run_map_reduce(
            orch, "Review files", items,
            lambda item: SubagentContract(name=f"review-{item}", task=f"Review {item}"),
            "Synthesize findings",
        )
        assert result.success
        assert "Synthesize findings" in result.task_id or "reducer" in result.task_id

    @pytest.mark.asyncio
    async def test_mapper_failure_with_retry(self):
        """Failed mappers are retried once if retry_failed=True."""
        call_count = 0

        async def flaky_run(contract):
            nonlocal call_count
            call_count += 1
            if contract.name == "review-a.py" and call_count == 1:
                return SubagentResult(task_id=contract.name, success=False, error="fail")
            return SubagentResult(task_id=contract.name, success=True, output="ok")

        orch = MockOrchestrator()
        orch.run = flaky_run

        async def _run_parallel(contracts, max_concurrent=4):
            out = []
            for c in contracts:
                out.append(await orch.run(c))
            return out
        orch.run_parallel = _run_parallel

        items = ["a.py"]
        result = await run_map_reduce(
            orch, "Review", items,
            lambda item: SubagentContract(name=f"review-{item}", task=f"Review {item}"),
            "Synthesize",
            retry_failed=True,
        )
        # The retry should have succeeded
        assert call_count >= 2


class TestRunVote:

    @pytest.mark.asyncio
    async def test_empty_agents(self):
        orch = MockOrchestrator()
        result = await run_vote(orch, "question", [])
        assert not result.success
        assert "No agents" in result.error

    @pytest.mark.asyncio
    async def test_consensus_reached(self):
        orch = MockOrchestrator({
            "voter-0": SubagentResult(task_id="v0", success=True, output="YES"),
            "voter-1": SubagentResult(task_id="v1", success=True, output="YES"),
            "voter-2": SubagentResult(task_id="v2", success=True, output="YES"),
        })
        agents = [SubagentContract(name=f"voter-{i}") for i in range(3)]
        result = await run_vote(orch, "Is this safe?", agents, consensus_threshold=0.6)
        assert result.success  # 3/3 >= 0.6
        assert "REACHED" in result.output

    @pytest.mark.asyncio
    async def test_no_consensus(self):
        orch = MockOrchestrator({
            "voter-0": SubagentResult(task_id="v0", success=True, output="YES"),
            "voter-1": SubagentResult(task_id="v1", success=True, output="NO"),
            "voter-2": SubagentResult(task_id="v2", success=True, output="MAYBE"),
        })
        agents = [SubagentContract(name=f"voter-{i}") for i in range(3)]
        result = await run_vote(orch, "Is this safe?", agents, consensus_threshold=0.6)
        assert not result.success  # No group has >= 60%
        assert "NOT REACHED" in result.output

    @pytest.mark.asyncio
    async def test_with_failures(self):
        orch = MockOrchestrator({
            "voter-0": SubagentResult(task_id="v0", success=True, output="YES"),
            "voter-1": SubagentResult(task_id="v1", success=False, error="crashed"),
            "voter-2": SubagentResult(task_id="v2", success=True, output="YES"),
        })
        agents = [SubagentContract(name=f"voter-{i}") for i in range(3)]
        result = await run_vote(orch, "Is this safe?", agents, consensus_threshold=0.5)
        assert result.success  # 2/3 >= 0.5 (only successful count)


class TestRunChain:

    @pytest.mark.asyncio
    async def test_empty_chain(self):
        orch = MockOrchestrator()
        result = await run_chain(orch, [])
        assert result.success
        assert "empty chain" in result.output

    @pytest.mark.asyncio
    async def test_single_step(self):
        orch = MockOrchestrator()
        contracts = [SubagentContract(name="step-1", task="do something")]
        result = await run_chain(orch, contracts)
        assert result.success
        assert "Chain Complete" in result.output

    @pytest.mark.asyncio
    async def test_context_passing(self):
        orch = MockOrchestrator()
        contracts = [
            SubagentContract(name="writer", task="Write code"),
            SubagentContract(name="reviewer", task="Review code"),
        ]
        result = await run_chain(orch, contracts, pass_context=True)
        assert result.success
        assert "Chain Complete" in result.output

    @pytest.mark.asyncio
    async def test_failure_stops_chain(self):
        async def failing_run(contract):
            if contract.name == "step-2":
                return SubagentResult(task_id="step-2", success=False, error="failed")
            return SubagentResult(task_id=contract.name, success=True, output="ok")

        orch = MockOrchestrator()
        orch.run = failing_run
        contracts = [
            SubagentContract(name="step-1", task="first"),
            SubagentContract(name="step-2", task="second"),
            SubagentContract(name="step-3", task="third"),
        ]
        result = await run_chain(orch, contracts, pass_context=True, continue_on_error=False)
        assert not result.success
        assert "Chain Failed" in result.output
        assert "step-2" in result.output

    @pytest.mark.asyncio
    async def test_continue_on_error(self):
        async def failing_run(contract):
            if contract.name == "step-2":
                return SubagentResult(task_id="step-2", success=False, error="failed")
            return SubagentResult(task_id=contract.name, success=True, output="ok")

        orch = MockOrchestrator()
        orch.run = failing_run
        contracts = [
            SubagentContract(name="step-1", task="first"),
            SubagentContract(name="step-2", task="second"),
            SubagentContract(name="step-3", task="third"),
        ]
        result = await run_chain(orch, contracts, pass_context=True, continue_on_error=True)
        assert not result.success  # Has failures
        assert "Failed steps" in result.output
        assert "step-2" in result.output
        # But step-3 still ran
        assert "step-3" in result.output or "ok" in result.output
