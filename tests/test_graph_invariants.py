"""Graph invariant tests (§44) — property-style checks over generated shapes."""

from __future__ import annotations

import asyncio
import random

import pytest

from wisp.graph.compat import fan_to_graph
from wisp.graph.control import resolve_route
from wisp.graph.executor import GraphExecutor
from wisp.graph.store import GraphStore
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    JoinPolicy,
    NodeContract,
    NodeResult,
    NodeStatus,
    NodeType,
)
from wisp.graph.validator import validate_graph


async def _no_sleep(_: float) -> None:
    return None


def _agent(nid: str, **kw) -> GraphNode:
    return GraphNode(id=nid, type=NodeType.AGENT, contract=NodeContract(id=nid, **kw))


def _recorder(order: list, fail: set[str] | None = None, usage: dict | None = None):
    async def run(node, inputs):
        order.append(node.id)
        if usage is not None:
            usage[node.id] = inputs
        if fail and node.id in fail:
            return NodeResult(node.id, NodeStatus.FAILURE, error_code="X", message="x")
        return NodeResult(node.id, NodeStatus.SUCCESS, output={"id": node.id})
    return run


def _random_dag(rng: random.Random, n: int = 8) -> Graph:
    names = [f"n{i}" for i in range(n)]
    nodes = tuple(_agent(x) for x in names)
    edges = []
    for i in range(1, n):
        for j in range(i):
            if rng.random() < 0.3:
                edges.append(EdgeMapping(names[j], names[i],
                                         reason=f"{names[i]} consumes {names[j]}.output"))
    if not edges:
        edges = [EdgeMapping(names[0], names[1], reason="r")]
    return Graph(id="prop", entrypoint=names[0], nodes=nodes,
                 edges=tuple(edges))


def _reachable_set(g: Graph) -> set[str]:
    adj: dict[str, list[str]] = {}
    for e in g.edges:
        adj.setdefault(e.from_node, []).append(e.to_node)
    seen, stack = {g.entrypoint}, [g.entrypoint]
    while stack:
        for m in adj.get(stack.pop(), []):
            if m not in seen:
                seen.add(m)
                stack.append(m)
    return seen


@pytest.mark.asyncio
async def test_no_node_runs_before_dependencies(tmp_path):
    rng = random.Random(7)
    for trial in range(5):
        g = _random_dag(rng)
        if validate_graph(g):
            continue
        order: list = []
        ws = str(tmp_path / f"t{trial}")
        ex = GraphExecutor(runner=_recorder(order), workspace=ws,
                           store=GraphStore(workspace=ws), sleep=_no_sleep)
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"
        pos = {nid: i for i, nid in enumerate(order)}
        for e in g.edges:
            assert pos[e.from_node] < pos[e.to_node], (e, order)


@pytest.mark.asyncio
async def test_failed_branch_cannot_mutate_siblings(tmp_path):
    g = fan_to_graph("f", ["a", "b", "c"])
    order: list = []
    usage: dict = {}
    ex = GraphExecutor(runner=_recorder(order, {"branch-0"}, usage),
                       workspace=str(tmp_path),
                       store=GraphStore(workspace=str(tmp_path)), sleep=_no_sleep)
    ex.register_function("split_work", lambda i: {"ok": True})
    r = await ex.run(g, {})
    assert r["results_by_node"]["branch-1"]["status"] == "success"
    assert r["results_by_node"]["branch-2"]["status"] == "success"
    # sibling inputs contain no trace of the failed branch output
    assert "branch-0" not in str(usage.get("branch-1", {}))


@pytest.mark.asyncio
async def test_concurrency_cap_holds_under_fan(tmp_path):
    live = {"n": 0}
    peak = {"v": 0}

    async def run(node, inputs):
        live["n"] += 1
        peak["v"] = max(peak["v"], live["n"])
        try:
            await asyncio.sleep(0.005)
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})
        finally:
            live["n"] -= 1

    g = fan_to_graph("f", [f"x{i}" for i in range(20)])
    ex = GraphExecutor(runner=run, workspace=str(tmp_path), max_concurrency=3,
                       store=GraphStore(workspace=str(tmp_path)), sleep=_no_sleep)
    ex.register_function("split_work", lambda i: {"ok": True})
    await ex.run(g, {})
    assert peak["v"] <= 3


