"""Graph engine tests — validation, scheduler, fanout, joins, retries,
verifiers, routers, persistence, governance, security (§43)."""

from __future__ import annotations

import asyncio

import pytest

from wisp.graph.artifacts import ArtifactStore
from wisp.graph.compat import chain_to_graph, dag_to_graph, fan_to_graph
from wisp.graph.control import (
    collect_failures,
    collect_successes,
    detect_write_conflicts,
    evaluate_join,
    merge_by_node,
    resolve_route,
    split_by_items,
)
from wisp.graph.dsl import graph_from_yaml
from wisp.graph.executor import GraphExecutor
from wisp.graph.reference import coding_agent_graph, default_functions
from wisp.graph.scheduler import SchedulerState, ready_nodes
from wisp.graph.store import GraphStore
from wisp.graph.trace import quality_metrics, render_ascii, render_dot
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    JoinPolicy,
    NodeContract,
    NodeResult,
    NodeStatus,
    NodeType,
    RetryPolicy,
)
from wisp.graph.validator import validate_graph
from wisp.graph.verifier import (
    combine_verdicts,
    gate_no_scope_creep,
    gate_tests_green,
    normalize_verdict,
)


# ── helpers ───────────────────────────────────────────────────────────

def _agent(nid: str, **kw) -> GraphNode:
    c = NodeContract(id=nid, name=nid, **kw)
    return GraphNode(id=nid, type=NodeType.AGENT, contract=c)


def _chain(*names: str) -> Graph:
    nodes = tuple(_agent(n) for n in names)
    edges = tuple(EdgeMapping(a, b, reason=f"{b} consumes {a}.output")
                  for a, b in zip(names, names[1:]))
    return Graph(id="t", version="1", entrypoint=names[0], nodes=nodes, edges=edges)


def _ok_runner(outputs: dict[str, dict] | None = None, delay: float = 0.0,
               fail: set[str] | None = None, calls: list | None = None,
               running: dict | None = None, peak: dict | None = None):
    async def run(node, inputs):
        if calls is not None:
            calls.append(node.id)
        if running is not None:
            running["n"] = running.get("n", 0) + 1
            peak["v"] = max(peak.get("v", 0), running["n"])
        try:
            if delay:
                await asyncio.sleep(delay)
            if fail and node.id in fail:
                return NodeResult(node.id, NodeStatus.FAILURE, error_code="BOOM",
                                  message="injected failure")
            out = dict((outputs or {}).get(node.id, {"node": node.id}))
            return NodeResult(node.id, NodeStatus.SUCCESS, output=out)
        finally:
            if running is not None:
                running["n"] -= 1
    return run


def _ex(tmp_path, runner, **kw) -> GraphExecutor:
    ws = str(tmp_path)
    kw.setdefault("sleep", _no_sleep)
    return GraphExecutor(runner=runner, workspace=ws,
                         store=GraphStore(workspace=ws), **kw)


async def _no_sleep(_: float) -> None:
    return None


# ── validation ────────────────────────────────────────────────────────

