"""Phase 9A–9C: IR strictness, deterministic compiler, policy boundary."""

from __future__ import annotations

import pytest

from wisp.graph.planner import (
    PlanError,
    compile_ir,
    compile_proposal,
    ir_hash,
    quality_score,
)
from wisp.graph.types import GraphPolicy
from wisp.graph.validator import validate_graph


def _ir(nodes=None, edges=None, policies=None, shape="GRAPH", entry="a"):
    return {"objective": "test", "execution_shape": shape,
            "graph": {"id": "t", "entry": entry,
                      "nodes": nodes or {"a": {"type": "agent"}},
                      "edges": edges or [],
                      "policies": policies or {}}}


class TestIR:
    def test_valid_single_node(self):
        g, notes = compile_ir(_ir())
        assert g.id == "t" and validate_graph(g) == []

    def test_unknown_shape_rejected(self):
        with pytest.raises(PlanError, match="execution_shape"):
            compile_ir(_ir(shape="SWARM"))

    def test_single_agent_shape_enforces_one_node(self):
        ir = _ir(nodes={"a": {"type": "agent"}, "b": {"type": "agent"}},
                 edges=[{"from": "a", "to": "b", "reason": "x"}],
                 shape="SINGLE_AGENT")
        with pytest.raises(PlanError, match="exactly one node"):
            compile_ir(ir)

    def test_duplicate_ids_rejected(self):
        # dicts can't hold dupes; compiler-level dupes surface via validator
        ir = _ir(nodes={"a": {"type": "agent"}},
                 edges=[{"from": "a", "to": "ghost", "reason": "x"}])
        with pytest.raises(PlanError):
            compile_ir(ir)

    def test_oversized_ir_rejected(self):
        ir = _ir(nodes={f"n{i}": {"type": "agent"} for i in range(2000)})
        with pytest.raises(PlanError):
            compile_ir(ir)

    def test_ir_hash_deterministic(self):
        assert ir_hash(_ir()) == ir_hash(_ir())

    def test_host_controlled_fields_rejected(self):
        for field in ("function", "workspace", "approved", "command"):
            ir = _ir(nodes={"a": {"type": "agent", field: "x"}})
            with pytest.raises(PlanError, match="host-controlled"):
                compile_ir(ir, GraphPolicy())


class TestCompiler:
    def test_deterministic_lowering(self):
        ir = _ir(nodes={"a": {"type": "agent"}, "b": {"type": "agent"}},
                 edges=[{"from": "a", "to": "b", "reason": "b consumes a.output",
                         "mapping": {"v": "output.v"}}])
        g1, _ = compile_ir(ir)
        g2, _ = compile_ir(ir)
        assert g1.fingerprint() == g2.fingerprint()

    def test_fan_router_join_cycle_lower(self):
        ir = {"execution_shape": "GRAPH",
              "graph": {"id": "f", "entry": "s",
                        "nodes": {"s": {"type": "function", "function": "split"},
                                  "b": {"type": "agent"}, "j": {"type": "join"},
                                  "r": {"type": "router", "routes": {"x": "b"},
                                        "default_route": "b"}},
                        "edges": [{"from": "s", "to": "b", "reason": "x"},
                                  {"from": "b", "to": "j", "reason": "x"},
                                  {"from": "s", "to": "r", "reason": "x"}]}}
        # 's' function field is host-controlled -> rejected (planner can't bind)
        with pytest.raises(PlanError, match="host-controlled"):
            compile_ir(ir, GraphPolicy())

    def test_quality_flags_fake_deps(self):
        g, _ = compile_ir(_ir(nodes={"a": {"type": "agent"}, "b": {"type": "agent"},
                                            "c": {"type": "agent"}},
                               edges=[{"from": "a", "to": "b", "reason": "x"},
                                      {"from": "b", "to": "c", "reason": "x"},
                                      {"from": "a", "to": "c", "reason": "x"}]))
        q = quality_score(g)
        assert any("OPT-001" in f for f in q["findings"])

    def test_quality_flags_missing_verifier(self):
        g, _ = compile_ir(_ir(
            nodes={f"n{i}": {"type": "agent"} for i in range(3)},
            edges=[{"from": "n0", "to": "n1", "reason": "x", "mapping": {"v": "output.v"}},
                   {"from": "n1", "to": "n2", "reason": "x", "mapping": {"v": "output.v"}}],
            entry="n0"))
        assert any("OPT-002" in f for f in quality_score(g)["findings"])


class TestPolicyBoundary:
    def test_forbidden_tool_rejected(self):
        ir = _ir(nodes={"a": {"type": "agent", "allowed_tools": ["run_bash"]}})
        with pytest.raises(PlanError, match="POLICY_REJECTED"):
            compile_ir(ir, GraphPolicy(allowed_tools=("read_file",)))

    def test_allowed_tool_survives(self):
        ir = _ir(nodes={"a": {"type": "agent", "allowed_tools": ["read_file"]}})
        g, _ = compile_ir(ir, GraphPolicy(allowed_tools=("read_file",)))
        assert validate_graph(g) == []

    def test_all_tools_forbidden_under_policy(self):
        with pytest.raises(PlanError, match="POLICY_REJECTED"):
            compile_ir(_ir(), GraphPolicy(allowed_tools=("read_file",)))

    def test_budget_clamped_not_silently_kept(self):
        ir = _ir(policies={"budget": {"max_cost_usd": 999}})
        g, notes = compile_ir(ir, GraphPolicy(max_cost_usd=1.0))
        assert any("clamped" in n for n in notes)
        assert g.policies.max_cost_usd == 1.0

    def test_retry_clamped(self):
        ir = _ir(nodes={"a": {"type": "agent", "retry": {"max_attempts": 9}}})
        g, notes = compile_ir(ir, GraphPolicy(max_retries=2))
        assert any("clamped" in n for n in notes)

    def test_forbidden_model_rejected(self):
        ir = _ir(nodes={"a": {"type": "agent", "model": {"model": "evil-9"}}})
        with pytest.raises(PlanError, match="POLICY_REJECTED"):
            compile_ir(ir, GraphPolicy(allowed_models=("good",)))

    def test_host_workspace_wins(self):
        ir = _ir(policies={"workspace": "/evil"})
        # workspace key inside node rejected; policy-level forced to host
        g, _ = compile_ir(ir, GraphPolicy(workspace="/host"))
        assert g.policies.workspace == "/host"

    def test_compile_proposal_lifecycle(self):
        p = compile_proposal(_ir(), "do x", GraphPolicy())
        assert p["status"] == "APPROVAL_REQUIRED"
        assert p["graph_hash"] and p["ir_hash"]
        assert p["quality"]["score"] >= 0

    def test_invalid_proposal_marked(self):
        p = compile_proposal(_ir(shape="NOPE"), "do x", None)
        assert p["status"] in ("INVALID", "REJECTED")
        assert p["graph_hash"] == ""
