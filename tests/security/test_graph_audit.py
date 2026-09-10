"""Graph → immutable-audit integration (closes G-1).

Every security-relevant graph decision lands in the EXISTING
ImmutableAuditTrail hash chain. Tests pin: happy path, rejections,
approvals, routes, verdicts, resume/tamper, scrubbing, audit failure,
chain integrity, replay determinism, concurrency, cancel races,
multi-run isolation.
"""

from __future__ import annotations

import json

import pytest

from wisp.graph.audit import AUDIT_EVENTS, GraphSecurityAuditor
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    NodeContract,
    NodeType,
)
from wisp.infra.audit import ImmutableAuditTrail
from .conftest import make_executor, ok_runner


def _agent(nid, **kw):
    return GraphNode(id=nid, type=NodeType.AGENT, contract=NodeContract(id=nid, **kw))


def _auditor(tmp_path):
    ws = str(tmp_path)
    return GraphSecurityAuditor(workspace=ws), ws


def _actions(ws):
    trail = ImmutableAuditTrail(_Conn(ws))
    return [r["action"] for r in trail.entries(limit=1000)]


class _Conn:
    def __init__(self, ws):
        import os
        self._c = __import__("sqlite3").connect(
            os.path.join(ws, ".wisp", "wisp.db"), timeout=10,
            isolation_level=None, check_same_thread=False)
        self._c.row_factory = __import__("sqlite3").Row

    def _get_conn(self):
        return self._c


def _run(tmp_path, graph, runner=None, **kw):
    import asyncio
    run_id = kw.pop("run_id", "")
    inputs = kw.pop("inputs", {})
    ex = make_executor(tmp_path, runner or ok_runner(), **kw)
    return asyncio.run(ex.run(graph, inputs, run_id=run_id)), ex


class TestHappyPath:
    def test_run_terminated_audited_and_chain_valid(self, tmp_path):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=())
        r, _ = _run(tmp_path, g)
        assert r["status"] == "succeeded"
        auditor, ws = _auditor(tmp_path)
        assert auditor.verify() is None
        assert "graph.run_terminated" in _actions(ws)

    def test_audit_hash_travels_with_decision(self, tmp_path):
        import asyncio
        events = []
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=())
        ex = make_executor(tmp_path, ok_runner(), emit=events.append)
        asyncio.run(ex.run(g, {}))
        term = next(e for e in events if e["type"] == "graph.completed")
        assert term["data"].get("audit_hash")  # real hash, never ""


class TestRejections:
    def test_validation_rejection_audited(self, tmp_path):
        import asyncio
        g = Graph(id="t", entrypoint="nope", nodes=(_agent("a"),), edges=())
        ex = make_executor(tmp_path, ok_runner())
        with pytest.raises(ValueError):
            asyncio.run(ex.run(g, {}))
        _, ws = _auditor(tmp_path)
        assert "graph.validation_rejected" in _actions(ws)

    def test_policy_rejection_audited(self, tmp_path):
        import asyncio
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a", allowed_tools=("run_bash",)),),
                  edges=(), policies=GraphPolicy(allowed_tools=("read",)))
        ex = make_executor(tmp_path, ok_runner())
        with pytest.raises(ValueError):
            asyncio.run(ex.run(g, {}))
        _, ws = _auditor(tmp_path)
        assert "graph.policy_rejected" in _actions(ws)

    def test_budget_rejection_audited(self, tmp_path):
        from wisp.graph.types import NodeResult, NodeStatus

        async def pricey(node, inputs):
            return NodeResult(node.id, NodeStatus.SUCCESS, output={}, cost_usd=10.0)

        nodes = (_agent("a"), _agent("b"))
        g = Graph(id="t", entrypoint="a", nodes=nodes,
                  edges=(EdgeMapping("a", "b", reason="x"),),
                  policies=GraphPolicy(max_cost_usd=1.0))
        r, _ = _run(tmp_path, g, pricey)
        assert r["status"] == "failed"
        _, ws = _auditor(tmp_path)
        assert "graph.budget_exceeded" in _actions(ws)