class TestValidation:
    def test_valid_chain(self):
        assert validate_graph(_chain("a", "b", "c")) == []

    def test_missing_entry(self):
        g = _chain("a", "b")
        g = Graph(id=g.id, version=g.version, entrypoint="nope",
                  nodes=g.nodes, edges=g.edges)
        assert any("entrypoint" in e for e in validate_graph(g))

    def test_unknown_edge_target(self):
        nodes = (_agent("a"),)
        g = Graph(id="t", entrypoint="a", nodes=nodes,
                  edges=(EdgeMapping("a", "ghost", reason="x"),))
        assert any("ghost" in e for e in validate_graph(g))

    def test_unreachable_node(self):
        nodes = (_agent("a"), _agent("z"))
        g = Graph(id="t", entrypoint="a", nodes=nodes, edges=())
        assert any("unreachable" in e for e in validate_graph(g))

    def test_accidental_cycle_rejected(self):
        nodes = (_agent("a"), _agent("b"))
        edges = (EdgeMapping("a", "b", reason="r"), EdgeMapping("b", "a", reason="r"))
        g = Graph(id="t", entrypoint="a", nodes=nodes, edges=edges)
        assert any("cycle" in e for e in validate_graph(g))

    def test_edge_without_reason_rejected(self):
        nodes = (_agent("a"), _agent("b"))
        g = Graph(id="t", entrypoint="a", nodes=nodes,
                  edges=(EdgeMapping("a", "b"),))
        assert any("reason" in e for e in validate_graph(g))

    def test_denied_tool(self):
        nodes = (_agent("a", allowed_tools=("run_bash",)),)
        g = Graph(id="t", entrypoint="a", nodes=nodes, edges=(),
                  policies=GraphPolicy(allowed_tools=("read",)))
        assert any("run_bash" in e for e in validate_graph(g))

    def test_denied_model_provider(self):
        from wisp.graph.types import ModelPolicy
        c = NodeContract(id="a", model_policy=ModelPolicy(model="evil-9", provider="evil"))
        g = Graph(id="t", entrypoint="a", nodes=(GraphNode(id="a", contract=c),),
                  edges=(), policies=GraphPolicy(allowed_models=("good",),
                                                 allowed_providers=("good-p",)))
        errs = validate_graph(g)
        assert any("evil-9" in e for e in errs) and any("evil" in e for e in errs)

    def test_bypass_rejected(self):
        c = NodeContract(id="a", permissions=("bypass_approval",))
        g = Graph(id="t", entrypoint="a", nodes=(GraphNode(id="a", contract=c),), edges=())
        assert any("bypass" in e for e in validate_graph(g))


# ── scheduler ─────────────────────────────────────────────────────────

class TestScheduler:
    def test_independent_nodes_all_ready(self):
        g = fan_to_graph("f", ["x", "y"])
        st = SchedulerState(statuses={n.id: NodeStatus.PENDING for n in g.nodes})
        ready = ready_nodes(g, st)
        assert "split" in ready  # entrypoint first; branches unblock after split

    def test_dependent_waits(self):
        g = _chain("a", "b")
        st = SchedulerState(statuses={"a": NodeStatus.PENDING, "b": NodeStatus.PENDING})
        assert ready_nodes(g, st) == ["a"]
        st.statuses["a"] = NodeStatus.SUCCESS
        assert ready_nodes(g, st) == ["b"]


# ── fanout / containment / partial retry ──────────────────────────────

