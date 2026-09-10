"""Race / interleaving tests: duplicate execution, lost/stale results.

THREAT: randomized timing causes double-runs, budget bypass, or wrong status.
EXPECTED: deterministic outcomes across seeds and injected delays.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from wisp.graph.compat import fan_to_graph
from wisp.graph.types import GraphPolicy, NodeResult, NodeStatus
from .conftest import make_executor


def _jitter_runner(seed: int, log: list, fail_first: set | None = None):
    rng = random.Random(seed)
    counts: dict = {}

    async def run(node, inputs):
        counts[node.id] = counts.get(node.id, 0) + 1
        log.append(("start", node.id))
        await asyncio.sleep(rng.random() * 0.01)
        if fail_first and node.id in fail_first and counts[node.id] == 1:
            log.append(("fail", node.id))
            return NodeResult(node.id, NodeStatus.FAILURE, error_code="F", message="f")
        log.append(("ok", node.id))
        return NodeResult(node.id, NodeStatus.SUCCESS, output={"n": node.id},
                          input_tokens=10, output_tokens=5, cost_usd=0.0001)

    return run, counts


class TestRaces:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("seed", [1, 2, 3, 7, 42])
    async def test_fan_deterministic_outcomes(self, tmp_path, seed):
        log: list = []
        run, counts = _jitter_runner(seed, log)
        g = fan_to_graph("f", [f"i{i}" for i in range(12)])
        ex = make_executor(tmp_path, run, max_concurrency=4)
        ex.register_function("split_work", lambda i: {"ok": True})
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"
        branches = [k for k in r["results_by_node"] if k.startswith("branch-")]
        assert len(branches) == 12
        assert all(counts.get(f"branch-{i}", 0) == 1 for i in range(12))
        assert r["tokens"] == {"input": 120, "output": 60}

    @pytest.mark.asyncio
    async def test_no_duplicate_execution_under_jitter(self, tmp_path):
        for seed in range(10):
            log: list = []
            run, counts = _jitter_runner(seed, log)
            g = fan_to_graph("f", ["a", "b", "c"])
            ex = make_executor(tmp_path, run, max_concurrency=3)
            ex.register_function("split_work", lambda i: {"ok": True})
            await ex.run(g, {}, run_id=f"race-{seed}")
            assert all(v == 1 for k, v in counts.items() if k.startswith("branch-")), seed

    @pytest.mark.asyncio
    async def test_cancel_never_becomes_success(self, tmp_path):
        for seed in range(5):
            log: list = []
            run, _ = _jitter_runner(seed, log)

            async def slow(node, inputs):
                if node.id == "split":
                    return NodeResult("split", NodeStatus.SUCCESS, output={})
                await asyncio.sleep(0.05)
                return await run(node, inputs)

            g = fan_to_graph("f", ["a", "b"])
            ex = make_executor(tmp_path, slow, max_concurrency=2)
            ex.register_function("split_work", lambda i: {"ok": True})
            task = asyncio.create_task(ex.run(g, {}, run_id=f"cs-{seed}"))
            await asyncio.sleep(0.01)
            ex.cancel(f"cs-{seed}")
            r = await asyncio.wait_for(task, timeout=15)
            assert r["status"] == "cancelled", (seed, r["status"])
            from wisp.graph.store import GraphStore as _GS
            assert _GS(workspace=str(tmp_path)).get_run(f"cs-{seed}")["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_budget_holds_under_jitter(self, tmp_path):
        log: list = []
        run, _ = _jitter_runner(9, log)
        g = fan_to_graph("f", [f"i{i}" for i in range(6)])
        g = type(g)(id=g.id, version=g.version, entrypoint=g.entrypoint,
                    nodes=g.nodes, edges=g.edges,
                    policies=GraphPolicy(max_nodes=64, max_cost_usd=0.00025))
        ex = make_executor(tmp_path, run, max_concurrency=6)
        ex.register_function("split_work", lambda i: {"ok": True})
        r = await ex.run(g, {})
        assert r["status"] == "failed" and r["error"] == "budget_exceeded"
