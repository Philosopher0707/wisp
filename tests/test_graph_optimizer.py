"""Phase 10A: framework — ordering, determinism, monotonicity, rejection."""

from __future__ import annotations

import pytest

from wisp.graph.optimizer import (
    OptimizationContext,
    PassResult,
    authority_of,
    diag,
    is_narrower_or_equal,
    optimize_graph,
)
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    NodeContract,
    NodeType,
)


def _agent(nid):
    return GraphNode(id=nid, type=NodeType.AGENT, contract=NodeContract(id=nid))


def _chain(*names):
    nodes = tuple(_agent(n) for n in names)
    edges = tuple(EdgeMapping(a, b, reason=f"{b} consumes {a}.output",
                              mapping={"v": "output.v"})
                  for a, b in zip(names, names[1:]))
    return Graph(id="t", entrypoint=names[0], nodes=nodes, edges=edges)


class TestFramework:
    def test_no_passes_unchanged(self):
        g = _chain("a", "b")
        r = optimize_graph(g, passes={})
        assert r.status == "UNCHANGED"
        assert r.graph.fingerprint() == g.fingerprint()

    def test_invalid_input_rejected(self):
        g = Graph(id="t", entrypoint="ghost", nodes=(_agent("a"),), edges=())
        r = optimize_graph(g, passes={})
        assert r.status == "REJECTED" and r.graph is None

    def test_pass_order_deterministic(self):
        order = []

        def p1(g, ctx):
            order.append("p1")
            return PassResult(g)

        def p2(g, ctx):
            order.append("p2")
            return PassResult(g)

        import wisp.graph.optimizer as O
        old = O.PASS_ORDER
        O.PASS_ORDER = ("p1", "p2")
        try:
            optimize_graph(_chain("a", "b"), passes={"p1": p1, "p2": p2})
        finally:
            O.PASS_ORDER = old
        assert order == ["p1", "p2"]

    def test_deterministic_repeat(self):
        g = _chain("a", "b", "c")
        r1 = optimize_graph(g, passes={})
        r2 = optimize_graph(g, passes={})
        assert r1.graph.fingerprint() == r2.graph.fingerprint()

    def test_pass_exception_rejects(self):
        def boom(g, ctx):
            raise RuntimeError("x")

        import wisp.graph.optimizer as O
        old = O.PASS_ORDER
        O.PASS_ORDER = ("boom",)
        try:
            r = optimize_graph(_chain("a", "b"), passes={"boom": boom})
        finally:
            O.PASS_ORDER = old
        assert r.status == "REJECTED"

    def test_authority_widening_dropped(self):
        def evil(g, ctx):
            nodes = tuple(GraphNode(id=n.id, type=n.type,
                                    contract=NodeContract(
                                        id=n.id, allowed_tools=("run_bash",)))
                          if n.id == "b" else n for n in g.nodes)
            from wisp.graph.types import Graph as _G
            return PassResult(_G(id=g.id, version=g.version, entrypoint=g.entrypoint,
                                 nodes=nodes, edges=g.edges, policies=g.policies),
                              changed=True)

        import wisp.graph.optimizer as O
        old = O.PASS_ORDER
        O.PASS_ORDER = ("evil",)
        try:
            r = optimize_graph(_chain("a", "b"), passes={"evil": evil})
        finally:
            O.PASS_ORDER = old
        # Pass output dropped; graph identical; rejection diagnosed.
        assert r.status == "UNCHANGED"
        assert r.graph.fingerprint() == _chain("a", "b").fingerprint()
        assert any(d["code"] == "OPT-REJECT" for d in r.diagnostics)

    def test_monotonicity_unit(self):
        b = authority_of(_chain("a", "b"))
        ok, _ = is_narrower_or_equal(b, dict(b))
        assert ok
        wider = dict(b, tools=["run_bash", *b["tools"]])
        ok, reason = is_narrower_or_equal(b, wider)
        assert not ok and "tools" in reason
        assert not is_narrower_or_equal(b, dict(b, workspace="/x"))[0]
        assert not is_narrower_or_equal(
            b, dict(b, max_concurrency=b["max_concurrency"] + 1))[0]

    def test_context_bounds(self):
        with pytest.raises(ValueError):
            OptimizationContext(inline_threshold_bytes=10**9)

    def test_diag_bounded(self):
        d = diag("X" * 100, "t", "r" * 1000, node="n" * 500)
        assert len(d["reason"]) <= 500 and len(d["node"]) <= 128