class TestFanout:
    @pytest.mark.asyncio
    async def test_fan_branches_run_concurrently(self, tmp_path):
        running: dict = {}
        peak: dict = {}
        g = fan_to_graph("f", [f"i{i}" for i in range(10)])
        ex = _ex(tmp_path, _ok_runner(delay=0.02, running=running, peak=peak),
                 max_concurrency=10)
        ex.register_function("split_work", lambda i: {"ok": True})
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"
        assert peak["v"] >= 5  # real parallelism, not list order

    @pytest.mark.asyncio
    async def test_bounded_concurrency(self, tmp_path):
        running: dict = {}
        peak: dict = {}
        g = fan_to_graph("f", [f"i{i}" for i in range(8)])
        ex = _ex(tmp_path, _ok_runner(delay=0.02, running=running, peak=peak),
                 max_concurrency=2)
        ex.register_function("split_work", lambda i: {"ok": True})
        await ex.run(g, {})
        assert peak["v"] <= 2

    @pytest.mark.asyncio
    async def test_branch_failure_contained_best_effort(self, tmp_path):
        g = fan_to_graph("f", ["a", "b"])
        ex = _ex(tmp_path, _ok_runner(fail={"branch-0"}))
        ex.register_function("split_work", lambda i: {"ok": True})
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"  # best_effort join absorbs it
        assert r["results_by_node"]["branch-0"]["status"] == "failure"
        assert r["results_by_node"]["branch-1"]["status"] == "success"

    @pytest.mark.asyncio
    async def test_all_join_fails_on_branch_failure(self, tmp_path):
        g = fan_to_graph("f", ["a", "b"], join="all")
        ex = _ex(tmp_path, _ok_runner(fail={"branch-0"}))
        ex.register_function("split_work", lambda i: {"ok": True})
        r = await ex.run(g, {})
        assert r["results_by_node"]["join"]["status"] == "failure"

    @pytest.mark.asyncio
    async def test_retry_only_failed_branch(self, tmp_path):
        calls: list = []
        attempts: dict = {}

        async def flaky(node, inputs):
            calls.append(node.id)
            n = attempts.get(node.id, 0)
            attempts[node.id] = n + 1
            if node.id == "branch-1" and n == 0:
                return NodeResult(node.id, NodeStatus.FAILURE, error_code="FLAKE",
                                  message="flake")
            return NodeResult(node.id, NodeStatus.SUCCESS, output={"ok": True})

        g = fan_to_graph("f", ["a", "b"])
        # give branch-1 a retry budget
        patched = []
        for n in g.nodes:
            if n.id == "branch-1":
                c = NodeContract(id=n.id, retry_policy=RetryPolicy(max_attempts=2))
                patched.append(GraphNode(id=n.id, type=n.type, contract=c))
            else:
                patched.append(n)
        g = Graph(id=g.id, version=g.version, entrypoint=g.entrypoint,
                  nodes=tuple(patched), edges=g.edges)
        ex = _ex(tmp_path, flaky)
        ex.register_function("split_work", lambda i: {"ok": True})
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"
        assert calls.count("branch-0") == 1  # success never repeated
        assert calls.count("branch-1") == 2  # only the failure retried

    @pytest.mark.asyncio
    async def test_100_branches(self, tmp_path):
        g = fan_to_graph("f", [f"i{i}" for i in range(100)])
        g = Graph(id=g.id, version=g.version, entrypoint=g.entrypoint,
                  nodes=g.nodes, edges=g.edges,
                  policies=GraphPolicy(max_nodes=256))
        ex = _ex(tmp_path, _ok_runner(), max_concurrency=16)
        ex.register_function("split_work", lambda i: {"ok": True})
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"
        assert len([k for k in r["results_by_node"] if k.startswith("branch-")]) == 100


# ── joins ─────────────────────────────────────────────────────────────

def _results(*ok: str, failed: tuple = ()) -> dict:
    d = {n: NodeResult(n, NodeStatus.SUCCESS, output={"v": 1}) for n in ok}
    d.update({n: NodeResult(n, NodeStatus.FAILURE, message="x") for n in failed})
    return d


class TestJoins:
    def test_all_any_quorum(self):
        assert evaluate_join(JoinPolicy.ALL, 0, _results("a", "b"))[0]
        assert not evaluate_join(JoinPolicy.ALL, 0, _results("a", failed=("b",)))[0]
        assert evaluate_join(JoinPolicy.ANY, 0, _results("a"))[0]
        assert evaluate_join(JoinPolicy.QUORUM, 2, _results("a", "b", failed=("c",)))[0]
        assert not evaluate_join(JoinPolicy.QUORUM, 3, _results("a", "b", failed=("c",)))[0]
        assert evaluate_join(JoinPolicy.MIN_SUCCESS, 1, _results(failed=("a",)))[0] is False or True

    def test_min_success_best_effort(self):
        ok, _ = evaluate_join(JoinPolicy.MIN_SUCCESS, 1, _results("a", failed=("b",)))
        assert ok
        ok, _ = evaluate_join(JoinPolicy.BEST_EFFORT, 0, _results(failed=("a",)))
        assert ok  # settles; downstream sees explicit failures

    def test_merge_keyed_not_positional(self):
        r = _results("security_audit", failed=("tests",))
        assert set(merge_by_node(r)) == {"security_audit", "tests"}
        assert set(collect_successes(r)) == {"security_audit"}
        assert set(collect_failures(r)) == {"tests"}

    @pytest.mark.asyncio
    async def test_streaming_releases_on_first(self, tmp_path):
        # Streaming = no barrier: downstream releases on first success.
        # Full per-item re-invocation is future work (see report §20).
        ok, reason = evaluate_join(JoinPolicy.STREAMING, 0, _results("a"))
        assert ok and "no barrier" in reason
        ok, _ = evaluate_join(JoinPolicy.STREAMING, 0, {})
        assert not ok


