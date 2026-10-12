"""A graph run whose correction cycle is exhausted has failed.

The final verdict looked only at sink nodes. When the verifier rejected on every round, the correction target was marked FAILURE (`CYCLE_BOUND`) and the
only sink (`publish`, reached on accept) was skipped as LANE_UNTAKEN, which counts as benign, so the run was recorded as `succeeded` while nothing had been
accepted. Seen first through the REPL (2026-10-10: "correction cycle exhausted" with `verification: REJECT` and a green tick).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wisp.graph.executor import GraphExecutor
from wisp.graph.store import GraphStore
from wisp.graph.types import CycleSpec, EdgeMapping, Graph, GraphNode, GraphPolicy, NodeContract, NodeResult, NodeStatus, NodeType


async def _no_sleep(_s: float) -> None:
    return None


def _graph(max_iterations: int = 2) -> Graph:
    nodes = (
        GraphNode(id="gen", type=NodeType.AGENT, contract=NodeContract(id="gen"),
                  cycle=CycleSpec(entry="gen", body=("ver",), exit_gate="ver", max_iterations=max_iterations)),
        GraphNode(id="ver", type=NodeType.VERIFIER, contract=NodeContract(id="ver")),
        GraphNode(id="publish", type=NodeType.AGENT, contract=NodeContract(id="publish")),
    )
    edges = (
        EdgeMapping("gen", "ver", reason="ver consumes gen.output"),
        EdgeMapping("ver", "gen", reason="gen consumes ver.corrections", condition="reject.gen"),
        EdgeMapping("ver", "publish", reason="publish on accept", condition="accept"),
    )
    return Graph(id="cyc", entrypoint="gen", nodes=nodes, edges=edges, policies=GraphPolicy(max_concurrency=4))


def _runner(accept_on_round: int | None, calls: list[str]):
    async def run(node, inputs):
        calls.append(node.id)
        if node.id == "ver":
            accepted = accept_on_round is not None and calls.count("gen") >= accept_on_round
            return NodeResult("ver", NodeStatus.SUCCESS, output={"decision": "ALLOW" if accepted else "REJECT", "lane": "" if accepted else "gen"})
        return NodeResult(node.id, NodeStatus.SUCCESS, output={"v": "x"})

    return run


def _executor(tmp_path: Path, run) -> GraphExecutor:
    ws = str(tmp_path)
    return GraphExecutor(runner=run, workspace=ws, store=GraphStore(workspace=ws), sleep=_no_sleep)


def _run(tmp_path: Path, accept_on_round: int | None, max_iterations: int = 2, run_id: str = "run-cyc"):
    calls: list[str] = []
    ex = _executor(tmp_path, _runner(accept_on_round, calls))
    return ex, asyncio.run(ex.run(_graph(max_iterations), {}, run_id=run_id)), calls


class TestAnExhaustedCycleFailsTheRun:
    def test_the_run_is_failed(self, tmp_path):
        _, result, _ = _run(tmp_path, accept_on_round=None)
        assert result["status"] == "failed", result
        assert result["results_by_node"]["gen"]["error_code"] == "CYCLE_BOUND"

    def test_the_bound_is_honoured(self, tmp_path):
        _, _, calls = _run(tmp_path, accept_on_round=None, max_iterations=2)
        assert calls.count("gen") == 3  # the first round and two corrections

    def test_the_store_and_the_event_say_failed(self, tmp_path):
        ex, result, _ = _run(tmp_path, accept_on_round=None)
        row = GraphStore(workspace=str(tmp_path)).get_run("run-cyc")
        assert row is not None and row["status"] == "failed" and "gen" in (row["error"] or ""), dict(row) if row else row
        kinds = [e["type"] for e in GraphStore(workspace=str(tmp_path)).events("run-cyc")]
        assert "graph.failed" in kinds and "graph.completed" not in kinds

    def test_a_resume_of_the_failed_run_is_still_failed(self, tmp_path):
        _, first, _ = _run(tmp_path, accept_on_round=None)
        calls: list[str] = []
        again = asyncio.run(_executor(tmp_path, _runner(None, calls)).resume(_graph(), "run-cyc"))
        assert first["status"] == "failed" and again["status"] == "failed", again
        assert calls.count("gen") == 3, calls  # the cycle was retried with a fresh budget, not skipped as already done

    def test_a_resume_after_the_verifier_accepts_succeeds_and_stays_succeeded(self, tmp_path):
        _run(tmp_path, accept_on_round=None)
        calls: list[str] = []
        again = asyncio.run(_executor(tmp_path, _runner(1, calls)).resume(_graph(), "run-cyc"))
        assert again["status"] == "succeeded" and calls[:2] == ["gen", "ver"] and "publish" in calls, (again, calls)
        later: list[str] = []
        third = asyncio.run(_executor(tmp_path, _runner(None, later)).resume(_graph(), "run-cyc"))
        assert third["status"] == "succeeded" and later == [], (third, later)  # the old marker is superseded by the later success


class TestACycleThatConvergesStillSucceeds:
    @pytest.mark.parametrize("accept_on_round", [1, 2, 3])
    def test_accepted_within_the_bound(self, tmp_path, accept_on_round):
        _, result, calls = _run(tmp_path, accept_on_round=accept_on_round, max_iterations=2)
        assert result["status"] == "succeeded", result
        assert result["results_by_node"]["publish"]["status"] == "success" and calls.count("gen") == accept_on_round
