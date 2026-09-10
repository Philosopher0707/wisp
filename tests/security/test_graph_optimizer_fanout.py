"""Phase 10E adversarial: authority/resource escalation, boundary, determinism.

THREAT: hostile IR/context/thresholds widen concurrency, budgets, retries,
authority, or smuggle execution into the compile-time pass.
EXPECTED: reductions only; escalation refused; boundary holds; deterministic.
"""

from __future__ import annotations

import pytest

from wisp.graph.compat import fan_to_graph
from wisp.graph.optimizer import (
    OptimizationContext,
    authority_of,
    envelope_le,
    is_narrower_or_equal,
    optimize_graph,
    resource_envelope,
    structural_envelope,
)
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    NodeContract,
    NodeType,
    RetryPolicy,
)
from wisp.graph.validator import validate_graph


def _agent(nid):
    return GraphNode(id=nid, type=NodeType.AGENT,
                     contract=NodeContract(id=nid, allowed_tools=("read_file",)))


def _e(f, t, reason="x", when=""):
    return EdgeMapping(f, t, reason=reason, condition=when)


class TestEscalationRefused:
    def _graph(self):
        return Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                     edges=(_e("a", "b"),),
                     policies=GraphPolicy(max_concurrency=2))

    def test_concurrency_increase_impossible(self):
        # Even a hostile policy claiming more cannot raise the graph bound:
        # target = min(graph, policy, nodes) only ever lowers.
        r = optimize_graph(self._graph(),
                           context=OptimizationContext(
                               policy=GraphPolicy(max_concurrency=128)))
        assert r.graph.policies.max_concurrency == 2

    def test_budget_increase_impossible(self):
        g = self._graph()
        r = optimize_graph(g)
        assert r.graph.policies.max_cost_usd is None
        assert r.graph.policies.max_tokens is None

    def test_retry_increase_impossible(self):
        c = NodeContract(id="b", retry_policy=RetryPolicy(max_attempts=2))
        b = GraphNode(id="b", type=NodeType.AGENT, contract=c)
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), b),
                  edges=(_e("a", "b"),))
        r = optimize_graph(g)
        nb = next(n for n in r.graph.nodes if n.id == "b")
        assert nb.effective_contract().retry_policy.max_attempts == 2

    def test_authority_expansion_impossible(self):
        g = self._graph()
        before = authority_of(g)
        r = optimize_graph(g)
        ok, _ = is_narrower_or_equal(before, authority_of(r.graph))
        assert ok

    def test_approval_removal_impossible(self):
        ap = GraphNode(id="ap", type=NodeType.APPROVAL,
                       contract=NodeContract(id="ap"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), ap, _agent("s")),
                  edges=(_e("a", "ap"), _e("ap", "s")))
        r = optimize_graph(g)
        assert any(n.type == NodeType.APPROVAL for n in r.graph.nodes)

    def test_verifier_removal_impossible(self):
        v = GraphNode(id="v", type=NodeType.VERIFIER,
                      contract=NodeContract(id="v"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), v, _agent("s")),
                  edges=(_e("a", "v"), _e("v", "s", )))
        r = optimize_graph(g)
        assert any(n.type == NodeType.VERIFIER for n in r.graph.nodes)

    def test_malicious_thresholds_rejected_or_clamped(self):
        with pytest.raises(ValueError):
            OptimizationContext(max_fanout=10 ** 9)
        with pytest.raises(ValueError):
            OptimizationContext(max_branches=-5)
        with pytest.raises(ValueError):
            OptimizationContext(inline_threshold_bytes=10 ** 12)

    def test_graph_metadata_cannot_raise_limits(self):
        # Absurd policy values inside the graph are rejected by validation,
        # so the pass never sees them (defense in depth, not trust).
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b"),),
                  policies=GraphPolicy(max_concurrency=10 ** 9))
        assert validate_graph(g) != []
        assert optimize_graph(g).status == "REJECTED"


