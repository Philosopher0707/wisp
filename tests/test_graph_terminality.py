"""G1C graph terminality & join honesty (§2-§21).

Invariant: SUCCESS is legal only when every required predecessor/branch
condition for that terminal path is satisfied. Timeout/cancel/failure/
incomplete joins never finalize as success.

Join-semantics matrix (DEFINED behavior, pinned — not redesigned):
  ALL: every branch must succeed (skipped/failed branch -> join FAILURE).
  ANY: first SETTLED branch releases (even failure — explicit partial).
  QUORUM/MIN_SUCCESS: need successes (failures don't release early).
  BEST_EFFORT: releases SUCCESS once all settled ("best_effort: partial").
  STREAMING: no barrier, first settled releases.
  TIMEOUT (any policy): TIMEOUT, never SUCCESS (G1C §4 fix).
  EMPTY join: FAILURE JOIN_UNSATISFIED (validator allows; executor refuses).
"""
from __future__ import annotations

import asyncio
import tempfile
import time

import pytest

from wisp.graph import Graph, GraphNode
from wisp.graph.api import run_graph
from wisp.graph.executor import GraphExecutor
from wisp.graph.scheduler import SchedulerState
from wisp.graph.store import GraphStore
from wisp.graph.types import (EdgeMapping, JoinPolicy, NodeContract,
                              NodeResult, NodeStatus, NodeType)


def _ws():
    return tempfile.mkdtemp(prefix="g1c-")


def _fan(policy=JoinPolicy.ALL, timeout_s=None, n=3):
    branches = [GraphNode(id=f"b{i}") for i in range(n)]
    nodes = [GraphNode(id="split"), *branches,
             GraphNode(id="j", type=NodeType.JOIN, join_policy=policy,
                       join_timeout_s=timeout_s)]
    edges = [EdgeMapping("split", b.id, reason="fan") for b in branches]
    edges += [EdgeMapping(b.id, "j", reason="join") for b in branches]
    return Graph(id="t", version="1", entrypoint="split", nodes=nodes,
                 edges=edges)


async def _ok(node, inputs):
    if node.id == "split":
        return NodeResult(node.id, NodeStatus.SUCCESS, output={})
    return NodeResult(node.id, NodeStatus.SUCCESS, output={"ok": True})


def _ex(ws, runner, **kw):
    kw.setdefault("sleep", lambda s: asyncio.sleep(0))
    return GraphExecutor(runner=runner, workspace=ws,
                         store=GraphStore(workspace=ws), **kw)


def _rows(ws, rid):
    st = GraphStore(workspace=ws)
    return {r["node_id"]: r["status"] for r in st.node_runs(rid)}


def _snapshot_statuses(ws, rid):
    """Durable join truth lives in checkpoint snapshots (join settles write
    memory + events; launched nodes also get rows)."""
    import json as _json
    st = GraphStore(workspace=ws)
    saved = st.latest_checkpoint(rid)
    if saved is None:
        return {}  # failed before first settle: run row is the record
    return _json.loads(saved["state"])["statuses"]


def _assert_honest_terminal(result, ws, rid, expect):
    """§20: API response AND persisted state must agree and be honest."""
    assert result["status"] == expect, result
    st = GraphStore(workspace=ws)
    row = st.get_run(rid)
    assert row is not None
    assert row["status"] == {"succeeded": "succeeded", "failed": "failed",
                             "cancelled": "cancelled"}[expect], row["status"]
    by_node = result["results_by_node"]
    snap = _snapshot_statuses(ws, rid)
    if expect == "succeeded":
        bad = [k for k, v in by_node.items()
               if v["status"] not in ("success", "skipped")
               or (v["output"] or {}).get("timed_out")]
        assert not bad, by_node
        assert "timeout" not in snap.values(), snap
    return by_node


# ── P0: timeout branch records TIMEOUT, never SUCCESS ──