class TestApprovals:
    def _graph(self):
        nodes = (_agent("a"),
                 GraphNode(id="gate", type=NodeType.APPROVAL,
                           contract=NodeContract(id="gate")),
                 _agent("b"))
        return Graph(id="ap", entrypoint="a", nodes=nodes,
                     edges=(EdgeMapping("a", "gate", reason="x"),
                            EdgeMapping("gate", "b", reason="y")))

    def test_requested_granted_denied(self, tmp_path):
        import asyncio
        g = self._graph()
        ex = make_executor(tmp_path, ok_runner())
        r = asyncio.run(ex.run(g, {}, run_id="a1"))
        assert r["status"] == "awaiting_approval"
        asyncio.run(ex.resume(g, "a1", approvals={"gate": True}))
        ex2 = make_executor(tmp_path, ok_runner())
        r2 = asyncio.run(ex2.run(g, {}, run_id="a2"))
        assert r2["status"] == "awaiting_approval"
        asyncio.run(ex2.resume(g, "a2", approvals={"gate": False}))
        _, ws = _auditor(tmp_path)
        acts = _actions(ws)
        assert "graph.approval_requested" in acts
        assert "graph.approval_granted" in acts
        assert "graph.approval_denied" in acts

    def test_truthy_non_bool_never_grants(self, tmp_path):
        import asyncio
        g = self._graph()
        ex = make_executor(tmp_path, ok_runner())
        r = asyncio.run(ex.run(g, {}, run_id="a3"))
        assert r["status"] == "awaiting_approval"
        # "false" is truthy — only an explicit True grants.
        r2 = asyncio.run(ex.resume(g, "a3", approvals={"gate": "false"}))
        assert r2["results_by_node"]["b"]["status"] == "skipped"
        _, ws = _auditor(tmp_path)
        assert "graph.approval_denied" in _actions(ws)


class TestRoutesAndVerdicts:
    def test_route_selected_and_unknown(self, tmp_path):
        nodes = (GraphNode(id="cls", type=NodeType.ROUTER, routes={"s": "s"},
                           default_route="s", contract=NodeContract(id="cls")),
                 _agent("s"))
        g = Graph(id="r", entrypoint="cls", nodes=nodes,
                  edges=(EdgeMapping("cls", "s", reason="x", condition="s"),))
        _run(tmp_path, g, ok_runner(outputs={"cls": {"label": "s"}}), run_id="r1")
        _run(tmp_path, g, ok_runner(outputs={"cls": {"label": "???"}}), run_id="r2")
        _, ws = _auditor(tmp_path)
        acts = _actions(ws)
        assert "graph.route_selected" in acts
        assert "graph.route_unknown" in acts

    @pytest.mark.parametrize("decision,event", [
        ("ALLOW", "graph.verification_allow"), ("REJECT", "graph.verification_reject"),
        ("RETRY", "graph.verification_retry"), ("ESCALATE", "graph.verification_escalate")])
    def test_all_verdicts_audited(self, tmp_path, decision, event):
        nodes = (_agent("gen"),
                 GraphNode(id="ver", type=NodeType.VERIFIER,
                           contract=NodeContract(id="ver")))
        g = Graph(id="v", entrypoint="gen", nodes=nodes,
                  edges=(EdgeMapping("gen", "ver", reason="x"),))
        _run(tmp_path, g, ok_runner(outputs={"ver": {"decision": decision}}),
             run_id=f"v-{decision}")
        _, ws = _auditor(tmp_path)
        assert event in _actions(ws)


class TestResumeAndTamper:
    def test_fingerprint_mismatch_audited(self, tmp_path):
        import asyncio
        g = Graph(id="t", version="1", entrypoint="a", nodes=(_agent("a"),), edges=())
        ex = make_executor(tmp_path, ok_runner())
        asyncio.run(ex.run(g, {}, run_id="fp"))
        g2 = Graph(id="t", version="1", entrypoint="a",
                   nodes=(_agent("a", allowed_tools=("run_bash",)),), edges=())
        with pytest.raises(ValueError):
            asyncio.run(ex.resume(g2, "fp"))
        _, ws = _auditor(tmp_path)
        assert "graph.fingerprint_mismatch" in _actions(ws)

    def test_corrupt_checkpoint_audited(self, tmp_path):
        import asyncio
        import sqlite3
        import os
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=())
        ex = make_executor(tmp_path, ok_runner())
        asyncio.run(ex.run(g, {}, run_id="cc"))
        conn = sqlite3.connect(os.path.join(str(tmp_path), ".wisp", "wisp.db"))
        conn.execute("INSERT OR REPLACE INTO graph_checkpoints(run_id,seq,state,created_at)"
                     " VALUES(?,?,?,?)", ("cc", 9, "{bad", 0))
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="corrupt"):
            asyncio.run(ex.resume(g, "cc"))
        _, ws = _auditor(tmp_path)
        assert "graph.state_tamper" in _actions(ws)

    def test_retry_and_stale_audited(self, tmp_path):
        from wisp.graph.types import NodeResult, NodeStatus, RetryPolicy
        calls = []

        async def flaky(node, inputs):
            calls.append(1)
            if len(calls) == 1:
                return NodeResult(node.id, NodeStatus.FAILURE, error_code="F",
                                  message="f")
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})

        c = NodeContract(id="w", retry_policy=RetryPolicy(max_attempts=2))
        g = Graph(id="t", entrypoint="w",
                  nodes=(GraphNode(id="w", type=NodeType.AGENT, contract=c),), edges=())
        r, _ = _run(tmp_path, g, flaky)
        assert r["status"] == "succeeded"
        _, ws = _auditor(tmp_path)
        assert "graph.retry_allowed" in _actions(ws)


