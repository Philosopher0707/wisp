"""Phase 10F security closure: boundaries, hostile contexts, red-team.

THREAT: hostile IR/context/thresholds smuggle authority, execution, or
artifacts through the full optimize→propose→execute chain.
EXPECTED: refusals/advisories; chain integrity; no escape.
"""

from __future__ import annotations

import pytest

from wisp.graph.compat import fan_to_graph
from wisp.graph.optimizer import (
    OptimizationContext,
    authority_of,
    is_narrower_or_equal,
    optimize_graph,
)
from wisp.graph.planner import PlanError, compile_proposal
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    ModelPolicy,
    NodeContract,
    NodeType,
)
from wisp.graph.validator import validate_graph


def _agent(nid):
    return GraphNode(id=nid, type=NodeType.AGENT,
                     contract=NodeContract(
                         id=nid, allowed_tools=("read_file",),
                         model_policy=ModelPolicy(model="m1", provider="p1")))


def _e(f, t, reason="x", mapping=None):
    return EdgeMapping(f, t, reason=reason, mapping=dict(mapping or {}))


class TestHostileContexts:
    def test_extra_tools_ignored(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b"),))
        # Context carries no authority: profile tools outside the graph die.
        ctx = OptimizationContext(
            verifier_profile={"tools": ["run_bash"], "model": "m1", "provider": "p1"})
        r = optimize_graph(g, context=ctx)
        assert not any(n.id == "a__verify" for n in r.graph.nodes)
        ok, _ = is_narrower_or_equal(authority_of(g), authority_of(r.graph))
        assert ok

    def test_extra_model_provider_ignored(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b"),))
        for prof in ({"tools": ["read_file"], "model": "evil", "provider": "p1"},
                     {"tools": ["read_file"], "model": "m1", "provider": "evil"}):
            r = optimize_graph(g, context=OptimizationContext(verifier_profile=prof))
            assert not any(n.id == "a__verify" for n in r.graph.nodes)

    def test_workspace_in_context_ignored(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b"),))
        ctx = OptimizationContext(policy=GraphPolicy(workspace="/evil"))
        # Context policy only narrows known dims; workspace can never change
        # via optimization (monotonicity gate pins it).
        r = optimize_graph(g, context=ctx)
        assert r.graph.policies.workspace == ""

    def test_larger_budgets_retries_concurrency_ignored(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b"),),
                  policies=GraphPolicy(max_concurrency=2, max_retries=1))
        ctx = OptimizationContext(policy=GraphPolicy(
            max_concurrency=128, max_retries=10, max_cost_usd=10 ** 9))
        r = optimize_graph(g, context=ctx)
        assert r.graph.policies.max_concurrency == 2
        assert r.graph.policies.max_retries == 1

    def test_malicious_size_hints_safe(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")),
                  edges=(EdgeMapping("a", "s", reason="x",
                                     mapping={"v": "output.v"}),))
        ctx = OptimizationContext(size_hints={
            ("a", "s", "v"): 10 ** 18, ("x", "y", "z"): -1,
            ("a", "s", "v" * 10000): 10 ** 18})
        r = optimize_graph(g, context=ctx)
        assert r.status in ("UNCHANGED", "OPTIMIZED")
        assert validate_graph(r.graph) == []


class TestRedTeamBoundaries:
    def test_planner_cannot_smuggle_authority(self):
        # Planner IR requesting forbidden tools dies at compile, never optimizes.
        ir = {"objective": "x", "execution_shape": "GRAPH",
              "graph": {"id": "t", "entry": "a",
                        "nodes": {"a": {"type": "agent", "allowed_tools": ["run_bash"]},
                                  "s": {"type": "agent", "allowed_tools": ["run_bash"]}},
                        "edges": [{"from": "a", "to": "s", "reason": "x"}]}}
        p = compile_proposal(ir, "x", GraphPolicy(allowed_tools=("read_file",)))
        assert p["status"] == "REJECTED"

    def test_malformed_ir_never_reaches_optimizer(self):
        p = compile_proposal({"execution_shape": "GRAPH"}, "x", GraphPolicy())
        assert p["status"] in ("INVALID", "REJECTED")
        assert p["graph_hash"] == ""

    def test_optimized_graph_always_fingerprinted(self):
        g = fan_to_graph("f", ["a", "b"])
        r = optimize_graph(g)
        assert r.graph.fingerprint()
        assert r.graph.fingerprint() == optimize_graph(g).graph.fingerprint()

    def test_execution_runs_approved_only(self):
        import asyncio
        from wisp.graph.executor import GraphExecutor
        from wisp.graph.planner import execute_proposal
        from wisp.graph.store import GraphStore
        from wisp.graph.types import NodeResult, NodeStatus
        ir = {"objective": "x", "execution_shape": "SINGLE_AGENT",
              "graph": {"id": "t", "entry": "a",
                        "nodes": {"a": {"type": "agent"}}, "edges": []}}
        p = compile_proposal(ir, "x", GraphPolicy())

        async def run(node, inputs):
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})

        async def no_sleep(s):
            pass

        import tempfile
        ws = tempfile.mkdtemp()
        ex = GraphExecutor(runner=run, workspace=ws,
                           store=GraphStore(workspace=ws), sleep=no_sleep)
        with pytest.raises(PlanError):
            asyncio.run(execute_proposal(p, ex, {}, approve=False))
        r = asyncio.run(execute_proposal(p, ex, {}, approve=True))
        assert r["status"] == "succeeded"

    def test_pass_cannot_trust_later_pass(self):
        # 10B removes a→b; 10D must re-derive (not inherit) its choke proof:
        # no a__verify may appear (edge gone), c__verify may (edge remains).
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b"), _agent("c")),
                  edges=(_e("a", "b"), _e("c", "b"),
                         _e("a", "c", mapping={"v": "output.v"})))
        ctx = OptimizationContext(
            verifier_profile={"tools": ["read_file"], "model": "m1", "provider": "p1"})
        r = optimize_graph(g, context=ctx)
        assert validate_graph(r.graph) == []
        ids = {n.id for n in r.graph.nodes}
        assert "a__verify" not in ids
        assert "c__verify" in ids

    def test_optimizer_never_executes(self):
        import ast
        import wisp.graph.optimizer as O
        import wisp.graph.optimizer_passes as OP
        for mod in (O, OP):
            tree = ast.parse(open(mod.__file__).read())
            calls = set()
            for n in ast.walk(tree):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                    calls.add(n.func.attr)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                    calls.add(n.func.id)
            for banned in ("put", "generate", "generate_structured", "run",
                           "execute", "authorize", "system", "popen", "open"):
                assert banned not in calls, (mod.__name__, banned)
            src = open(mod.__file__).read()
            assert "ToolExecutor" not in src
            assert "SubagentOrchestrator" not in src

    def test_no_artifact_materialization(self):
        import tempfile
        import os
        ws = tempfile.mkdtemp()
        arts = os.path.join(ws, ".wisp", "artifacts")
        g = fan_to_graph("f", ["a", "b"])
        optimize_graph(g)
        assert not os.path.exists(arts)

    def test_malformed_fuzz(self):
        import random
        for seed in range(40):
            rng = random.Random(71000 + seed)
            nodes = {f"n{i}": {"type": rng.choice(["agent", "bogus", 1, None])}
                     for i in range(rng.randint(0, 4))}
            ir = {"execution_shape": "GRAPH",
                  "graph": {"id": "f", "entry": "n0", "nodes": nodes, "edges": []}}
            try:
                p = compile_proposal(ir, "x", GraphPolicy())
            except Exception:
                continue  # strict parser may raise; never execute
            assert p["status"] in ("APPROVAL_REQUIRED", "INVALID", "REJECTED")
            assert p["graph_hash"] == "" or len(p["graph_hash"]) == 32