@pytest.mark.asyncio
async def test_budget_cap_holds(tmp_path):
    async def run(node, inputs):
        return NodeResult(node.id, NodeStatus.SUCCESS, output={},
                          input_tokens=500, output_tokens=500)

    nodes = tuple(_agent(f"n{i}") for i in range(4))
    edges = tuple(EdgeMapping(f"n{i}", f"n{i+1}", reason="r") for i in range(3))
    g = Graph(id="b", entrypoint="n0", nodes=nodes, edges=edges,
              policies=GraphPolicy(max_tokens=1500))
    ex = GraphExecutor(runner=run, workspace=str(tmp_path),
                       store=GraphStore(workspace=str(tmp_path)), sleep=_no_sleep)
    r = await ex.run(g, {})
    assert r["status"] == "failed" and r["error"] == "budget_exceeded"


@pytest.mark.asyncio
async def test_completed_stays_completed_after_restart(tmp_path):
    ws = str(tmp_path)
    nodes = (_agent("a"), _agent("b"))
    edges = (EdgeMapping("a", "b", reason="b consumes a.output"),)
    g = Graph(id="c", entrypoint="a", nodes=nodes, edges=edges)
    ex = GraphExecutor(runner=_recorder([]), workspace=ws,
                       store=GraphStore(workspace=ws), sleep=_no_sleep)
    r1 = await ex.run(g, {}, run_id="stable")
    before = {k: v["status"] for k, v in r1["results_by_node"].items()}
    order: list = []
    ex2 = GraphExecutor(runner=_recorder(order), workspace=ws,
                        store=GraphStore(workspace=ws), sleep=_no_sleep)
    r2 = await ex2.resume(g, "stable")
    assert {k: v["status"] for k, v in r2["results_by_node"].items()} == before
    assert order == []  # nothing re-executed


def test_unknown_route_gains_no_authority():
    assert resolve_route({"a": "node-a"}, "quarantine", "mystery") == "quarantine"
    assert resolve_route({}, "", "mystery") == ""


def test_join_policy_rejects_positional_merge():
    from wisp.graph.control import collect_successes
    r = {"b": NodeResult("b", NodeStatus.SUCCESS, output={"v": 1}),
         "a": NodeResult("a", NodeStatus.SUCCESS, output={"v": 2})}
    assert list(collect_successes(r)) == ["b", "a"]  # keyed, insertion = completion order
    assert set(collect_successes(r)) == {"a", "b"}


@pytest.mark.asyncio
async def test_rejected_branch_retries_independently(tmp_path):
    calls: list = []

    async def run(node, inputs):
        calls.append(node.id)
        if node.id == "wobble" and calls.count("wobble") < 3:
            return NodeResult("wobble", NodeStatus.FAILURE, error_code="F", message="f")
        return NodeResult(node.id, NodeStatus.SUCCESS, output={})

    from wisp.graph.types import RetryPolicy
    nodes = (_agent("steady"),
             GraphNode(id="wobble", type=NodeType.AGENT,
                       contract=NodeContract(id="wobble",
                                             retry_policy=RetryPolicy(max_attempts=3))),
             GraphNode(id="j", type=NodeType.JOIN, join_policy=JoinPolicy.ALL,
                       contract=NodeContract(id="j")))
    edges = (EdgeMapping("steady", "j", reason="j consumes steady.output"),
             EdgeMapping("wobble", "j", reason="j consumes wobble.output"))
    # entrypoint problem: two roots; add split root
    nodes = (GraphNode(id="root", type=NodeType.FUNCTION, function="id",
                       contract=NodeContract(id="root")),) + nodes
    edges = edges + (EdgeMapping("root", "steady", reason="s consumes root.output"),
                     EdgeMapping("root", "wobble", reason="w consumes root.output"))
    g = Graph(id="rt", entrypoint="root", nodes=nodes, edges=edges)
    ex = GraphExecutor(runner=run, workspace=str(tmp_path),
                       store=GraphStore(workspace=str(tmp_path)), sleep=_no_sleep)
    ex.register_function("id", lambda i: {"ok": True})
    r = await ex.run(g, {})
    assert r["status"] == "succeeded"
    assert calls.count("steady") == 1
    assert calls.count("wobble") == 3