def _timeout_unit(policy=JoinPolicy.ALL):
    """Direct _resolve_join with expired clock + partial branch (unit)."""
    ws = _ws()
    st = GraphStore(workspace=ws)
    ex = GraphExecutor(runner=_ok, workspace=ws, store=st)
    g = _fan(policy, timeout_s=0.2)
    nodes = {n.id: n for n in g.nodes}
    sched = SchedulerState(statuses={"split": NodeStatus.SUCCESS,
                                     "b0": NodeStatus.SUCCESS,
                                     "b1": NodeStatus.RUNNING,
                                     "b2": NodeStatus.PENDING})
    results = {"split": NodeResult("split", NodeStatus.SUCCESS, output={}),
               "b0": NodeResult("b0", NodeStatus.SUCCESS, output={"ok": 1})}
    ex._join_wait["r:j"] = time.time() - 10.0  # deadline long past
    settled = ex._resolve_join(g, "r", st, sched, results, nodes["j"])
    return settled, sched, results, st


def test_p0_timeout_branch_records_timeout_not_success():
    settled, sched, results, _ = _timeout_unit()
    assert settled is True
    assert sched.statuses["j"] == NodeStatus.TIMEOUT, sched.statuses
    res = results["j"]
    assert res.status == NodeStatus.TIMEOUT
    assert res.output["timed_out"] is True
    assert res.output["settled"] == ["b0"]
    assert res.output["unsettled"] == ["b1", "b2"]
    assert res.error_code == "JOIN_TIMEOUT"


def test_timeout_requires_partial_branch():
    """Expired clock + EMPTY branch must not time out (no evidence yet)."""
    ws = _ws()
    st = GraphStore(workspace=ws)
    ex = GraphExecutor(runner=_ok, workspace=ws, store=st)
    g = _fan(JoinPolicy.ALL, timeout_s=0.2)
    nodes = {n.id: n for n in g.nodes}
    sched = SchedulerState(statuses={"split": NodeStatus.SUCCESS})
    results = {"split": NodeResult("split", NodeStatus.SUCCESS, output={})}
    ex._join_wait["r:j"] = time.time() - 10.0
    assert ex._resolve_join(g, "r", st, sched, results, nodes["j"]) is False
    assert "j" not in sched.statuses  # still pending, still honest


def test_timeout_ordering_both_ways():
    """§7 race rule: settled-before-observation satisfies; else timeout wins."""
    # (i) all settled before deadline passes -> policy SUCCESS, no timeout
    ws = _ws()
    st = GraphStore(workspace=ws)
    ex = GraphExecutor(runner=_ok, workspace=ws, store=st)
    g = _fan(JoinPolicy.ALL, timeout_s=60.0)
    nodes = {n.id: n for n in g.nodes}
    sched = SchedulerState(statuses={"split": NodeStatus.SUCCESS,
                                     "b0": NodeStatus.SUCCESS,
                                     "b1": NodeStatus.SUCCESS,
                                     "b2": NodeStatus.SUCCESS})
    results = {k: NodeResult(k, NodeStatus.SUCCESS, output={"ok": 1})
               for k in ("split", "b0", "b1", "b2")}
    assert ex._resolve_join(g, "r", st, sched, results, nodes["j"]) is True
    assert sched.statuses["j"] == NodeStatus.SUCCESS
    assert results["j"].output.get("timed_out") is not True
    # (ii) partial past deadline -> TIMEOUT (test_p0 above)


def test_scheduler_presents_settled_branches():
    """Pin the readiness gating: non-streaming joins are evaluated once,
    all-settled. The timeout branch is therefore defense-in-depth; the
    final predicate (§9) is the live guard."""
    ws = _ws()
    seen = []
    import wisp.graph.executor as _exmod
    orig = _exmod.GraphExecutor._resolve_join

    def spy(self, graph, rid, store, sched, results, node):
        preds = [e.from_node for e in graph.edges
                 if e.to_node == node.id and not e.condition]
        seen.append({p: str(sched.statuses.get(p)) for p in preds})
        return orig(self, graph, rid, store, sched, results, node)

    _exmod.GraphExecutor._resolve_join = spy
    try:
        h = run_graph(_fan(JoinPolicy.ALL), {}, runner=_ok, workspace=ws,
                      run_id="gate")
    finally:
        _exmod.GraphExecutor._resolve_join = orig
    assert h.status() == "succeeded"
    assert len(seen) == 1, seen  # exactly one evaluation ...
    assert set(seen[0].values()) == {"NodeStatus.SUCCESS"}, seen  # ... all-settled


