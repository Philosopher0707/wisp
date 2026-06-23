"""Tests for subagent cost estimation."""

from __future__ import annotations

import pytest
from pathlib import Path

from wisp.config import WispConfig
from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
from wisp.multi_agent.task import SubagentContract


@pytest.fixture
def orchestrator(tmp_path):
    config = WispConfig()
    config = config.replace(workspace=str(tmp_path))
    return SubagentOrchestrator(config=config, workspace=tmp_path)


class TestEstimateCost:
    def test_basic_estimation(self, orchestrator):
        contract = SubagentContract(
            task="Read auth.py and summarize its structure",
            max_iterations=15,
            timeout_seconds=120.0,
        )
        est = orchestrator.estimate_cost(contract)
        assert "estimated_input_tokens" in est
        assert "estimated_output_tokens" in est
        assert "estimated_total_tokens" in est
        assert "estimated_wall_time_seconds" in est
        assert "estimated_tool_calls" in est
        assert "confidence" in est

    def test_total_is_sum_of_input_and_output(self, orchestrator):
        contract = SubagentContract(task="Do something", max_iterations=10)
        est = orchestrator.estimate_cost(contract)
        assert est["estimated_total_tokens"] == est["estimated_input_tokens"] + est["estimated_output_tokens"]

    def test_more_iterations_means_more_output(self, orchestrator):
        low = SubagentContract(task="task", max_iterations=5)
        high = SubagentContract(task="task", max_iterations=30)
        est_low = orchestrator.estimate_cost(low)
        est_high = orchestrator.estimate_cost(high)
        assert est_high["estimated_output_tokens"] > est_low["estimated_output_tokens"]

    def test_context_files_increase_input_tokens(self, orchestrator):
        without = SubagentContract(task="task", max_iterations=10)
        with_files = SubagentContract(
            task="task", max_iterations=10,
            context_files=["src/auth.py", "src/utils.py", "src/api.py"],
        )
        est_without = orchestrator.estimate_cost(without)
        est_with = orchestrator.estimate_cost(with_files)
        assert est_with["estimated_input_tokens"] > est_without["estimated_input_tokens"]

    def test_wall_time_bounded_by_timeout(self, orchestrator):
        contract = SubagentContract(
            task="task", max_iterations=100, timeout_seconds=30.0,
        )
        est = orchestrator.estimate_cost(contract)
        assert est["estimated_wall_time_seconds"] <= 30.0

    def test_confidence_starts_low(self, orchestrator):
        contract = SubagentContract(task="task", max_iterations=10)
        est = orchestrator.estimate_cost(contract)
        assert est["confidence"] == "low"


class TestEstimateParallelCost:
    def test_basic_parallel_estimation(self, orchestrator):
        contracts = [
            SubagentContract(name="a", task="task A", max_iterations=10),
            SubagentContract(name="b", task="task B", max_iterations=15),
            SubagentContract(name="c", task="task C", max_iterations=5),
        ]
        est = orchestrator.estimate_parallel_cost(contracts)
        assert est["contracts"] == 3
        assert est["total_estimated_tokens"] > 0
        assert len(est["per_contract"]) == 3

    def test_parallel_wall_time_is_max_not_sum(self, orchestrator):
        contracts = [
            SubagentContract(name="a", task="task A", max_iterations=10, timeout_seconds=50),
            SubagentContract(name="b", task="task B", max_iterations=20, timeout_seconds=100),
        ]
        est = orchestrator.estimate_parallel_cost(contracts)
        individual_times = [e["estimated_wall_time_seconds"] for e in est["per_contract"]]
        # Parallel wall time should be max of individual, not sum
        assert est["estimated_wall_time_seconds"] == max(individual_times)
        assert est["estimated_wall_time_seconds"] < sum(individual_times)

    def test_empty_contracts(self, orchestrator):
        est = orchestrator.estimate_parallel_cost([])
        assert est["contracts"] == 0
        assert est["total_estimated_tokens"] == 0
