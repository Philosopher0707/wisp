"""Phase 10E: OPT-004 fanout/resource — detection, analysis, safe clamp."""

from __future__ import annotations

import pytest

from wisp.graph.compat import fan_to_graph
from wisp.graph.optimizer import (
    OptimizationContext,
    envelope_le,
    optimize_graph,
    resource_envelope,
    structural_envelope,
)
from wisp.graph.optimizer_passes import resource_pass
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    JoinPolicy,
    NodeContract,
    NodeType,
    RetryPolicy,
)
from wisp.graph.validator import validate_graph


def _agent(nid, tools=("read_file",)):
    return GraphNode(id=nid, type=NodeType.AGENT,
                     contract=NodeContract(id=nid, allowed_tools=tools))


def _e(f, t, reason="x", mapping=None, when=""):
    return EdgeMapping(f, t, reason=reason, mapping=dict(mapping or {}),
                       condition=when)


def _fan(n, policy=None):
    g = fan_to_graph("f", [f"i{i}" for i in range(n)])
    if policy is not None:
        g = Graph(id=g.id, version=g.version, entrypoint=g.entrypoint,
                  nodes=g.nodes, edges=g.edges, policies=policy)
    return g


class TestDetection:
    def test_small_fanout_silent(self):
        r = resource_pass(_fan(2), OptimizationContext())
        assert not any(d["code"] == "OPT-004" and "fanout" in d["reason"]
                       for d in r.diagnostics)

    def test_excessive_fanout_advisory(self):
        g = _fan(4)
        ctx = OptimizationContext(max_fanout=2)
        r = resource_pass(g, ctx)
        assert any("exceeds recommended 2" in d["reason"] for d in r.diagnostics)
        # Topology never serialized: same nodes and edges out.
        assert {n.id for n in r.graph.nodes} == {n.id for n in g.nodes}
        assert {(e.from_node, e.to_node) for e in r.graph.edges} == \
            {(e.from_node, e.to_node) for e in g.edges}

    def test_deep_fanout_measured(self):
        g = _fan(6)
        env = resource_envelope(g)
        assert env["F"] == 6

    def test_wide_fanout_join_inbound(self):
        g = _fan(200)
        ctx = OptimizationContext(max_branches=100)
        r = resource_pass(g, ctx)
        assert any("inbound branches" in d["reason"] for d in r.diagnostics)

    def test_fanout_through_router_untouched(self):
        r_ = GraphNode(id="r", type=NodeType.ROUTER, routes={"x": "b"},
                       default_route="b", contract=NodeContract(id="r"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), r_, _agent("b"), _agent("c")),
                  edges=(_e("a", "r"), EdgeMapping("r", "b", reason="x", condition="x"),
                         EdgeMapping("r", "c", reason="x", condition="y")))
        assert validate_graph(g) == []
        before = {(e.from_node, e.to_node) for e in g.edges}
        r = optimize_graph(g)
        after = {(e.from_node, e.to_node) for e in r.graph.edges}
        assert before <= after

    def test_fanout_with_verifier_preserved(self):
        v = GraphNode(id="v", type=NodeType.VERIFIER,
                      contract=NodeContract(id="v"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b"), v, _agent("s")),
                  edges=(_e("a", "b"), _e("b", "v"), _e("v", "s", when="accept"),
                         _e("a", "s")))
        assert validate_graph(g) == []
        r = optimize_graph(g)
        assert any(n.type == NodeType.VERIFIER for n in r.graph.nodes)

    def test_fanout_with_approval_preserved(self):
        ap = GraphNode(id="ap", type=NodeType.APPROVAL,
                       contract=NodeContract(id="ap"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b"), ap, _agent("s")),
                  edges=(_e("a", "b"), _e("b", "ap"), _e("ap", "s"), _e("a", "s")))
        assert validate_graph(g) == []
        r = optimize_graph(g)
        assert any(n.type == NodeType.APPROVAL for n in r.graph.nodes)

    def test_join_fanout_preserved(self):
        g = _fan(5)
        before = len(g.edges)
        r = optimize_graph(g)
        assert len(r.graph.edges) == before
        assert any(n.type == NodeType.JOIN for n in r.graph.nodes)


class TestResourceAnalysis:
    def test_below_threshold_silent(self):
        g = _fan(2, GraphPolicy(max_concurrency=2))
        r = resource_pass(g, OptimizationContext())
        # No clamp (already minimal); duplicate advisory may fire — topology kept.
        assert not r.changed
        assert {(e.from_node, e.to_node) for e in r.graph.edges} == \
            {(e.from_node, e.to_node) for e in g.edges}

    def test_at_threshold_silent(self):
        g = _fan(3, GraphPolicy(max_concurrency=4))
        # fan(3) = 5 nodes; target min(4, 5) = 4 = current -> silent.
        r = resource_pass(g, OptimizationContext())
        assert not r.changed
        env = resource_envelope(g)
        assert env["C"] == 4

    def test_above_threshold_clamped(self):
        g = _fan(3, GraphPolicy(max_concurrency=32))
        r = resource_pass(g, OptimizationContext())
        assert r.changed
        assert r.graph.policies.max_concurrency == 5  # node count
        assert validate_graph(r.graph) == []

    def test_retry_fanout_bounded(self):
        c = NodeContract(id="b", retry_policy=RetryPolicy(max_attempts=3))
        b = GraphNode(id="b", type=NodeType.AGENT, contract=c)
        g = Graph(id="t", entrypoint="s",
                  nodes=(GraphNode(id="s", type=NodeType.FUNCTION, function="f",
                                   contract=NodeContract(id="s")),
                         b, _agent("c")),
                  edges=(_e("s", "b"), _e("s", "c")))
        r = resource_pass(g, OptimizationContext())
        assert any("envelope" in d["reason"] for d in r.diagnostics)

    def test_overflow_capped(self):
        from wisp.graph.optimizer import _AMP_CAP
        assert _AMP_CAP == 10 ** 12
        # Amplification arithmetic is capped by construction.
        assert min(10 ** 18, _AMP_CAP) == _AMP_CAP

    def test_missing_thresholds_use_defaults(self):
        ctx = OptimizationContext()
        assert ctx.max_fanout == 64 and ctx.max_branches == 128

    def test_bad_thresholds_rejected(self):
        with pytest.raises(ValueError):
            OptimizationContext(max_fanout=0)
        with pytest.raises(ValueError):
            OptimizationContext(max_branches=10 ** 9)

    def test_envelopes(self):
        g = _fan(4)
        assert structural_envelope(g) == (6, 8, 0)
        env = resource_envelope(g)
        assert env["F"] == 4 and env["C"] == 8 and env["T"] == 1
        assert env["X"] == "unknown"
        ok, _ = envelope_le(env, dict(env))
        assert ok

    def test_envelope_rejects_growth(self):
        g = _fan(2)
        env = resource_envelope(g)
        assert not envelope_le(env, {**env, "C": env["C"] + 1})[0]
        assert not envelope_le(env, {**env, "F": env["F"] + 1})[0]
        assert not envelope_le(env, {**env, "X": "changed"})[0]


class TestSafeOptimization:
    def test_node_count_clamp(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b", mapping={"v": "output.v"}),),
                  policies=GraphPolicy(max_concurrency=64))
        r = optimize_graph(g)
        assert r.graph.policies.max_concurrency == 2

    def test_policy_clamp(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b", mapping={"v": "output.v"}),),
                  policies=GraphPolicy(max_concurrency=32))
        ctx = OptimizationContext(policy=GraphPolicy(max_concurrency=6))
        r = optimize_graph(g, context=ctx)
        assert r.graph.policies.max_concurrency == 2  # min(32, 6, 2 nodes)

    def test_deterministic(self):
        g = _fan(5, GraphPolicy(max_concurrency=32))
        r1 = optimize_graph(g)
        r2 = optimize_graph(g)
        assert r1.graph.fingerprint() == r2.graph.fingerprint()

    def test_revalidated(self):
        g = _fan(5, GraphPolicy(max_concurrency=32))
        r = optimize_graph(g)
        assert r.status == "OPTIMIZED"
        assert validate_graph(r.graph) == []

    def test_fingerprint_changes_on_clamp(self):
        g = _fan(3, GraphPolicy(max_concurrency=32))
        before = g.fingerprint()
        r = optimize_graph(g)
        assert r.graph.fingerprint() != before
        # ...and stays stable (proposal binding works).
        r2 = optimize_graph(r.graph)
        assert r2.graph.fingerprint() == r.graph.fingerprint()


class TestUnsafePreserved:
    def test_similar_agents_separate(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b"), _agent("c")),
                  edges=(_e("a", "b"), _e("a", "c")))
        r = optimize_graph(g)
        assert {n.id for n in r.graph.nodes} == {"a", "b", "c"}

    def test_side_effectful_branches_kept(self):
        c = NodeContract(id="w", idempotent=False)
        w = GraphNode(id="w", type=NodeType.AGENT, contract=c)
        g = Graph(id="t", entrypoint="s",
                  nodes=(GraphNode(id="s", type=NodeType.FUNCTION, function="f",
                                   contract=NodeContract(id="s")),
                         w, _agent("c2")),
                  edges=(_e("s", "w"), _e("s", "c2")))
        before = {(e.from_node, e.to_node) for e in g.edges}
        r = optimize_graph(g)
        assert {(e.from_node, e.to_node) for e in r.graph.edges} == before

    def test_mapping_semantics_kept(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b", mapping={"v": "output.v"}),))
        r = optimize_graph(g)
        e = next(iter(r.graph.edges))
        assert e.mapping == {"v": "output.v"}

    def test_retry_policy_untouched(self):
        c = NodeContract(id="b", retry_policy=RetryPolicy(max_attempts=3))
        b = GraphNode(id="b", type=NodeType.AGENT, contract=c)
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), b),
                  edges=(_e("a", "b"),))
        r = optimize_graph(g)
        nb = next(n for n in r.graph.nodes if n.id == "b")
        assert nb.effective_contract().retry_policy.max_attempts == 3

    def test_failure_propagation_kept(self):
        g = _fan(3)
        r = optimize_graph(g)
        # join still waits on all branches (BEST_EFFORT policy intact).
        j = next(n for n in r.graph.nodes if n.type == NodeType.JOIN)
        assert j.join_policy == JoinPolicy.BEST_EFFORT

    def test_runtime_budget_blocks_clamp(self):
        # Serial total (2×300s) exceeds the 100s runtime budget: withhold.
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b", mapping={"v": "output.v"}),),
                  policies=GraphPolicy(max_concurrency=32, max_runtime_s=100.0))
        r = resource_pass(g, OptimizationContext())
        assert not r.changed
        assert any("withheld" in d["reason"] for d in r.diagnostics)
        assert r.graph.policies.max_concurrency == 32

    def test_no_budget_no_block(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b", mapping={"v": "output.v"}),),
                  policies=GraphPolicy(max_concurrency=32))
        r = resource_pass(g, OptimizationContext())
        assert r.changed and r.graph.policies.max_concurrency == 2
