"""Phase 9D–9F: planner (mocked provider), proposals, pinned execution."""

from __future__ import annotations

import pytest

from wisp.graph.planner import (
    PlanError,
    compile_proposal,
    execute_proposal,
    plan_graph,
    propose,
)
from wisp.graph.store import GraphStore
from wisp.graph.types import GraphPolicy, NodeResult, NodeStatus


def _ir():
    return {"objective": "fix bug", "execution_shape": "SINGLE_AGENT",
            "graph": {"id": "fix", "entry": "a",
                      "nodes": {"a": {"type": "agent"}},
                      "edges": []}}


class StructuredProvider:
    def __init__(self, payload=None, fail=None):
        self.payload = payload
        self.fail = fail
        self.seen = []

    def generate_structured(self, system, messages, schema):
        self.seen.append((system, messages))
        if self.fail:
            raise self.fail
        return self.payload


class TextProvider:
    def __init__(self, text):
        self.text = text

    def generate(self, system, messages):
        return self.text


class TestPlanner:
    def test_valid_proposal_from_structured(self):
        p = StructuredProvider(_ir())
        ir = plan_graph("fix the bug", p, model="mock")
        assert ir["graph"]["id"] == "fix"
        assert "do NOT execute" in p.seen[0][0] or "no authority" in p.seen[0][0]

    def test_text_fallback_parses_json(self):
        import json
        p = TextProvider("```json\n" + json.dumps(_ir()) + "\n```")
        assert plan_graph("x", p)["graph"]["id"] == "fix"

    def test_malformed_output_rejected(self):
        with pytest.raises(PlanError, match="INVALID_PLANNER_OUTPUT"):
            plan_graph("x", TextProvider("no json here {{{"))

    def test_provider_failure_typed(self):
        with pytest.raises(PlanError, match="PLANNING_FAILED"):
            plan_graph("x", StructuredProvider(fail=RuntimeError("down")))

    def test_oversized_output_rejected(self):
        with pytest.raises(PlanError, match="size limit"):
            plan_graph("x", StructuredProvider({"graph": {"id": "x" * 300000}}))

    def test_non_dict_rejected(self):
        with pytest.raises(PlanError, match="INVALID_PLANNER_OUTPUT"):
            plan_graph("x", StructuredProvider(["not", "a", "dict"]))

    def test_empty_objective_rejected(self):
        with pytest.raises(PlanError):
            plan_graph("", StructuredProvider(_ir()))


class TestProposals:
    def test_full_propose_path(self):
        p = propose("fix it", StructuredProvider(_ir()), GraphPolicy(), model="m")
        assert p["status"] == "APPROVAL_REQUIRED"
        assert p["graph_hash"] and p["ir_hash"]

    def test_proposal_persisted_and_reloaded(self, tmp_path):
        store = GraphStore(workspace=str(tmp_path))
        p = compile_proposal(_ir(), "fix it", GraphPolicy(), proposal_id="p1")
        store.put_proposal(p)
        back = store.get_proposal("p1")
        assert back["ir_hash"] == p["ir_hash"]
        assert back["graph_hash"] == p["graph_hash"]
        assert back["ir"]["graph"]["id"] == "fix"
        store.set_proposal_status("p1", "APPROVED")
        assert store.get_proposal("p1")["status"] == "APPROVED"
        assert store.list_proposals()[0]["proposal_id"] == "p1"

    def test_proposal_roundtrip_hash_stable(self, tmp_path):
        store = GraphStore(workspace=str(tmp_path))
        p = compile_proposal(_ir(), "fix it", GraphPolicy(), proposal_id="p2")
        store.put_proposal(p)
        assert store.get_proposal("p2")["ir_hash"] == p["ir_hash"]

    def test_unknown_proposal_none(self, tmp_path):
        assert GraphStore(workspace=str(tmp_path)).get_proposal("nope") is None


class TestPinnedExecution:
    def _ex(self, tmp_path):
        from wisp.graph.executor import GraphExecutor

        async def run(node, inputs):
            return NodeResult(node.id, NodeStatus.SUCCESS, output={"ok": True})

        async def no_sleep(s):
            pass

        ws = str(tmp_path)
        return GraphExecutor(runner=run, workspace=ws,
                             store=GraphStore(workspace=ws), sleep=no_sleep)

    def test_execute_without_approval_refused(self, tmp_path):
        import asyncio
        p = compile_proposal(_ir(), "x", GraphPolicy())
        for bad in (False, "true", 1, None):
            with pytest.raises(PlanError, match="APPROVAL_REQUIRED"):
                asyncio.run(execute_proposal(p, self._ex(tmp_path), approve=bad))

    def test_execute_runs_exact_graph(self, tmp_path):
        import asyncio
        p = compile_proposal(_ir(), "x", GraphPolicy())
        r = asyncio.run(execute_proposal(p, self._ex(tmp_path), approve=True))
        assert r["status"] == "succeeded"

    def test_drift_forces_reproposal(self, tmp_path):
        import asyncio
        p = compile_proposal(_ir(), "x", GraphPolicy())
        p["ir"]["graph"]["nodes"]["a"]["timeout"] = 60  # still valid, new fingerprint
        with pytest.raises(PlanError, match="STALE_PROPOSAL"):
            asyncio.run(execute_proposal(p, self._ex(tmp_path), approve=True))

    def test_proposal_decided_audited(self, tmp_path):
        import asyncio
        from wisp.graph.audit import GraphSecurityAuditor
        p = compile_proposal(_ir(), "x", GraphPolicy())
        asyncio.run(execute_proposal(p, self._ex(tmp_path), approve=True))
        trail = GraphSecurityAuditor(workspace=str(tmp_path))
        assert trail.verify() is None
        from wisp.infra.audit import ImmutableAuditTrail
        import sqlite3
        import os
        conn = sqlite3.connect(os.path.join(str(tmp_path), ".wisp", "wisp.db"))
        conn.row_factory = sqlite3.Row

        class C:
            def _get_conn(self):
                return conn

        acts = [r["action"] for r in ImmutableAuditTrail(C()).entries(limit=100)]
        assert "graph.proposal_decided" in acts