class TestRuntimeBoundary:
    def _calls(self):
        import ast
        import wisp.graph.optimizer_passes as OP
        tree = ast.parse(open(OP.__file__).read())
        calls = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                calls.add(n.func.attr)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                calls.add(n.func.id)
        return calls, open(OP.__file__).read()

    def test_no_provider_model_execution(self):
        calls, _ = self._calls()
        assert "generate" not in calls
        assert "generate_structured" not in calls

    def test_no_toolexecutor(self):
        _, src = self._calls()
        assert "ToolExecutor" not in src

    def test_no_authorization(self):
        calls, _ = self._calls()
        assert "authorize" not in calls

    def test_no_artifact_writes(self):
        calls, _ = self._calls()
        assert "put" not in calls

    def test_no_shell(self):
        _, src = self._calls()
        for token in ("subprocess", "os.system", "popen", "check_output"):
            assert token not in src

    def test_no_filesystem_mutation(self):
        import ast
        import wisp.graph.optimizer as O
        import wisp.graph.optimizer_passes as OP
        for mod in (O, OP):
            tree = ast.parse(open(mod.__file__).read())
            names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
            assert "open" not in names, mod.__name__


class TestDeterminism:
    def test_repeat_identical(self):
        g = fan_to_graph("f", [f"i{i}" for i in range(10)])
        r1 = optimize_graph(g)
        r2 = optimize_graph(g)
        assert r1.graph.fingerprint() == r2.graph.fingerprint()
        assert [d["code"] for d in r1.diagnostics] == \
               [d["code"] for d in r2.diagnostics]

    def test_noop_preserves_fingerprint(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),),
                  edges=(), policies=GraphPolicy(max_concurrency=1))
        r = optimize_graph(g)
        assert r.status == "UNCHANGED"
        assert r.graph.fingerprint() == g.fingerprint()

    def test_clamp_changes_fingerprint(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b"), _agent("c")),
                  edges=(_e("a", "b"), _e("a", "c")),
                  policies=GraphPolicy(max_concurrency=32))
        r = optimize_graph(g)
        assert r.graph.fingerprint() != g.fingerprint()
        # Proposal binding: re-derivation matches.
        assert optimize_graph(r.graph).graph.fingerprint() == \
            r.graph.fingerprint()


