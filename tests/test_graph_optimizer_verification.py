"""Phase 10D: OPT-002 verification pass — detection, insertion, preservation."""

from __future__ import annotations

import pytest

from wisp.graph.optimizer import OptimizationContext, optimize_graph
from wisp.graph.optimizer_passes import verification_pass
from wisp.graph.types import (
    CycleSpec,
    EdgeMapping,
    Graph,
    GraphNode,
    JoinPolicy,
    ModelPolicy,
    NodeContract,
    NodeType,
)
from wisp.graph.validator import validate_graph

PROFILE = {"tools": ["read_file"], "model": "m1", "provider": "p1"}


def _agent(nid, tools=("read_file",), model="m1", provider="p1"):
    return GraphNode(id=nid, type=NodeType.AGENT,
                     contract=NodeContract(
                         id=nid, allowed_tools=tools,
                         model_policy=ModelPolicy(model=model, provider=provider)))


def _ver(nid="v"):
    return GraphNode(id=nid, type=NodeType.VERIFIER,
                     contract=NodeContract(id=nid, allowed_tools=("read_file",),
                                           model_policy=ModelPolicy(model="m1",
                                                                    provider="p1")))


def _ctx(profile=PROFILE):
    return OptimizationContext(verifier_profile=dict(profile) if profile else None)


def _e(f, t, reason="x", mapping=None, when=""):
    return EdgeMapping(f, t, reason=reason, mapping=dict(mapping or {}),
                       condition=when)


def _diag_codes(r):
    return [d["code"] for d in r.diagnostics]


class TestDetection:
    def test_missing_obligation_advisory(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("b")),
                  edges=(_e("a", "b"),))
        r = verification_pass(g, OptimizationContext())
        assert not r.changed
        assert any("obligation (a,b)" in d["reason"] for d in r.diagnostics)

    def test_satisfied_by_gating_verifier(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _ver(), _agent("s")),
                  edges=(_e("a", "v"), _e("v", "s", when="accept")))
        r = verification_pass(g, _ctx())
        assert not r.changed
        assert any("satisfied by v" in d["reason"] for d in r.diagnostics)

    def test_verifier_on_unrelated_path_insufficient(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _ver("v"), _agent("s"), _agent("z")),
                  edges=(_e("a", "s"), _e("z", "v"), _e("v", "s", when="accept")))
        # v is fed by z, not a: cannot vouch for a -> (a,s) obligation stands,
        # while (z,s) is legitimately satisfied.
        r = verification_pass(g, _ctx())
        assert any(d.get("detail", {}).get("producer") == "a"
                   and "satisfied" not in d["reason"] for d in r.diagnostics)
        assert any("satisfied by v" in d["reason"] for d in r.diagnostics)

    def test_upstream_verifier_without_gate_insufficient(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _ver(), _agent("s")),
                  edges=(_e("a", "v"), _e("a", "s"), _e("v", "s")))
        # v sees a's output but s reachable without v's accept -> unsatisfied.
        r = verification_pass(g, _ctx())
        assert not any("satisfied by" in d["reason"] for d in r.diagnostics)

    def test_downstream_verifier_no_protection(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s"), _ver()),
                  edges=(_e("a", "s"), _e("s", "v")))
        r = verification_pass(g, _ctx())
        assert not any("satisfied by" in d["reason"] for d in r.diagnostics)

    def test_approval_on_path_satisfies(self):
        ap = GraphNode(id="ap", type=NodeType.APPROVAL,
                       contract=NodeContract(id="ap"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), ap, _agent("s")),
                  edges=(_e("a", "ap"), _e("ap", "s")))
        r = verification_pass(g, _ctx())
        assert any("satisfied by __approval__" in d["reason"] for d in r.diagnostics)