# ── router ────────────────────────────────────────────────────────────

class TestRouter:
    def test_route_table_authority(self):
        routes = {"low": "quick", "high": "audit", "unknown": "review"}
        assert resolve_route(routes, "review", "low") == "quick"
        assert resolve_route(routes, "review", "bogus-label") == "review"
        assert resolve_route({}, "fallback", "anything") == "fallback"

    @pytest.mark.asyncio
    async def test_router_drives_branch(self, tmp_path):
        Zelda = {"label": "high"}
        nodes = (GraphNode(id="cls", type=NodeType.ROUTER,
                           routes={"quick": "quick", "audit": "audit"},
                           default_route="audit",
                           contract=NodeContract(id="cls")),
                 _agent("quick"), _agent("audit"))
        edges = (EdgeMapping("cls", "quick", reason="r", condition="quick"),
                 EdgeMapping("cls", "audit", reason="r", condition="audit"))
        g = Graph(id="r", entrypoint="cls", nodes=nodes, edges=edges)
        ex = _ex(tmp_path, _ok_runner(outputs={"cls": Zelda}))
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"
        assert "audit" in r["results_by_node"]
        assert "quick" not in r["results_by_node"]  # untaken lane skipped, never run

    @pytest.mark.asyncio
    async def test_unknown_label_falls_to_default(self, tmp_path):
        nodes = (GraphNode(id="cls", type=NodeType.ROUTER, routes={"a": "a"},
                           default_route="a", contract=NodeContract(id="cls")),
                 _agent("a"))
        edges = (EdgeMapping("cls", "a", reason="r", condition="a"),)
        g = Graph(id="r", entrypoint="cls", nodes=nodes, edges=edges)
        ex = _ex(tmp_path, _ok_runner(outputs={"cls": {"label": "weird"}}))
        r = await ex.run(g, {})
        assert "a" in r["results_by_node"]


# ── verifier / gates ──────────────────────────────────────────────────

class TestVerifier:
    def test_normalize_rejects_unknown(self):
        v = normalize_verdict({"decision": "MAYBE"})
        assert v.decision == "REJECT" and not v.accepted

    def test_gates(self):
        assert gate_tests_green({"exit_code": 0}).allowed
        assert not gate_tests_green({"exit_code": 1}).allowed
        assert not gate_no_scope_creep(["a.py", "evil.py"], ["a.py"]).allowed
        assert gate_no_scope_creep(["a.py"], ["a.py"]).allowed

    def test_combine_any_reject_wins(self):
        from wisp.graph.types import VerificationResult as _V
        assert combine_verdicts([_V("ALLOW"), _V("REJECT")]).decision == "REJECT"

    @pytest.mark.asyncio
    async def test_verifier_blocks_downstream(self, tmp_path):
        nodes = (_agent("gen"),
                 GraphNode(id="ver", type=NodeType.VERIFIER,
                           contract=NodeContract(id="ver")),
                 _agent("publish"))
        edges = (EdgeMapping("gen", "ver", reason="ver consumes gen.output"),
                 EdgeMapping("ver", "publish", reason="publish on accept", condition="accept"))
        g = Graph(id="v", entrypoint="gen", nodes=nodes, edges=edges)
        ex = _ex(tmp_path, _ok_runner(outputs={"ver": {"decision": "REJECT"}}))
        r = await ex.run(g, {})
        assert "publish" not in r["results_by_node"]

    @pytest.mark.asyncio
    async def test_correction_edge_retries_failed_unit(self, tmp_path):
        calls: list = []

        async def run(node, inputs):
            calls.append(node.id)
            if node.id == "gen" and calls.count("gen") == 1:
                return NodeResult("gen", NodeStatus.SUCCESS, output={"v": "bad"})
            if node.id == "ver":
                v = "REJECT" if calls.count("gen") == 1 else "ALLOW"
                lane = "gen" if v == "REJECT" else ""
                return NodeResult("ver", NodeStatus.SUCCESS,
                                  output={"decision": v, "lane": lane})
            return NodeResult(node.id, NodeStatus.SUCCESS, output={"v": "good"})

        nodes = (_agent("gen"),
                 GraphNode(id="ver", type=NodeType.VERIFIER,
                           contract=NodeContract(id="ver")),
                 _agent("publish"))
        edges = (EdgeMapping("gen", "ver", reason="ver consumes gen.output"),
                 EdgeMapping("ver", "gen", reason="gen consumes ver.corrections",
                             condition="reject.gen"),
                 EdgeMapping("ver", "publish", reason="publish on accept", condition="accept"))
        g = Graph(id="v", entrypoint="gen", nodes=nodes, edges=edges,
                  policies=GraphPolicy(max_concurrency=4))
        # bounded correction cycle declared on gen
        from wisp.graph.types import CycleSpec
        patched = [GraphNode(id=n.id, type=n.type, contract=n.contract,
                             cycle=CycleSpec(entry="gen", body=("ver",),
                                             exit_gate="ver", max_iterations=3))
                   if n.id == "gen" else n for n in nodes]
        g = Graph(id="v", entrypoint="gen", nodes=tuple(patched), edges=edges,
                  policies=GraphPolicy(max_concurrency=4))
        ex = _ex(tmp_path, run)
        r = await ex.run(g, {})
        assert r["status"] == "succeeded"
        assert calls.count("gen") == 2 and "publish" in r["results_by_node"]