# ── Live branch-outcome matrix (§6 D/E, §13, §14, §15) ──

@pytest.mark.asyncio
async def test_branch_timeout_fails_join_live():
    """Case A/B live analog: branch node TIMEOUT -> ALL join FAILURE."""
    ws = _ws()

    async def _runner(node, inputs):
        if node.id == "split":
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})
        if node.id == "b0":
            await asyncio.sleep(30)  # contract timeout converts to TIMEOUT
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})
        return NodeResult(node.id, NodeStatus.SUCCESS, output={})

    nodes = [GraphNode(id="split"),
             GraphNode(id="b0", contract=NodeContract(id="b0", name="b0",
                                                      timeout_s=0.1)),
             GraphNode(id="b1"),
             GraphNode(id="j", type=NodeType.JOIN, join_policy=JoinPolicy.ALL)]
    g = Graph(id="t", version="1", entrypoint="split", nodes=nodes,
              edges=[EdgeMapping("split", "b0", reason="f"),
                     EdgeMapping("split", "b1", reason="f"),
                     EdgeMapping("b0", "j", reason="j"),
                     EdgeMapping("b1", "j", reason="j")])
    ex = _ex(ws, _runner)
    r = await ex.run(g, {})
    by_node = _assert_honest_terminal(r, ws, r["run_id"], "failed")
    assert by_node["b0"]["status"] == "timeout", by_node
    assert by_node["j"]["status"] == "failure", by_node


@pytest.mark.asyncio
async def test_branch_failure_fails_all_join_live():
    """Case D: SUCCESS/FAILURE/SUCCESS + ALL -> join FAILURE -> run FAILED."""
    ws = _ws()

    async def _runner(node, inputs):
        if node.id == "b1":
            return NodeResult(node.id, NodeStatus.FAILURE, error_code="X",
                              message="boom")
        return await _ok(node, inputs)

    ex = _ex(ws, _runner)
    r = await ex.run(_fan(JoinPolicy.ALL), {})
    by_node = _assert_honest_terminal(r, ws, r["run_id"], "failed")
    assert by_node["j"]["status"] == "failure", by_node


@pytest.mark.asyncio
async def test_all_branches_complete_before_deadline_succeeds():
    """Case E: no over-aggressive timeout — healthy fan succeeds."""
    ws = _ws()
    ex = _ex(ws, _ok)
    r = await ex.run(_fan(JoinPolicy.ALL, timeout_s=60.0), {})
    by_node = _assert_honest_terminal(r, ws, r["run_id"], "succeeded")
    assert by_node["j"]["status"] == "success", by_node


@pytest.mark.asyncio
async def test_any_first_failure_releases_explicit_partial():
    """ANY = first SETTLED releases (defined partial semantics, preserved).
    The join output records the failure explicitly — not silent."""
    ws = _ws()

    async def _runner(node, inputs):
        if node.id == "b0":
            return NodeResult(node.id, NodeStatus.FAILURE, error_code="X",
                              message="boom")
        return await _ok(node, inputs)

    ex = _ex(ws, _runner)
    r = await ex.run(_fan(JoinPolicy.ANY), {})
    assert r["status"] == "succeeded", r  # licensed partiality, pinned
    out = r["results_by_node"]["j"]["output"]
    assert out.get("failures"), out  # failure evidence preserved


@pytest.mark.asyncio
async def test_empty_join_fails_explicit():
    """§12: join with zero branches -> FAILURE JOIN_UNSATISFIED (resolved:
    validator allows it; executor refuses it deterministically)."""
    ws = _ws()
    g = Graph(id="e", version="1", entrypoint="j",
              nodes=[GraphNode(id="j", type=NodeType.JOIN,
                               join_policy=JoinPolicy.ALL)])
    ex = _ex(ws, _ok)
    r = await ex.run(g, {})
    by_node = _assert_honest_terminal(r, ws, r["run_id"], "failed")
    assert by_node.get("j", {}).get("status") == "failure", by_node