class TestAdversarial:
    def test_1000_node_fanout(self):
        import time
        g = fan_to_graph("f", [f"i{i}" for i in range(1000)])
        t0 = time.monotonic()
        r = optimize_graph(g)
        dt = time.monotonic() - t0
        assert dt < 10
        # Over the default 64-node policy cap: bounded rejection, not effort.
        assert r.status == "REJECTED"
        assert validate_graph(g) != []

    def test_512_node_fanout_within_caps(self):
        import time
        from wisp.graph.types import GraphPolicy as _GP
        g = fan_to_graph("f", [f"i{i}" for i in range(510)])
        g = Graph(id=g.id, version=g.version, entrypoint=g.entrypoint,
                  nodes=g.nodes, edges=g.edges,
                  policies=_GP(max_nodes=1024, max_concurrency=128))
        assert validate_graph(g) == []
        t0 = time.monotonic()
        r = optimize_graph(g)
        dt = time.monotonic() - t0
        assert dt < 10
        assert r.status in ("UNCHANGED", "OPTIMIZED")
        assert validate_graph(r.graph) == []

    def test_10000_edges_bounded(self):
        import time
        names = [f"n{i}" for i in range(200)]
        nodes = tuple(_agent(n) for n in names)
        edges = tuple(EdgeMapping(names[i % 200], names[(i * 7 + 1) % 200],
                                  reason="x")
                      for i in range(10000))
        from wisp.graph.types import GraphPolicy as _GP
        g = Graph(id="big", entrypoint=names[0], nodes=nodes, edges=edges,
                  policies=_GP(max_nodes=512, max_depth=256))
        t0 = time.monotonic()
        # Parser caps reject first (8192 edges); optimizer never sees it.
        assert validate_graph(g) != []
        assert optimize_graph(g).status == "REJECTED"
        assert time.monotonic() - t0 < 10

    def test_large_retries_bounded(self):
        c = NodeContract(id="branch-0", retry_policy=RetryPolicy(max_attempts=10))
        b = GraphNode(id="branch-0", type=NodeType.AGENT, contract=c)
        g = fan_to_graph("f", ["x"])
        # graft max retries onto a branch
        nodes = tuple(b if n.id == "branch-0" else n for n in g.nodes)
        from wisp.graph.types import GraphPolicy as _GP2
        g = Graph(id=g.id, version=g.version, entrypoint=g.entrypoint,
                  nodes=nodes, edges=g.edges, policies=_GP2(max_retries=10))
        assert validate_graph(g) == []
        r = optimize_graph(g)
        nb = next(n for n in r.graph.nodes if n.id == "branch-0")
        assert nb.effective_contract().retry_policy.max_attempts == 10

    def test_large_budgets_untouched(self):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b"),),
                  policies=GraphPolicy(max_concurrency=4, max_cost_usd=999999.0))
        r = optimize_graph(g)
        assert r.graph.policies.max_cost_usd == 999999.0

    def test_deep_router_structure(self):
        nodes = [_agent("entry")]
        edges = []
        prev = "entry"
        for i in range(10):
            r_ = GraphNode(id=f"r{i}", type=NodeType.ROUTER,
                           routes={"x": f"leaf{i}"}, default_route=f"leaf{i}",
                           contract=NodeContract(id=f"r{i}"))
            leaf = _agent(f"leaf{i}")
            nodes += [r_, leaf]
            edges += [_e(prev, f"r{i}"),
                      EdgeMapping(f"r{i}", f"leaf{i}", reason="x", condition="x")]
            prev = f"leaf{i}"
        from wisp.graph.types import GraphPolicy as _GP
        g = Graph(id="dr", entrypoint="entry", nodes=tuple(nodes),
                  edges=tuple(edges), policies=_GP(max_depth=64))
        assert validate_graph(g) == []
        before = {(e.from_node, e.to_node) for e in g.edges}
        r = optimize_graph(g)
        assert before <= {(e.from_node, e.to_node) for e in r.graph.edges}

    def test_cross_verifier_fanout(self):
        v1 = GraphNode(id="v1", type=NodeType.VERIFIER,
                       contract=NodeContract(id="v1"))
        v2 = GraphNode(id="v2", type=NodeType.VERIFIER,
                       contract=NodeContract(id="v2"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b1"), _agent("b2"), v1, v2,
                         _agent("s")),
                  edges=(_e("a", "b1"), _e("a", "b2"),
                         _e("b1", "v1"), _e("v1", "s", when="accept"),
                         _e("b2", "v2"), _e("v2", "s", when="accept")))
        assert validate_graph(g) == []
        r = optimize_graph(g)
        assert sum(1 for n in r.graph.nodes if n.type == NodeType.VERIFIER) == 2

    def test_fanout_with_approval_boundaries(self):
        ap = GraphNode(id="ap", type=NodeType.APPROVAL,
                       contract=NodeContract(id="ap"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b1"), _agent("b2"), ap),
                  edges=(_e("a", "b1"), _e("a", "b2"), _e("b1", "ap"),
                         _e("b2", "ap")))
        assert validate_graph(g) == []
        before = {(e.from_node, e.to_node) for e in g.edges}
        r = optimize_graph(g)
        assert before <= {(e.from_node, e.to_node) for e in r.graph.edges}
        assert any(n.type == NodeType.APPROVAL for n in r.graph.nodes)

    def test_fuzz_resource_metadata(self):
        import random
        for seed in range(40):
            rng = random.Random(41000 + seed)
            try:
                ctx = OptimizationContext(
                    max_fanout=rng.choice([1, 64, 1024]),
                    max_branches=rng.choice([1, 128, 4096]))
            except ValueError:
                continue
            names = [f"n{i}" for i in range(rng.randint(1, 6))]
            nodes = tuple(_agent(n) for n in names)
            edges = []
            for _ in range(rng.randint(0, 8)):
                a, b = rng.choice(names), rng.choice(names)
                if a != b:
                    edges.append(EdgeMapping(a, b, reason="x"))
            g = Graph(id="fz", entrypoint=names[0], nodes=nodes, edges=tuple(edges))
            if validate_graph(g):
                continue
            r = optimize_graph(g, context=ctx)
            assert r.status in ("UNCHANGED", "OPTIMIZED")
            if r.status == "OPTIMIZED":
                assert validate_graph(r.graph) == []
                ok, _ = envelope_le(resource_envelope(g, ctx.policy),
                                    resource_envelope(r.graph, ctx.policy))
                assert ok
                s0, s1 = structural_envelope(g), structural_envelope(r.graph)
                assert s1 <= s0  # nodes/edges/mappings never grow


class TestProperties:
    def test_resource_monotonicity(self):
        g = fan_to_graph("f", [f"i{i}" for i in range(8)])
        r = optimize_graph(g)
        ok, _ = envelope_le(resource_envelope(g), resource_envelope(r.graph))
        assert ok

    def test_structural_shrink(self):
        g = fan_to_graph("f", ["a", "b"])
        r = optimize_graph(g)
        assert structural_envelope(r.graph) <= structural_envelope(g)