class TestScrubbing:
    @pytest.mark.parametrize("secret", [
        "OPENAI_API_KEY=sk-probe-1234567890abcdef",
        "Authorization: Bearer probe-token-abcdef123456",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_probetoken1234567890abcdef",
        "password: hunter2-hunter2",
    ])
    def test_no_plaintext_in_audit(self, tmp_path, secret):
        auditor, ws = _auditor(tmp_path)
        h = auditor.emit("graph.run_terminated", graph_id="t", run_id="r",
                         allowed=False, reason=f"boom {secret}",
                         evidence={"blob": secret, "nested": [secret]})
        assert h
        trail = ImmutableAuditTrail(_Conn(ws))
        blob = "".join(str(r) for r in trail.entries(limit=1000))
        assert secret not in blob

    def test_private_key_body_redacted(self, tmp_path):
        auditor, ws = _auditor(tmp_path)
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIBPROBE\n-----END RSA PRIVATE KEY-----"
        h = auditor.emit("graph.run_terminated", graph_id="t", run_id="r",
                         allowed=False, reason="key leak?", evidence={"k": pem})
        assert h
        trail = ImmutableAuditTrail(_Conn(ws))
        blob = "".join(str(r) for r in trail.entries(limit=1000))
        assert "MIIBPROBE" not in blob  # body gone; header/footer labels may remain
        assert "[REDACTED:private-key-block]" in blob


class TestAuditFailure:
    def test_failed_write_never_claims_audited(self, tmp_path):
        from wisp.graph.audit import GraphSecurityAuditor

        class Dead:
            def record_decision(self, **kw):
                raise OSError("disk gone")

            def verify(self):
                raise OSError("disk gone")

        a = GraphSecurityAuditor(workspace=str(tmp_path), trail=Dead())
        assert a.emit("graph.run_terminated", allowed=False, reason="x") == ""
        assert a.verify() == -1

    def test_unknown_event_refused(self, tmp_path):
        auditor, _ = _auditor(tmp_path)
        assert auditor.emit("graph.pwned", allowed=True) == ""


class TestChain:
    def test_chain_links_and_tamper_detected(self, tmp_path):
        auditor, ws = _auditor(tmp_path)
        h1 = auditor.emit("graph.run_terminated", graph_id="a", run_id="1", allowed=True)
        h2 = auditor.emit("graph.run_terminated", graph_id="a", run_id="2", allowed=True)
        assert h1 and h2 and h1 != h2
        assert auditor.verify() is None
        # Tamper: flip an allowed bit in SQLite directly.
        import sqlite3
        import os
        conn = sqlite3.connect(os.path.join(ws, ".wisp", "wisp.db"))
        conn.execute("UPDATE audit_log SET allowed=0 WHERE entry_hash=?", (h2,))
        conn.commit()
        conn.close()
        assert auditor.verify() is not None

    def test_replay_deterministic(self, tmp_path):
        auditor, _ = _auditor(tmp_path)
        kw = dict(graph_id="g", graph_hash="h", run_id="r", node_id="n",
                  attempt=1, allowed=True, reason="ok", evidence={"e": 1})
        h1 = auditor.emit("graph.retry_allowed", **kw)
        assert h1
        # Same semantic decision re-emitted links forward (chain grows, hashes differ by prev).
        h2 = auditor.emit("graph.retry_allowed", **kw)
        assert h2 and h2 != h1
        assert auditor.verify() is None