@pytest.mark.asyncio
async def test_router_untaken_lane_skips_benignly():
    """§13/§16: untaken conditional lane skips; run succeeds honestly."""
    ws = _ws()

    async def _rr(node, inputs):
        if node.id == "route":
            return NodeResult(node.id, NodeStatus.SUCCESS,
                              output={"label": "left"})
        return await _ok(node, inputs)

    g = Graph(id="t", version="1", entrypoint="route",
              nodes=[GraphNode(id="route", type=NodeType.ROUTER,
                               routes={"left": "L", "right": "R"},
                               default_route="R"),
                     GraphNode(id="L"), GraphNode(id="R"),
                     GraphNode(id="j", type=NodeType.JOIN,
                               join_policy=JoinPolicy.ALL)],
              edges=[EdgeMapping("route", "L", reason="lane", condition="left"),
                     EdgeMapping("route", "R", reason="lane", condition="right"),
                     EdgeMapping("L", "j", reason="lj"),
                     EdgeMapping("R", "j", reason="jr")])
    ex = _ex(ws, _rr)
    r = await ex.run(g, {})
    by_node = _assert_honest_terminal(r, ws, r["run_id"], "succeeded")
    assert by_node["R"]["status"] == "skipped", by_node


# ── Resume preserves truth; final guard bites stale rows (§9, §17) ──

@pytest.mark.asyncio
async def test_resume_after_branch_timeout_reruns_honestly():
    """Timeout run -> resume -> unsettled work re-runs, terminal stays honest."""
    ws = _ws()

    async def _runner(node, inputs):
        if node.id == "b0":
            await asyncio.sleep(30)
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})
        return await _ok(node, inputs)

    nodes = [GraphNode(id="split"),
             GraphNode(id="b0", contract=NodeContract(id="b0", name="b0",
                                                      timeout_s=0.1)),
             GraphNode(id="b1"),
             GraphNode(id="j", type=NodeType.JOIN, join_policy=JoinPolicy.ALL)]
    g = Graph(id="t", version="1", entrypoint="split", nodes=nodes,
              edges=[EdgeMapping("split", "b0", reason="f"),
                     EdgeMapping("split", "b1", reason="f"),
                     EdgeMapping("b0", "j", reason="j"),
                     EdgeMapping("b1", "j", reason="j")])
    ex = _ex(ws, _runner)
    r1 = await ex.run(g, {})
    assert r1["status"] == "failed", r1
    rid = r1["run_id"]
    # resume with a healthy runner: timeout branch re-runs, run converges
    ex2 = _ex(ws, _ok)
    r2 = await ex2.resume(g, rid)
    assert r2["status"] == "succeeded", r2
    st = GraphStore(workspace=ws)
    assert st.get_run(rid)["status"] == "succeeded"


@pytest.mark.asyncio
async def test_stale_success_timed_out_row_cannot_finalize():
    """§9 guard end-to-end: a SUCCESS-status join row carrying timed_out
    evidence (pre-fix shape) must finalize FAILED on resume, never success."""
    ws = _ws()
    g = _fan(JoinPolicy.ALL)
    ex = _ex(ws, _ok)
    r1 = await ex.run(g, {})
    assert r1["status"] == "succeeded"
    rid = r1["run_id"]
    st = GraphStore(workspace=ws)
    # forge the pre-fix shape: SUCCESS status + timed_out output on sink j
    forged = {"status": "success",
              "output": {"branches": ["b0"], "successes": ["b0"],
                         "failures": {}, "timed_out": True},
              "artifacts": [], "model": "", "provider": "",
              "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
              "duration_s": 0.0, "attempt": 1, "error_code": "",
              "message": ""}
    nrid = None
    for row in st.node_runs(rid):
        if row["node_id"] == "j":
            nrid = row["node_run_id"]
    if nrid is None:
        # joins write no node rows: insert the forged row directly
        nrid = f"{rid}:j#1"
        st.put_node_run(nrid, rid, "j", "success", 1, "", forged,
                        f"{rid}:j", 0.0, 0.0)
    else:
        st.put_node_run(nrid, rid, "j", "success", 1, "", forged,
                        f"{rid}:j", 0.0, 0.0)
    st.set_run_status(rid, "running")  # simulate pre-final crash
    ex2 = _ex(ws, _ok)
    r2 = await ex2.resume(g, rid)
    assert r2["status"] == "failed", r2
    assert st.get_run(rid)["status"] == "failed"