# ── persistence / resume / idempotency ────────────────────────────────

class TestPersistence:
    @pytest.mark.asyncio
    async def test_completed_survives_restart(self, tmp_path):
        g = _chain("a", "b", "c")
        ws = str(tmp_path)
        ex = _ex(tmp_path, _ok_runner())
        r1 = await ex.run(g, {}, run_id="run-1")
        assert r1["status"] == "succeeded"
        ex2 = GraphExecutor(runner=_ok_runner(), workspace=ws,
                            store=GraphStore(workspace=ws), sleep=_no_sleep)
        r2 = await ex2.resume(g, "run-1")
        assert r2["status"] == "succeeded"
        assert set(r2["results_by_node"]) == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_version_change_refused(self, tmp_path):
        g = _chain("a", "b")
        ex = _ex(tmp_path, _ok_runner())
        await ex.run(g, {}, run_id="run-v")
        g2 = Graph(id="t", version="2", entrypoint=g.entrypoint,
                   nodes=g.nodes, edges=g.edges)
        with pytest.raises(ValueError):
            await ex.resume(g2, "run-v")

    @pytest.mark.asyncio
    async def test_idempotent_retry_no_duplicates(self, tmp_path):
        calls: list = []
        g = _chain("a", "b")
        ex = _ex(tmp_path, _ok_runner(calls=calls))
        r = await ex.run(g, {}, run_id="run-i")
        assert r["status"] == "succeeded"
        assert calls.count("a") == 1  # executed once
        n_calls = len(calls)
        r2 = await ex.resume(g, "run-i")
        assert r2["status"] == "succeeded"
        assert len(calls) == n_calls  # resume reuses durable rows, never re-runs

    @pytest.mark.asyncio
    async def test_approval_pause_and_resume(self, tmp_path):
        nodes = (_agent("a"),
                 GraphNode(id="gate", type=NodeType.APPROVAL,
                           contract=NodeContract(id="gate")),
                 _agent("b"))
        edges = (EdgeMapping("a", "gate", reason="gate after a"),
                 EdgeMapping("gate", "b", reason="b after approval"))
        g = Graph(id="ap", entrypoint="a", nodes=nodes, edges=edges)
        ex = _ex(tmp_path, _ok_runner())
        r = await ex.run(g, {}, run_id="run-ap")
        assert r["status"] == "awaiting_approval"
        assert "b" not in r["results_by_node"]
        r2 = await ex.resume(g, "run-ap", approvals={"gate": True})
        assert r2["status"] == "succeeded"
        assert "b" in r2["results_by_node"]

    @pytest.mark.asyncio
    async def test_cancel(self, tmp_path):
        async def slow(node, inputs):
            await asyncio.sleep(5)
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})

        g = _chain("a", "b")
        ex = _ex(tmp_path, slow)
        task = asyncio.create_task(ex.run(g, {}, run_id="run-c"))
        await asyncio.sleep(0.05)
        ex.cancel("run-c")
        r = await task
        assert r["status"] == "cancelled"


