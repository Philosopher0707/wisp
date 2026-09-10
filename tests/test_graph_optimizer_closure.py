"""Phase 10F closure: global invariants, cross-pass matrix, corpora.

No new passes. This suite proves composition safety: every accepted result
is valid, authority/resource-monotone, approval- and verifier-preserving,
deterministic, idempotent, and fingerprint-bound.
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
    ModelPolicy,
    NodeContract,
    NodeType,
)
from wisp.graph.validator import validate_graph

PROFILE = {"tools": ["read_file"], "model": "m1", "provider": "p1"}


def _agent(nid, tools=("read_file",)):
    return GraphNode(id=nid, type=NodeType.AGENT,
                     contract=NodeContract(
                         id=nid, allowed_tools=tools,
                         model_policy=ModelPolicy(model="m1", provider="p1")))


def _e(f, t, reason="x", mapping=None, when=""):
    return EdgeMapping(f, t, reason=reason, mapping=dict(mapping or {}),
                       condition=when)


def _ctx(profile=PROFILE, **kw):
    return OptimizationContext(
        verifier_profile=dict(profile) if profile else None, **kw)


def assert_optimizer_invariants(original: Graph, result, context=None):
    """Scorecard helper (§28): hard failure on any critical invariant."""
    assert result.status in ("UNCHANGED", "OPTIMIZED"), result.error
    final = result.graph
    assert validate_graph(final) == []  # INV-1
    ok, reason = is_narrower_or_equal(authority_of(original), authority_of(final))
    assert ok, f"INV-2 authority: {reason}"  # INV-2
    ok, reason = envelope_le(resource_envelope(original, context.policy if context else None),
                             resource_envelope(final, context.policy if context else None))
    assert ok, f"INV-4 resource: {reason}"  # INV-4
    before_ap = sorted(n.id for n in original.nodes if n.type == NodeType.APPROVAL)
    after_ap = sorted(n.id for n in final.nodes if n.type == NodeType.APPROVAL)
    assert before_ap == after_ap, "INV-5 approval"  # INV-5
    before_v = {n.id for n in original.nodes if n.type == NodeType.VERIFIER}
    after_v = {n.id for n in final.nodes if n.type == NodeType.VERIFIER}
    assert before_v <= after_v, "INV-6 verifiers removed"  # INV-6
    s0, s1 = structural_envelope(original), structural_envelope(final)
    assert s1[2] <= s0[2], "mappings never grow"  # INV-3 (mappings)
    added = {n.id for n in final.nodes} - {n.id for n in original.nodes}
    for nid in added:  # INV-3: node growth only via verifier insertion
        assert next(n for n in final.nodes if n.id == nid).type == NodeType.VERIFIER
    return final


class TestGlobalInvariants:
    def test_scorecard_on_representative_graph(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b"), _agent("c"), _agent("s")),
                  edges=(_e("a", "b"), _e("c", "b"),
                         _e("a", "c", mapping={"v": "output.v"}),
                         _e("b", "s"), _e("c", "s")),
                  policies=GraphPolicy(max_concurrency=32))
        assert validate_graph(g) == []
        r = optimize_graph(g, context=_ctx())
        assert_optimizer_invariants(g, r, _ctx())

    def test_pass_order_stability(self):
        import wisp.graph.optimizer as O
        assert O.PASS_ORDER == ("dependency", "artifact_transport",
                                "verification", "resource")
        assert O.MAX_PASSES == 1


class TestCrossPassMatrix:
    def _multi(self):
        return Graph(id="t", entrypoint="a",
                     nodes=(_agent("a"), _agent("b"), _agent("c"), _agent("s")),
                     edges=(_e("a", "b"), _e("c", "b"),
                            _e("a", "c", mapping={"v": "output.v"}),
                            _e("b", "s")),
                     policies=GraphPolicy(max_concurrency=32))

    def test_full_pipeline(self):
        g = self._multi()
        r = optimize_graph(g, context=_ctx())
        final = assert_optimizer_invariants(g, r, _ctx())
        # dependency fired (a->b dropped), verification inserted, resource clamped.
        assert r.passes_applied == ["dependency", "verification", "resource"]
        assert "b__verify" in {n.id for n in final.nodes}
        assert final.policies.max_concurrency == 5

    def test_subset_dependency_verification(self):
        import wisp.graph.optimizer as O
        import wisp.graph.optimizer_passes as OP
        old = O.PASS_ORDER
        O.PASS_ORDER = ("dependency", "verification")
        try:
            r = optimize_graph(self._multi(), context=_ctx(),
                               passes={"dependency": OP.dependency_pass,
                                       "verification": OP.verification_pass})
        finally:
            O.PASS_ORDER = old
        assert_optimizer_invariants(self._multi(), r, _ctx())

    def test_subset_verification_resource(self):
        import wisp.graph.optimizer as O
        import wisp.graph.optimizer_passes as OP
        old = O.PASS_ORDER
        O.PASS_ORDER = ("verification", "resource")
        try:
            g = Graph(id="t", entrypoint="a",
                      nodes=(_agent("a"), _agent("s")),
                      edges=(_e("a", "s"),),
                      policies=GraphPolicy(max_concurrency=16))
            r = optimize_graph(g, context=_ctx(),
                               passes={"verification": OP.verification_pass,
                                       "resource": OP.resource_pass})
        finally:
            O.PASS_ORDER = old
        final = assert_optimizer_invariants(g, r, _ctx())
        assert "a__verify" in {n.id for n in final.nodes}
        assert final.policies.max_concurrency == 3

    def test_transport_metadata_survives_later_passes(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")),
                  edges=(_e("a", "s", mapping={"blob": "output.blob"}),))
        ctx = OptimizationContext(size_hints={("a", "s", "blob"): 200_000})
        r = optimize_graph(g, context=ctx)
        assert "artifact_transport" in r.meta
        t = r.meta["artifact_transport"]["transports"].get("a->s:blob", {})
        assert t.get("transport") == "ARTIFACT"


class TestNoOpCorpus:
    def test_minimal_chain(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b", mapping={"v": "output.v"}),),
                  policies=GraphPolicy(max_concurrency=2))
        r = optimize_graph(g, context=_ctx())
        assert r.status == "UNCHANGED"
        assert r.graph.fingerprint() == g.fingerprint()

    def test_fully_verified(self):
        v = GraphNode(id="v", type=NodeType.VERIFIER,
                      contract=NodeContract(id="v"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), v, _agent("s")),
                  edges=(_e("a", "v"), _e("v", "s", when="accept")),
                  policies=GraphPolicy(max_concurrency=3))
        r = optimize_graph(g, context=_ctx())
        assert r.status == "UNCHANGED"
        assert r.graph.fingerprint() == g.fingerprint()

    def test_approval_protected(self):
        ap = GraphNode(id="ap", type=NodeType.APPROVAL,
                       contract=NodeContract(id="ap"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), ap, _agent("s")),
                  edges=(_e("a", "ap"), _e("ap", "s")),
                  policies=GraphPolicy(max_concurrency=3))
        r = optimize_graph(g, context=_ctx())
        assert r.status == "UNCHANGED"

    def test_unknown_sizes_noop(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")),
                  edges=(_e("a", "s", mapping={"v": "output.v"}),),
                  policies=GraphPolicy(max_concurrency=2))
        r = optimize_graph(g, context=OptimizationContext())
        # No profile -> verification advisory; transport unknown -> inline.
        # Dependency keeps sole edge. Only silence or advisories; graph same.
        assert r.graph.fingerprint() == g.fingerprint()


class TestTransformationCorpus:
    def test_10b_fires(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b"), _agent("c")),
                  edges=(_e("a", "b"), _e("c", "b"),
                         _e("a", "c", mapping={"v": "output.v"})),
                  policies=GraphPolicy(max_concurrency=3))
        r = optimize_graph(g, context=_ctx())
        assert "dependency" in r.passes_applied
        assert ("a", "b") not in {(e.from_node, e.to_node) for e in r.graph.edges}

    def test_10c_annotates(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")),
                  edges=(_e("a", "s", mapping={"blob": "output.blob"}),),
                  policies=GraphPolicy(max_concurrency=2))
        ctx = OptimizationContext(size_hints={("a", "s", "blob"): 200_000})
        r = optimize_graph(g, context=ctx)
        assert r.meta["artifact_transport"]["transports"]["a->s:blob"][
            "transport"] == "ARTIFACT"

    def test_10d_inserts(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")),
                  edges=(_e("a", "s"),),
                  policies=GraphPolicy(max_concurrency=2))
        r = optimize_graph(g, context=_ctx())
        assert "a__verify" in {n.id for n in r.graph.nodes}

    def test_10e_clamps(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b", mapping={"v": "output.v"}),),
                  policies=GraphPolicy(max_concurrency=32))
        r = optimize_graph(g, context=_ctx())
        assert "resource" in r.passes_applied
        assert r.graph.policies.max_concurrency == 2


class TestIdempotence:
    def test_fixed_point(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b"), _agent("c"), _agent("s")),
                  edges=(_e("a", "b"), _e("c", "b"),
                         _e("a", "c", mapping={"v": "output.v"}),
                         _e("b", "s")),
                  policies=GraphPolicy(max_concurrency=32))
        ctx = _ctx()
        r1 = optimize_graph(g, context=ctx)
        r2 = optimize_graph(r1.graph, context=ctx)
        r3 = optimize_graph(r2.graph, context=ctx)
        assert r1.graph.fingerprint() == r2.graph.fingerprint()
        assert r2.status == "UNCHANGED" and r3.status == "UNCHANGED"
        d2 = [(d["code"], d["node"], d["reason"]) for d in r2.diagnostics]
        d3 = [(d["code"], d["node"], d["reason"]) for d in r3.diagnostics]
        assert d2 == d3

    def test_idempotence_fuzz(self):
        import random
        for seed in range(30):
            rng = random.Random(60000 + seed)
            names = [f"n{i}" for i in range(rng.randint(1, 6))]
            nodes = tuple(_agent(n) for n in names)
            edges = []
            for _ in range(rng.randint(0, 8)):
                a, b = rng.choice(names), rng.choice(names)
                if a == b:
                    continue
                edges.append(EdgeMapping(
                    a, b, reason="x",
                    mapping={"v": "output.v"} if rng.random() < 0.5 else {}))
            g = Graph(id="f", entrypoint=names[0], nodes=nodes, edges=tuple(edges))
            if validate_graph(g):
                continue
            ctx = _ctx() if rng.random() < 0.5 else OptimizationContext()
            r1 = optimize_graph(g, context=ctx)
            assert r1.status in ("UNCHANGED", "OPTIMIZED")
            if r1.status == "OPTIMIZED":
                r2 = optimize_graph(r1.graph, context=ctx)
                assert r2.graph.fingerprint() == r1.graph.fingerprint(), seed
                assert r2.status == "UNCHANGED", seed


class TestFingerprintAndProposal:
    def test_approved_equals_executed(self):
        from wisp.graph.planner import compile_optimized
        ir = {"objective": "x", "execution_shape": "GRAPH",
              "graph": {"id": "t", "entry": "a",
                        "nodes": {"a": {"type": "agent", "allowed_tools": ["read_file"]},
                                  "s": {"type": "agent", "allowed_tools": ["read_file"]}},
                        "edges": [{"from": "a", "to": "s", "reason": "x"}]}}
        from wisp.graph.types import GraphPolicy as _GP
        g, _, _, _ = compile_optimized(ir, _GP())
        # Re-derivation is stable: execution reproduces the approved hash.
        g2, _, _, _ = compile_optimized(ir, _GP())
        assert g.fingerprint() == g2.fingerprint()

    def test_stale_proposal_rejected(self):
        import asyncio
        from wisp.graph.executor import GraphExecutor
        from wisp.graph.planner import compile_proposal, execute_proposal
        from wisp.graph.store import GraphStore
        from wisp.graph.types import GraphPolicy as _GP, NodeResult, NodeStatus
        ir = {"objective": "x", "execution_shape": "SINGLE_AGENT",
              "graph": {"id": "t", "entry": "a",
                        "nodes": {"a": {"type": "agent"}},
                        "edges": []}}
        p = compile_proposal(ir, "x", _GP())
        assert p["status"] == "APPROVAL_REQUIRED"

        async def run(node, inputs):
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})

        async def no_sleep(s):
            pass

        import tempfile
        ws = tempfile.mkdtemp()
        ex = GraphExecutor(runner=run, workspace=ws,
                           store=GraphStore(workspace=ws), sleep=no_sleep)
        r = asyncio.run(execute_proposal(p, ex, {}, approve=True))
        assert r["status"] == "succeeded"
        # Tamper the stored IR: execution must refuse.
        p["ir"]["graph"]["nodes"]["a"]["timeout"] = 60
        import pytest as _pt
        from wisp.graph.planner import PlanError
        with _pt.raises(PlanError, match="STALE_PROPOSAL"):
            asyncio.run(execute_proposal(p, ex, {}, approve=True))


class TestDeterminism:
    def test_repeat_stable(self):
        g = fan_to_graph("f", [f"i{i}" for i in range(8)])
        ctx = _ctx()
        r1 = optimize_graph(g, context=ctx)
        r2 = optimize_graph(g, context=ctx)
        assert r1.graph.fingerprint() == r2.graph.fingerprint()
        assert [(d["code"], d["node"], d["reason"]) for d in r1.diagnostics] == \
               [(d["code"], d["node"], d["reason"]) for d in r2.diagnostics]

    def test_error_paths_bounded(self):
        g = Graph(id="t", entrypoint="ghost", nodes=(_agent("a"),), edges=())
        r = optimize_graph(g, context=_ctx())
        assert r.status == "REJECTED" and r.graph is None


class TestPerformance:
    @pytest.mark.parametrize("n", [32, 128])
    def test_pipeline_baselines(self, n):
        import time
        from wisp.graph.types import GraphPolicy as _GP
        g = fan_to_graph("f", [f"i{i}" for i in range(n)])
        g = Graph(id=g.id, version=g.version, entrypoint=g.entrypoint,
                  nodes=g.nodes, edges=g.edges,
                  policies=_GP(max_nodes=1024, max_concurrency=128))
        t0 = time.monotonic()
        r = optimize_graph(g, context=_ctx())
        dt = time.monotonic() - t0
        assert dt < 30  # generous anti-hang bound, not a benchmark
        assert validate_graph(r.graph) == []