class TestInsertion:
    def _pair(self):
        return Graph(id="t", entrypoint="a",
                     nodes=(_agent("a"), _agent("s")),
                     edges=(_e("a", "s"),))

    def test_safe_insertion(self):
        r = verification_pass(self._pair(), _ctx())
        assert r.changed
        ids = {n.id for n in r.graph.nodes}
        assert "a__verify" in ids
        v = next(n for n in r.graph.nodes if n.id == "a__verify")
        assert v.type == NodeType.VERIFIER
        assert v.contract.allowed_tools == ("read_file",)
        assert v.contract.model_policy.model == "m1"
        assert validate_graph(r.graph) == []

    def test_inserted_mapping_and_control(self):
        r = verification_pass(self._pair(), _ctx())
        by_pair = {(e.from_node, e.to_node): e for e in r.graph.edges}
        assert ("a", "s") not in by_pair  # choke replaced
        assert by_pair[("a", "a__verify")].mapping == {}
        accept = by_pair[("a__verify", "s")]
        assert accept.condition == "accept" and accept.mapping == {}

    def test_deterministic_id_and_rerun_stable(self):
        r1 = verification_pass(self._pair(), _ctx())
        r2 = verification_pass(r1.graph, _ctx())
        assert not r2.changed  # satisfied now; never duplicates
        assert r1.graph.fingerprint() == verification_pass(
            self._pair(), _ctx()).graph.fingerprint()

    def test_full_pipeline_revalidates(self):
        r = optimize_graph(self._pair(), context=_ctx())
        assert r.status == "OPTIMIZED"
        assert validate_graph(r.graph) == []

    def test_missing_profile_advisory(self):
        for bad in (None, {}, {"tools": ["read_file"]},
                    {"tools": ["read_file"], "model": "m1"},
                    {"model": "m1", "provider": "p1"}):
            r = verification_pass(self._pair(), OptimizationContext(
                verifier_profile=bad))
            assert not r.changed
            assert any("profile" in d["reason"] for d in r.diagnostics)

    def test_mapped_edge_advisory(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")),
                  edges=(_e("a", "s", mapping={"v": "output.v"}),))
        r = verification_pass(g, _ctx())
        assert not r.changed
        assert any("declared dataflow" in d["reason"] for d in r.diagnostics)

    def test_multi_pred_sink_advisory(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("c"), _agent("s")),
                  edges=(_e("a", "s"), _e("c", "s"), _e("a", "c",
                                                        mapping={"v": "output.v"})))
        r = verification_pass(g, _ctx())
        assert not any(n.id == "a__verify" for n in r.graph.nodes)
        assert any("predecessors" in d["reason"] for d in r.diagnostics)

    def test_cycle_risk_advisory(self):
        cyc = CycleSpec(entry="a", body=("s",), exit_gate="s", max_iterations=2)
        a = GraphNode(id="a", type=NodeType.AGENT,
                      contract=NodeContract(id="a", allowed_tools=("read_file",),
                                            model_policy=ModelPolicy(model="m1",
                                                                     provider="p1")),
                      cycle=cyc)
        s = GraphNode(id="s", type=NodeType.AGENT,
                      contract=NodeContract(id="s", allowed_tools=("read_file",),
                                            model_policy=ModelPolicy(model="m1",
                                                                     provider="p1")))
        g = Graph(id="t", entrypoint="a", nodes=(a, s),
                  edges=(_e("a", "s"), _e("s", "a")))
        # s reaches a -> insertion would close a loop (also undeclared-cycle
        # edges are exempt from the dependency pass, so it survives there).
        from wisp.graph.optimizer_passes import _try_insert
        new_graph, note = _try_insert(g, _ctx(), "a", s, "")
        assert new_graph is None and "cycle" in note

    def test_entrypoint_sink_advisory(self):
        g = Graph(id="t", entrypoint="s",
                  nodes=(_agent("a"), _agent("s")),
                  edges=(_e("a", "s"),))
        r = verification_pass(g, _ctx())
        assert not r.changed
        assert any("entrypoint" in d["reason"] for d in r.diagnostics)

    def test_join_sink_no_insertion(self):
        j = GraphNode(id="j", type=NodeType.JOIN, join_policy=JoinPolicy.ALL,
                      contract=NodeContract(id="j"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), j),
                  edges=(_e("a", "j"),))
        r = verification_pass(g, _ctx())
        assert not any(n.type == NodeType.VERIFIER for n in r.graph.nodes)

    def test_id_collision_advisory(self):
        v = GraphNode(id="a__verify", type=NodeType.AGENT,
                      contract=NodeContract(id="a__verify"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s"), v),
                  edges=(_e("a", "s"), _e("a", "a__verify")))
        r = verification_pass(g, _ctx())
        vs = [n for n in r.graph.nodes if n.type == NodeType.VERIFIER]
        assert not vs
        assert any("collision" in d["reason"] for d in r.diagnostics)


class TestPreservation:
    def test_verifiers_never_removed(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _ver("v1"), _ver("v2"), _agent("s")),
                  edges=(_e("a", "v1"), _e("v1", "s", when="accept"),
                         _e("a", "v2"), _e("v2", "s", when="accept")))
        before = sorted(n.id for n in g.nodes if n.type == NodeType.VERIFIER)
        r = optimize_graph(g, context=_ctx())
        after = sorted(n.id for n in r.graph.nodes if n.type == NodeType.VERIFIER)
        assert before == after

    def test_approvals_never_removed(self):
        ap = GraphNode(id="ap", type=NodeType.APPROVAL,
                       contract=NodeContract(id="ap"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), ap, _agent("s")),
                  edges=(_e("a", "ap"), _e("ap", "s")))
        r = optimize_graph(g, context=_ctx())
        assert any(n.type == NodeType.APPROVAL for n in r.graph.nodes)

    def test_router_join_untouched(self):
        r_ = GraphNode(id="r", type=NodeType.ROUTER, routes={"x": "s"},
                       default_route="s", contract=NodeContract(id="r"))
        j = GraphNode(id="j", type=NodeType.JOIN, join_policy=JoinPolicy.ALL,
                      contract=NodeContract(id="j"))
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), r_, j, _agent("s")),
                  edges=(_e("a", "r"), EdgeMapping("r", "s", reason="x", condition="x"),
                         _e("a", "j"), _e("j", "s")))
        before = {(e.from_node, e.to_node, e.condition) for e in g.edges}
        r = optimize_graph(g, context=_ctx())
        after = {(e.from_node, e.to_node, e.condition) for e in r.graph.edges}
        assert before <= after  # nothing control-related removed
        assert validate_graph(r.graph) == []