# ── Monotonicity + no resurrection (§5, §21) ──

@pytest.mark.asyncio
async def test_cancel_refuses_to_overwrite_terminal():
    """cancel() never rewrites succeeded/failed/cancelled rows (existing
    guard, pinned as the monotonicity precedent)."""
    ws = _ws()
    ex = _ex(ws, _ok)
    r = await ex.run(_fan(JoinPolicy.ALL), {})
    assert r["status"] == "succeeded"
    rid = r["run_id"]
    ex.cancel(rid)
    st = GraphStore(workspace=ws)
    assert st.get_run(rid)["status"] == "succeeded"


@pytest.mark.asyncio
async def test_late_rows_do_not_rewrite_terminal():
    """Generic invariant: for a terminal run, injected late branch rows
    change neither the run row nor a subsequent resume into success."""
    ws = _ws()
    ex = _ex(ws, _ok)
    r = await ex.run(_fan(JoinPolicy.ALL), {})
    rid = r["run_id"]
    assert r["status"] == "succeeded"
    st = GraphStore(workspace=ws)
    # inject every late outcome shape for a hypothetical unsettled branch
    for i, status in enumerate(("success", "failure", "timeout", "cancelled")):
        st.put_node_run(f"{rid}:late#{i}", rid, "late", status, 1, "",
                        {"status": status, "output": {}}, f"{rid}:late", 0.0, 0.0)
    assert st.get_run(rid)["status"] == "succeeded"  # untouched by rows
    ex2 = _ex(ws, _ok)
    r2 = await ex2.resume(Graph(id="t", version="1", entrypoint="split",
                                nodes=[GraphNode(id="split"),
                                       GraphNode(id="b0"), GraphNode(id="b1"),
                                       GraphNode(id="b2"),
                                       GraphNode(id="j", type=NodeType.JOIN,
                                                 join_policy=JoinPolicy.ALL)],
                                edges=[EdgeMapping("split", "b0", reason="f"),
                                       EdgeMapping("split", "b1", reason="f"),
                                       EdgeMapping("split", "b2", reason="f"),
                                       EdgeMapping("b0", "j", reason="j"),
                                       EdgeMapping("b1", "j", reason="j"),
                                       EdgeMapping("b2", "j", reason="j")]), rid)
    # foreign rows ignored; restored successes converge back to succeeded
    assert r2["status"] == "succeeded", r2

# ── Race harness: bulk honest-terminal iterations (§19) ──

@pytest.mark.asyncio
async def test_bulk_fanout_terminals_stay_honest():
    """200 fanouts with varied interleavings: every terminal honest.
    Delays shape scheduling only; assertions are state predicates."""
    import random as _random
    rng = _random.Random(13013)
    for i in range(200):
        ws = _ws()
        delays = {f"b{j}": rng.choice((0.0, 0.0, 0.001, 0.005)) for j in range(4)}
        fails = {f"b{j}" for j in range(4) if rng.random() < 0.1}

        async def _runner(node, inputs, _d=delays, _f=fails):
            if node.id == "split" or node.id not in _d:
                return NodeResult(node.id, NodeStatus.SUCCESS, output={})
            d = _d[node.id]
            if d:
                await asyncio.sleep(d)
            if node.id in _f:
                return NodeResult(node.id, NodeStatus.FAILURE,
                                  error_code="X", message="bulk")
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})

        branches = [GraphNode(id=f"b{j}") for j in range(4)]
        g = Graph(id="t", version="1", entrypoint="split",
                  nodes=[GraphNode(id="split"), *branches,
                         GraphNode(id="j", type=NodeType.JOIN,
                                   join_policy=JoinPolicy.ALL)],
                  edges=[EdgeMapping("split", b.id, reason="f") for b in branches]
                  + [EdgeMapping(b.id, "j", reason="j") for b in branches])
        ex = _ex(ws, _runner)
        r = await ex.run(g, {})
        by_node = r["results_by_node"]
        if fails:
            assert r["status"] == "failed", (i, r["status"])
        else:
            assert r["status"] == "succeeded", (i, r["status"])
            assert not [k for k, v in by_node.items()
                        if (v["output"] or {}).get("timed_out")], (i, by_node)