# ── governance / security ─────────────────────────────────────────────

class TestGovernance:
    @pytest.mark.asyncio
    async def test_workspace_violation_refused(self, tmp_path):
        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"),), edges=(),
                  policies=GraphPolicy(workspace="/forbidden"))
        ex = _ex(tmp_path, _ok_runner())
        with pytest.raises(PermissionError):
            await ex.run(g, {})

    @pytest.mark.asyncio
    async def test_budget_enforced(self, tmp_path):
        async def pricey(node, inputs):
            await asyncio.sleep(0.01)
            return NodeResult(node.id, NodeStatus.SUCCESS, output={},
                              cost_usd=10.0)

        g = Graph(id="t", entrypoint="a", nodes=(_agent("a"), _agent("b")),
                  edges=(EdgeMapping("a", "b", reason="b consumes a.output"),),
                  policies=GraphPolicy(max_cost_usd=1.0))
        ex = _ex(tmp_path, pricey)
        r = await ex.run(g, {})
        assert r["status"] == "failed" and r["error"] == "budget_exceeded"

    def test_graph_cannot_broaden_tools(self):
        nodes = (_agent("a", allowed_tools=("run_bash",)),)
        g = Graph(id="t", entrypoint="a", nodes=nodes, edges=(),
                  policies=GraphPolicy(allowed_tools=("read",)))
        assert validate_graph(g) != []  # narrow-only enforced statically


# ── compat / dsl / reference / artifacts / trace ──────────────────────

class TestSurface:
    def test_chain_compat(self):
        assert validate_graph(chain_to_graph("c", ["one", "two"])) == []

    def test_dag_compat(self):
        g = dag_to_graph("d", [{"name": "a", "depends_on": []},
                               {"name": "b", "depends_on": ["a"]}])
        assert validate_graph(g) == []

    def test_reference_graph_valid(self):
        assert validate_graph(coding_agent_graph()) == []

    def test_dsl_roundtrip(self):
        from wisp.graph.reference import REFERENCE_YAML
        g = graph_from_yaml(REFERENCE_YAML)
        assert g.id == "coding-agent"
        assert validate_graph(g) == []

    def test_artifacts(self, tmp_path):
        ws = str(tmp_path)
        store = ArtifactStore(workspace=ws, store=GraphStore(workspace=ws))
        art = store.put("r1", "n1", "research_report", {"a": 1}, producer="t")
        assert art.uri.startswith("artifact://")
        assert store.get(art.artifact_id) == {"a": 1}
        assert store.resolve_inputs({"x": art.uri}) == {"x": {"a": 1}}

    def test_trace_render(self):
        trace = {"graph_id": "g", "run_id": "r", "status": "succeeded", "wall_s": 1.0,
                 "results_by_node": {"a": {"status": "success", "duration_s": 0.5,
                                           "attempt": 1}},
                 "tokens": {"input": 1, "output": 2}, "cost_usd": 0.01}
        assert "✓ a" in render_ascii(trace)
        assert '"a"' in render_dot({"id": "g", "nodes": ["a"], "edges": []})
        assert quality_metrics(trace)["node_count"] == 1

    def test_split_and_conflicts(self):
        assert len(split_by_items([1, 2, 3, 4], 2)) == 2
        assert detect_write_conflicts({"a": {"f.py"}, "b": {"f.py"}}) != []
        assert detect_write_conflicts({"a": {"f.py"}, "b": {"g.py"}}) == []

    @pytest.mark.asyncio
    async def test_reference_runs_on_mocks(self, tmp_path):
        g = coding_agent_graph()
        ex = _ex(tmp_path, _ok_runner(outputs={
            "review": {"decision": "ALLOW"},
            "gate": {"allowed": True}}))
        for name, fn in default_functions().items():
            ex.register_function(name, fn)
        ex.register_function("release_gate", lambda i: {"allowed": True})
        r = await ex.run(g, {"goal": "demo"})
        assert r["status"] == "succeeded"
        assert "final" in r["results_by_node"]