class TestSecurity:
    @pytest.mark.parametrize("key,value", [
        ("tools", ["run_bash"]), ("model", "evil-9"), ("provider", "evil-p")])
    def test_expansion_rejected(self, key, value):
        prof = dict(PROFILE)
        prof[key] = value
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")), edges=(_e("a", "s"),))
        r = verification_pass(g, _ctx(prof))
        assert not any(n.id == "a__verify" for n in r.graph.nodes)
        assert any("exceed" in d["reason"] or "authority" in d["reason"]
                   for d in r.diagnostics)

    def test_policy_caps_profile(self):
        from wisp.graph.types import GraphPolicy
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a", tools=("read_file", "run_bash")), _agent("s")),
                  edges=(_e("a", "s"),))
        prof = {"tools": ["run_bash"], "model": "m1", "provider": "p1"}
        ctx = OptimizationContext(
            verifier_profile=prof,
            policy=GraphPolicy(allowed_tools=("read_file",)))
        r = verification_pass(g, ctx)
        assert not any(n.id == "a__verify" for n in r.graph.nodes)

    def test_monotonicity_holds_with_insertion(self):
        from wisp.graph.optimizer import authority_of, is_narrower_or_equal
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")), edges=(_e("a", "s"),))
        r = optimize_graph(g, context=_ctx())
        ok, _ = is_narrower_or_equal(authority_of(g), authority_of(r.graph))
        assert ok


class TestRuntimeBoundary:
    def test_no_runtime_calls_in_pass(self):
        import ast
        import wisp.graph.optimizer_passes as OP
        tree = ast.parse(open(OP.__file__).read())
        calls = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                calls.add(n.func.attr)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                calls.add(n.func.id)
        for banned in ("put", "generate", "generate_structured", "run",
                       "execute", "authorize", "system", "popen"):
            assert banned not in calls, banned
        src = open(OP.__file__).read()
        assert "ToolExecutor" not in src

    def test_no_provider_or_model_invocation(self):
        import wisp.graph.optimizer_passes as OP
        src = open(OP.__file__).read()
        assert "provider" in src.lower()  # profile *names* only...
        # ...but no invocation machinery:
        assert "generate_stream_events" not in src
        assert "SubagentOrchestrator" not in src


class TestFingerprint:
    def test_noop_preserves_fingerprint(self):
        from wisp.graph.types import GraphPolicy
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _ver(), _agent("s")),
                  edges=(_e("a", "v"), _e("v", "s", when="accept")),
                  policies=GraphPolicy(max_concurrency=3))
        r = optimize_graph(g, context=_ctx())
        assert r.status == "UNCHANGED"
        assert r.graph.fingerprint() == g.fingerprint()

    def test_insertion_changes_fingerprint(self):
        g = Graph(id="t", entrypoint="a",
                  nodes=(_agent("a"), _agent("s")), edges=(_e("a", "s"),))
        r = optimize_graph(g, context=_ctx())
        assert r.status == "OPTIMIZED"
        assert r.graph.fingerprint() != g.fingerprint()

    def test_proposal_binds_optimized_hash(self):
        from wisp.graph.planner import compile_proposal
        ir = {"objective": "x", "execution_shape": "GRAPH",
              "graph": {"id": "t", "entry": "a",
                        "nodes": {"a": {"type": "agent", "allowed_tools": ["read_file"]},
                                  "s": {"type": "agent", "allowed_tools": ["read_file"]}},
                        "edges": [{"from": "a", "to": "s", "reason": "x"}]}}
        p = compile_proposal(ir, "x", None)
        assert p["status"] == "APPROVAL_REQUIRED"
        # Optimized (dependency pass keeps sole edge; verification advisory
        # without profile) -> hash matches re-derivation.
        from wisp.graph.planner import compile_optimized
        g, _, _, _ = compile_optimized(ir, None)
        assert g.fingerprint() == p["graph_hash"]