@pytest.mark.asyncio
async def test_bulk_cancel_orderings():
    """25 iterations per cancel/completion ordering, event-orchestrated."""
    for i in range(25):
        ws = _ws()
        gate = asyncio.Event()
        proceed = asyncio.Event()
        cancel_first = (i % 2 == 0)

        async def _runner(node, inputs):
            if node.id == "b0":
                gate.set()
                await proceed.wait()
                return NodeResult(node.id, NodeStatus.SUCCESS, output={})
            return await _ok(node, inputs)

        ex = _ex(ws, _runner)
        t = asyncio.ensure_future(ex.run(_fan(JoinPolicy.ALL), {}))
        await asyncio.wait_for(gate.wait(), 10)
        st = GraphStore(workspace=ws)
        rid = None
        for _ in range(100):
            runs = st.list_runs(graph_id="t")
            if runs:
                rid = runs[0]["run_id"]
                break
            await asyncio.sleep(0.01)
        assert rid is not None
        if cancel_first:
            ex.cancel(rid)
            proceed.set()
            r = await t
            assert r["status"] == "cancelled", (i, r["status"])
        else:
            proceed.set()
            r = await t
            assert r["status"] == "succeeded", (i, r["status"])
        assert st.get_run(rid)["status"] == r["status"]

# ── Cancel race (§18; P1-2 retry itself deferred per §22) ──

@pytest.mark.asyncio
async def test_cancel_with_branch_running_stays_cancelled():
    ws = _ws()
    started = asyncio.Event()

    async def _runner(node, inputs):
        if node.id == "b0":
            started.set()
            await asyncio.sleep(30)
            return NodeResult(node.id, NodeStatus.SUCCESS, output={})
        return await _ok(node, inputs)

    ex = _ex(ws, _runner)
    task = asyncio.ensure_future(ex.run(_fan(JoinPolicy.ALL), {}))
    await asyncio.wait_for(started.wait(), 10)
    st = GraphStore(workspace=ws)
    rid = None
    for _ in range(100):
        runs = st.list_runs(graph_id="t")
        if runs:
            rid = runs[0]["run_id"]
            break
        await asyncio.sleep(0.01)
    assert rid is not None
    ex.cancel(rid)
    r = await task
    assert r["status"] == "cancelled", r
    assert st.get_run(rid)["status"] == "cancelled"
    by_node = r.get("results_by_node", {})
    assert by_node.get("j", {}).get("status") != "success", by_node


@pytest.mark.asyncio
async def test_cancel_race_both_orderings():
    """Completion-vs-cancel, both orders, event-orchestrated (no sleeps
    as correctness mechanism — events decide)."""
    for order in ("cancel-first", "complete-first"):
        ws = _ws()
        gate = asyncio.Event()
        proceed = asyncio.Event()

        async def _runner(node, inputs):
            if node.id == "b0":
                gate.set()
                await proceed.wait()
                return NodeResult(node.id, NodeStatus.SUCCESS, output={})
            return await _ok(node, inputs)

        ex = _ex(ws, _runner)
        holds = {}

        orig_run = ex.run

        async def _run(g, inputs, **kw):
            t = asyncio.ensure_future(orig_run(g, inputs, **kw))
            holds["task"] = t
            return await t

        ex.run = _run  # type: ignore[assignment]
        g = _fan(JoinPolicy.ALL)
        t = asyncio.ensure_future(ex.run(g, {}))
        await asyncio.wait_for(gate.wait(), 10)
        # discover rid from the store
        st = GraphStore(workspace=ws)
        rid = None
        for _ in range(100):
            runs = st.list_runs(graph_id="t")
            if runs:
                rid = runs[0]["run_id"]
                break
            await asyncio.sleep(0.01)
        assert rid is not None
        if order == "cancel-first":
            ex.cancel(rid)
            proceed.set()
            r = await t
            assert r["status"] == "cancelled", (order, r["status"])
        else:
            proceed.set()
            r = await t
            assert r["status"] == "succeeded", (order, r["status"])
