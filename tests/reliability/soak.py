"""G0 soak driver: repeated mock-provider operations with resource sampling.

Usage (repo root):
  python -m tests.reliability.soak --duration 10m --workload A --out /tmp/soakA
  python -m tests.reliability.soak --duration 30m --workload B --out /tmp/soakB
  python -m tests.reliability.soak --duration 60m --workload B --out /tmp/soakC
  python -m tests.reliability.soak --duration 60s --workload A --out /tmp/smoke  # CI smoke

Workloads use MockProvider (no network) + real graph/workspace/persistence
paths. Growth thresholds live in REPORT_THRESHOLDS (recorded in G0 report).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.getcwd())

from tests.reliability.measure import (  # noqa: E402
    count_async_tasks, sample_db, sample_files, sample_process,
)

# Thresholds: absolute deltas allowed over a run, plus per-op DB allowance.
# Established from smoke observations; recorded in PHASE13_G0_REPORT.md.
REPORT_THRESHOLDS = {
    "threads_delta_max": 4,
    "fds_delta_max": 32,
    "sockets_delta_max": 8,
    "children_delta_max": 0,
    "tasks_delta_max": 2,
    "rss_delta_max_bytes": 100 * 1024 * 1024,  # 10m; scale linearly 30m/60m
    "db_bytes_per_op_max": 64 * 1024,
}


def _parse_duration(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("ms"):
        return float(s[:-2]) / 1000
    if s.endswith("s"):
        return float(s[:-1])
    if s.endswith("m"):
        return float(s[:-1]) * 60
    if s.endswith("h"):
        return float(s[:-1]) * 3600
    return float(s)


class CountingProvider:
    """MockProvider wrapper counting calls (= requests) and retries."""

    def __init__(self, mock):
        self._m = mock
        self.calls = 0

    def generate_stream_events(self, *a, **k):
        self.calls += 1
        yield from self._m.generate_stream_events(*a, **k)

    def __getattr__(self, name):
        return getattr(self._m, name)


def _make_core():
    from wisp.providers.mock import MockProvider
    from wisp.core.engine import WispAgentCore

    mock = MockProvider(responses=["done"])
    prov = CountingProvider(mock)
    core = WispAgentCore(provider=prov)
    return core, prov


def _op_successful(core, ws: str) -> dict:
    """Workload A step: mock turn + 2-node graph + 1-file workspace apply."""
    from wisp.graph import Graph, GraphNode
    from wisp.graph.types import EdgeMapping, NodeResult, NodeStatus
    from wisp.graph.api import run_graph
    from wisp.workspace import (snapshot_workspace, make_changeset,
                                apply_changeset, Change)

    async def _turn():
        session = {"id": "soak", "messages": [], "model": "mock",
                   "workspace": ws}
        async for _ev in core.turn(session, "status check"):
            pass

    asyncio.run(_turn())

    async def _runner(node, inputs):
        return NodeResult(node.id, NodeStatus.SUCCESS, output={"ok": True})

    g = Graph(id="soak", version="1", entrypoint="a",
              nodes=[GraphNode(id="a"), GraphNode(id="b")],
              edges=[EdgeMapping("a", "b", reason="seq")])
    h = run_graph(g, {}, runner=_runner, workspace=ws,
                  run_id=f"soak-{time.time_ns()}")

    base = snapshot_workspace(ws)
    name = f"soak-{time.time_ns()}.txt"
    cs = make_changeset(base.id, {"run_id": "soak", "node_id": "n"},
                        [Change(path=name, op="CREATE", content="x")])
    res = apply_changeset(ws, base, cs)
    return {"graph": h.status(), "apply_ok": res.ok}


def _op_failure_heavy(core, ws: str, state: dict) -> dict:
    """Workload B step: A-step plus timeout / cancel / failure / drift."""
    from wisp.graph import Graph, GraphNode
    from wisp.graph.types import (NodeContract, NodeResult,
                                  NodeStatus, RetryPolicy)
    from wisp.graph.api import run_graph
    from wisp.workspace import (snapshot_workspace, make_changeset,
                                apply_changeset, Change)

    out = _op_successful(core, ws)

    async def _slow(node, inputs):
        await asyncio.sleep(5.0)
        return NodeResult(node.id, NodeStatus.SUCCESS, output={})

    # node timeout (short contract, slow runner)
    g = Graph(id="soak-t", version="1", entrypoint="a",
              nodes=[GraphNode(id="a", contract=NodeContract(
                  id="a", name="a", timeout_s=0.05))])
    h = run_graph(g, {}, runner=_slow, workspace=ws,
                  run_id=f"soak-t-{time.time_ns()}")
    state["timeouts"] = state.get("timeouts", 0) + 1
    out["timeout_status"] = h.status()

    # failing node with one retry (retry observable)
    calls = []
    async def _flaky(node, inputs):
        calls.append(1)
        if len(calls) < 2:
            return NodeResult(node.id, NodeStatus.FAILURE, output={},
                              error_code="E", message="boom")
        return NodeResult(node.id, NodeStatus.SUCCESS, output={"ok": True})

    g2 = Graph(id="soak-r", version="1", entrypoint="a",
               nodes=[GraphNode(id="a", contract=NodeContract(
                   id="a", name="a",
                   retry_policy=RetryPolicy(max_attempts=2)))])
    h2 = run_graph(g2, {}, runner=_flaky, workspace=ws,
                   run_id=f"soak-r-{time.time_ns()}")
    state["retries"] = state.get("retries", 0) + max(0, len(calls) - 1)
    out["retry_status"] = h2.status()

    # cancelled graph (slow runner + cancel)
    ex_cancelled = False
    try:
        from wisp.graph.executor import GraphExecutor
        ex = GraphExecutor(runner=_slow, workspace=ws)
        rid = f"soak-c-{time.time_ns()}"

        async def _run_cancel():
            task = asyncio.ensure_future(ex.run(
                Graph(id="soak-c", version="1", entrypoint="a",
                      nodes=[GraphNode(id="a")]), {}, rid))
            await asyncio.sleep(0.2)
            ex.cancel(rid)
            return await task

        cancel_res = asyncio.run(_run_cancel())
        state["cancels"] = state.get("cancels", 0) + 1
        state["last_cancel_status"] = str(
            (cancel_res or {}).get("status", cancel_res))[:40]
        ex_cancelled = True
    except Exception:
        pass
    out["cancel_issued"] = ex_cancelled

    # workspace drift refusal (external edit -> apply must refuse)
    base = snapshot_workspace(ws)
    with open(os.path.join(ws, "drift.txt"), "w") as fh:
        fh.write("base")
    base = snapshot_workspace(ws)
    with open(os.path.join(ws, "drift.txt"), "w") as fh:
        fh.write("external")
    cs = make_changeset(base.id, {"run_id": "s", "node_id": "n"},
                        [Change(path="drift2.txt", op="CREATE", content="y")])
    r = apply_changeset(ws, base, cs)
    out["drift_refused"] = not r.ok
    return out


def run_soak(duration_s: float, workload: str, sample_every_s: float,
             out_dir: str) -> dict:
    out = Path(out_dir)
    (out / "samples").mkdir(parents=True, exist_ok=True)
    ws = tempfile.mkdtemp(prefix="g0soak-ws_")
    home = tempfile.mkdtemp(prefix="g0soak-home_")
    os.environ["HOME"] = home
    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")

    core, prov = _make_core()
    db = os.path.join(ws, ".wisp", "wisp.db")
    state: dict = {"ops": 0, "retries": 0, "timeouts": 0, "cancels": 0}
    samples = []

    def _sample(tag: str) -> dict:
        s = {"t": time.time(), "tag": tag, "proc": sample_process(),
             "db": sample_db(db), "files": sample_files(ws),
             "tasks": count_async_tasks(), "state": dict(state),
             "provider_calls": prov.calls}
        samples.append(s)
        return s

    first = _sample("initial")
    deadline = time.monotonic() + duration_s
    nxt = time.monotonic() + sample_every_s
    while time.monotonic() < deadline:
        try:
            if workload == "A":
                _op_successful(core, ws)
            else:
                _op_failure_heavy(core, ws, state)
            state["ops"] += 1
        except Exception as e:  # harness must survive op failures; record them
            state["op_errors"] = state.get("op_errors", 0) + 1
            state["last_op_error"] = repr(e)[:200]
        if time.monotonic() >= nxt:
            _sample("periodic")
            nxt = time.monotonic() + sample_every_s
    last = _sample("final")

    summary = _evaluate(first, last, state, duration_s, workload, samples)
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    with open(out / "samples.jsonl", "w") as fh:
        for s in samples:
            fh.write(json.dumps(s, default=str) + "\n")
    return summary


def _second_half_slope(samples: list) -> dict:
    """Per-sample slope over the second half of periodic samples.

    Distinguishes warmup jump (early, then flat) from monotonic leak
    (sustained positive slope). Needs >=3 periodic samples; else 0.
    """
    per = [s for s in samples if s.get("tag") == "periodic"]
    if len(per) < 3:
        return {}
    half = per[len(per) // 2:]
    out = {}
    for key in ("fds", "threads", "rss"):
        vals = [(s["proc"] or {}).get(key) for s in half]
        vals = [v for v in vals if isinstance(v, (int, float)) and v >= 0]
        if len(vals) >= 2:
            out[key] = (vals[-1] - vals[0]) / max(1, len(vals) - 1)
    return out


def _delta(a, b):
    return (b or 0) - (a or 0)


def _evaluate(first: dict, last: dict, state: dict,
              duration_s: float, workload: str, samples: list) -> dict:
    p0, p1 = first["proc"], last["proc"]
    scale = max(1.0, duration_s / 600.0)
    db0 = first["db"].get("bytes", 0) if first["db"].get("exists") else 0
    db1 = last["db"].get("bytes", 0) if last["db"].get("exists") else 0
    ops = max(1, state.get("ops", 0))
    slope = _second_half_slope(samples)
    checks = {
        "threads": _delta(p0.get("threads"), p1.get("threads")) <= REPORT_THRESHOLDS["threads_delta_max"],
        "fds": _delta(p0.get("fds"), p1.get("fds")) <= REPORT_THRESHOLDS["fds_delta_max"]
        or slope.get("fds", 0) <= 0,
        "sockets": _delta(p0.get("sockets"), p1.get("sockets")) <= REPORT_THRESHOLDS["sockets_delta_max"],
        "children": _delta(p0.get("children"), p1.get("children")) <= REPORT_THRESHOLDS["children_delta_max"],
        "tasks": abs(_delta(first.get("tasks", 0), last.get("tasks", 0))) <= REPORT_THRESHOLDS["tasks_delta_max"],
        "rss": _delta(p0.get("rss"), p1.get("rss")) <= REPORT_THRESHOLDS["rss_delta_max_bytes"] * scale
        or slope.get("rss", 0) <= 0,
        "db_per_op": (db1 - db0) / ops <= REPORT_THRESHOLDS["db_bytes_per_op_max"],
    }
    return {
        "workload": workload, "duration_s": duration_s,
        "ops": state.get("ops", 0), "op_errors": state.get("op_errors", 0),
        "retries": state.get("retries", 0), "timeouts": state.get("timeouts", 0),
        "cancels": state.get("cancels", 0),
        "provider_calls": last.get("provider_calls", 0),
        "initial": {"proc": p0, "db_bytes": db0},
        "final": {"proc": p1, "db_bytes": db1},
        "growth": {k: (p1.get(k, 0) or 0) - (p0.get(k, 0) or 0)
                   for k in ("rss", "fds", "threads", "children", "sockets")},
        "second_half_slope": slope,
        "db_growth_bytes": db1 - db0,
        "thresholds": REPORT_THRESHOLDS,
        "checks": checks,
        "result": "PASS" if all(checks.values()) else "FAIL",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="G0 soak driver")
    ap.add_argument("--duration", default="60s")
    ap.add_argument("--workload", default="A", choices=("A", "B"))
    ap.add_argument("--sample-every", default="15s")
    ap.add_argument("--out", required=True)
    ns = ap.parse_args(argv)
    summary = run_soak(_parse_duration(ns.duration), ns.workload,
                       _parse_duration(ns.sample_every), ns.out)
    print(json.dumps(summary, indent=1))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