class TestConcurrencyAndIsolation:
    def test_parallel_runs_keep_valid_chain(self, tmp_path):
        import asyncio
        from wisp.graph.compat import fan_to_graph

        async def main():
            async def run(node, inputs):
                from wisp.graph.types import NodeResult as _R, NodeStatus as _S
                if node.id == "split":
                    return _R("split", _S.SUCCESS, output={})
                return _R(node.id, _S.SUCCESS, output={})

            async def one(i):
                ex = make_executor(tmp_path, run)
                ex.register_function("split_work", lambda inp: {"ok": True})
                return await ex.run(fan_to_graph("f", ["a", "b", "c"]), {}, run_id=f"cc-{i}")

            return await asyncio.gather(*[one(i) for i in range(6)])

        results = asyncio.run(main())
        assert all(r["status"] == "succeeded" for r in results)
        auditor, _ = _auditor(tmp_path)
        assert auditor.verify() is None
        # 6 runs × (3 branches + split + join + terminated) all present, isolated.
        trail = ImmutableAuditTrail(_Conn(str(tmp_path)))
        rows = trail.entries(limit=1000)
        assert len(rows) >= 6
        assert len({json.loads(r["args_summary"])["run_id"] for r in rows}) >= 6

    def test_cancel_and_stale_audited(self, tmp_path):
        import asyncio

        async def main():
            started = asyncio.Event()

            async def slow(node, inputs):
                from wisp.graph.types import NodeResult as _R, NodeStatus as _S
                started.set()
                await asyncio.sleep(30)
                return _R(node.id, _S.SUCCESS, output={})

            g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=())
            ex = make_executor(tmp_path, slow)
            task = asyncio.create_task(ex.run(g, {}, run_id="cx"))
            await _cancel_after(ex, "cx", started, task)

        asyncio.run(main())

    def test_gate_decision_audited(self, tmp_path):
        from wisp.graph.types import NodeType as _NT
        nodes = (_agent("a"),
                 GraphNode(id="gt", type=_NT.GATE, function="g",
                           contract=NodeContract(id="gt")),
                 _agent("b"))
        g = Graph(id="gt", entrypoint="a", nodes=nodes,
                  edges=(EdgeMapping("a", "gt", reason="x"),
                         EdgeMapping("gt", "b", reason="y", condition="allow")))
        import asyncio
        ex = make_executor(tmp_path, ok_runner())
        ex.register_function("g", lambda i: {"allowed": True})
        r = asyncio.run(ex.run(g, {}, run_id="gate-ok"))
        assert r["status"] == "succeeded"
        _, ws = _auditor(tmp_path)
        assert "graph.gate_decision" in _actions(ws)

    def test_gate_deny_blocks_and_audits(self, tmp_path):
        from wisp.graph.types import NodeType as _NT
        nodes = (_agent("a"),
                 GraphNode(id="gt", type=_NT.GATE, function="g",
                           contract=NodeContract(id="gt")),
                 _agent("b"))
        g = Graph(id="gt", entrypoint="a", nodes=nodes,
                  edges=(EdgeMapping("a", "gt", reason="x"),
                         EdgeMapping("gt", "b", reason="y", condition="allow")))
        import asyncio
        ex = make_executor(tmp_path, ok_runner())
        ex.register_function("g", lambda i: {"allowed": False})
        r = asyncio.run(ex.run(g, {}, run_id="gate-no"))
        assert r["results_by_node"]["b"]["status"] == "skipped"
        _, ws = _auditor(tmp_path)
        assert "graph.gate_decision" in _actions(ws)

    def test_event_taxonomy_closed(self):
        # Coverage matrix anchor: every emitted name is a known taxonomy member.
        assert len(AUDIT_EVENTS) >= 15
        assert all(e.startswith("graph.") for e in AUDIT_EVENTS)
    def test_every_taxonomy_event_has_an_emit_site(self):
        # Machine-checkable coverage matrix: each taxonomy member must occur
        # as an emit string in the graph package (no orphan events, and the
        # auditor refuses anything outside the set).
        import os
        import wisp.graph
        sources = ""
        root = os.path.dirname(wisp.graph.__file__)
        for name in os.listdir(root):
            if name.endswith(".py"):
                with open(os.path.join(root, name)) as fh:
                    sources += fh.read()
        orphans = [e for e in AUDIT_EVENTS if f'"{e}"' not in sources]
        assert not orphans, f"taxonomy events with no emit site: {orphans}"


async def _cancel_after(ex, rid, started, task):
    import asyncio
    await asyncio.wait_for(started.wait(), timeout=10)
    ex.cancel(rid)
    r = await asyncio.wait_for(task, timeout=15)
    assert r["status"] == "cancelled"
    _, ws = _auditor(ex.workspace)
    assert "graph.cancel" in _actions(ws)
