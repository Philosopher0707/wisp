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
from wisp.graph.optimizer_passes import dependency_pass
from wisp.graph.types import (
    CycleSpec,
    EdgeMapping,
    Graph,
    GraphNode,
    JoinPolicy,
    NodeContract,
    NodeType,
)
from wisp.graph.validator import validate_graph


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


def _ctx():
    return OptimizationContext()


def _edges(*pairs, mapping=None, condition=""):
    return tuple(EdgeMapping(a, b, reason="x",
                             mapping=dict(mapping or {}), condition=condition)
                 for a, b in pairs)


def _graph(nodes, edges, entry="a"):
    return Graph(id="t", entrypoint=entry, nodes=tuple(nodes), edges=edges)


class TestDependencyPass:
    def test_genuine_dependency_preserved(self):
        g = _graph([_agent("a"), _agent("b")],
                   _edges(("a", "b"), mapping={"v": "output.v"}))
        r = dependency_pass(g, _ctx())
        assert not r.changed and len(r.graph.edges) == 1

    def test_fake_edge_removed(self):
        # b has another incoming edge, so a->b is droppable.
        g = _graph([_agent("a"), _agent("b"), _agent("c")],
                   _edges(("a", "b"), ("c", "b"), ("a", "c")))
        assert validate_graph(g) == []
        r = optimize_graph(g)
        assert r.status == "OPTIMIZED"
        assert all(not (e.from_node == "a" and e.to_node == "b")
                   for e in r.graph.edges)
        assert any(d["code"] == "OPT-001" and "removed" in d["action"]
                   for d in r.diagnostics)
        assert validate_graph(r.graph) == []

    def test_sole_edge_preserved_with_advisory(self):
        g = _graph([_agent("a"), _agent("b")], _edges(("a", "b")))
        r = dependency_pass(g, _ctx())
        assert not r.changed
        assert any("sole incoming" in d["reason"] for d in r.diagnostics)

    def test_conditional_edge_preserved(self):
        v = GraphNode(id="v", type=NodeType.VERIFIER, contract=NodeContract(id="v"))
        g = _graph([_agent("g"), v, _agent("p")],
                   _edges(("g", "v")) + (EdgeMapping("v", "p", reason="x",
                                                     condition="accept"),),
                   entry="g")
        r = dependency_pass(g, _ctx())
        assert not r.changed
        # verifier source edges are control: kept even unmapped.
        assert len(r.graph.edges) == 2

    def test_router_source_preserved(self):
        r_ = GraphNode(id="r", type=NodeType.ROUTER, routes={"x": "b"},
                       default_route="b", contract=NodeContract(id="r"))
        g = _graph([_agent("a"), r_, _agent("b")],
                   _edges(("a", "r")) + (EdgeMapping("r", "b", reason="x"),),
                   entry="a")
        r = dependency_pass(g, _ctx())
        assert not r.changed

    def test_join_target_preserved(self):
        j = GraphNode(id="j", type=NodeType.JOIN, join_policy=JoinPolicy.ALL,
                      contract=NodeContract(id="j"))
        g = _graph([_agent("a"), _agent("b"), j],
                   _edges(("a", "j"), ("b", "j")), entry="a")
        # 'b' unreachable by validator, but pass-level: join targets kept.
        r = dependency_pass(g, _ctx())
        assert not r.changed

    def test_cycle_edges_preserved(self):
        cyc = CycleSpec(entry="a", body=("b",), exit_gate="b", max_iterations=2)
        a = GraphNode(id="a", type=NodeType.AGENT, contract=NodeContract(id="a"),
                      cycle=cyc)
        b = GraphNode(id="b", type=NodeType.AGENT, contract=NodeContract(id="b"))
        g = _graph([a, b], _edges(("a", "b"), ("b", "a")))
        r = dependency_pass(g, _ctx())
        assert not r.changed

    def test_unsafe_endpoint_preserved(self):
        c = NodeContract(id="w", idempotent=False)
        w = GraphNode(id="w", type=NodeType.AGENT, contract=c)
        g = _graph([_agent("a"), w, _agent("c")],
                   _edges(("a", "w"), ("c", "w"), ("a", "c")))
        r = dependency_pass(g, _ctx())
        assert not r.changed
        assert any("side-effect" in d["reason"] for d in r.diagnostics)

    def test_entrypoint_incoming_kept_outside_cycles(self):
        # In a valid acyclic graph, any node with an edge into the entrypoint
        # is unreachable (path back to entry = cycle), so the validator
        # rejects such graphs before the pass runs. Inside declared cycles
        # the exemption never fires (cycle-member rule wins). Pin both:
        cyc = CycleSpec(entry="b", body=("a",), exit_gate="a", max_iterations=2)
        a = GraphNode(id="a", type=NodeType.AGENT, contract=NodeContract(id="a"))
        b = GraphNode(id="b", type=NodeType.AGENT, contract=NodeContract(id="b"),
                      cycle=cyc)
        g = Graph(id="t", entrypoint="b", nodes=(a, b, _agent("d")),
                  edges=(EdgeMapping("b", "a", reason="x", mapping={"v": "output.v"}),
                         EdgeMapping("a", "b", reason="x"),
                         EdgeMapping("a", "d", reason="x", mapping={"v": "output.v"})))
        assert validate_graph(g) == []
        r = dependency_pass(g, _ctx())
        assert not r.changed  # cycle-member rule, not the entrypoint rule

    def test_approval_target_preserved(self):
        ap = GraphNode(id="ap", type=NodeType.APPROVAL, contract=NodeContract(id="ap"))
        g = _graph([_agent("a"), ap, _agent("c"), _agent("b")],
                   _edges(("a", "ap"), ("c", "ap"), ("a", "c"), ("ap", "b")),
                   entry="a")
        assert validate_graph(g) == []
        r = dependency_pass(g, _ctx())
        assert not r.changed
        assert any("completion signal" in d["reason"] for d in r.diagnostics)

    def test_removal_never_strands_target(self):
        # a->b, c->b, a->c: at most one incoming edge to b may drop.
        g = _graph([_agent("a"), _agent("b"), _agent("c")],
                   _edges(("a", "b"), ("c", "b"), ("a", "c")))
        r = optimize_graph(g)
        b_preds = [e for e in r.graph.edges if e.to_node == "b"]
        assert len(b_preds) >= 1
        assert validate_graph(r.graph) == []

    def test_property_removed_edges_were_eligible(self):
        import random
        for seed in range(40):
            rng = random.Random(5000 + seed)
            names = [f"n{i}" for i in range(rng.randint(2, 6))]
            nodes = tuple(_agent(n) for n in names)
            edges = []
            for _ in range(rng.randint(1, 8)):
                a, b = rng.choice(names), rng.choice(names)
                if a == b:
                    continue
                if rng.random() < 0.5:
                    edges.append(EdgeMapping(a, b, reason="x",
                                             mapping={"v": "output.v"}))
                else:
                    edges.append(EdgeMapping(a, b, reason="x"))
            g = Graph(id="p", entrypoint=names[0], nodes=nodes, edges=tuple(edges))
            if validate_graph(g):
                continue
            r = optimize_graph(g)
            assert r.status in ("UNCHANGED", "OPTIMIZED")
            if r.status == "OPTIMIZED":
                assert validate_graph(r.graph) == []
                info = {(e.from_node, e.to_node): (bool(e.mapping), e.condition)
                        for e in g.edges}
                removed = set(info) - {(e.from_node, e.to_node) for e in r.graph.edges}
                for pair in removed:
                    # Every removed edge was unmapped + unconditional.
                    assert info[pair] == (False, ""), pair
                # Authority never widens (fuzz-level monotonicity).
                from wisp.graph.optimizer import authority_of, is_narrower_or_equal
                ok, _ = is_narrower_or_equal(authority_of(g), authority_of(r.graph))
                assert ok
