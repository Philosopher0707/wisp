"""Graph executor: per-run bookkeeping is reclaimed at terminal.

Context (see PHASE_BOUNDARY_FORENSIC.md / F10):

`GraphExecutor` keys four structures by run id:

  `_cancelled`         set[str]                 added on cancel, discarded only on resume
  `_approvals`         dict[str, dict[str, bool]] set on resume, read per node
  `_join_wait`         dict[str, float]         popped on release/timeout
  `_join_wait_reason`  dict[str, str]           never popped

Only `_join_wait` reclaimed itself. A long-lived executor therefore
accumulated an entry for every run it had ever executed — a slow leak in a
process that serves many runs. `_forget_run()` now clears all four when the
run reaches a terminal status.
"""

from __future__ import annotations

import pytest

from wisp.graph.executor import GraphExecutor
from wisp.graph.store import GraphStore
from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    NodeContract,
    NodeResult,
    NodeStatus,
    NodeType,
)


def _agent(nid: str) -> GraphNode:
    return GraphNode(id=nid, type=NodeType.AGENT,
                     contract=NodeContract(id=nid, name=nid))


def _chain(*names: str) -> Graph:
    nodes = tuple(_agent(n) for n in names)
    edges = tuple(EdgeMapping(a, b, reason=f"{b} consumes {a}.output")
                  for a, b in zip(names, names[1:]))
    return Graph(id="t", version="1", entrypoint=names[0], nodes=nodes, edges=edges)


def _runner(fail: set[str] | None = None):
    async def run(node, inputs):
        if fail and node.id in fail:
            return NodeResult(node.id, NodeStatus.FAILURE, error_code="BOOM",
                              message="injected failure")
        return NodeResult(node.id, NodeStatus.SUCCESS, output={"node": node.id})
    return run


def _ex(tmp_path, runner) -> GraphExecutor:
    ws = str(tmp_path)

    async def _no_sleep(_: float) -> None:
        return None

    return GraphExecutor(runner=runner, workspace=ws,
                         store=GraphStore(workspace=ws), sleep=_no_sleep)


def _residue(ex: GraphExecutor, run_id: str) -> dict[str, object]:
    """Any per-run bookkeeping still held for `run_id`."""
    prefix = f"{run_id}:"
    return {
        "_cancelled": run_id in ex._cancelled,
        "_approvals": run_id in ex._approvals,
        "_join_wait": [k for k in ex._join_wait if k.startswith(prefix)],
        "_join_wait_reason": [k for k in ex._join_wait_reason if k.startswith(prefix)],
    }


# ── A successful run leaves nothing behind ───────────────────────────

@pytest.mark.asyncio
async def test_successful_run_leaves_no_bookkeeping(tmp_path):
    ex = _ex(tmp_path, _runner())
    result = await ex.run(_chain("a", "b", "c"), {}, run_id="run-ok")
    assert result["status"] == "succeeded"
    assert _residue(ex, "run-ok") == {
        "_cancelled": False, "_approvals": False,
        "_join_wait": [], "_join_wait_reason": [],
    }


@pytest.mark.asyncio
async def test_failed_run_leaves_no_bookkeeping(tmp_path):
    ex = _ex(tmp_path, _runner(fail={"b"}))
    result = await ex.run(_chain("a", "b"), {}, run_id="run-fail")
    assert result["status"] == "failed"
    assert _residue(ex, "run-fail")["_cancelled"] is False
    assert _residue(ex, "run-fail")["_approvals"] is False


# ── Many runs do not accumulate ──────────────────────────────────────

@pytest.mark.asyncio
async def test_many_runs_do_not_accumulate_state(tmp_path):
    ex = _ex(tmp_path, _runner())
    for i in range(25):
        result = await ex.run(_chain("a", "b"), {}, run_id=f"run-{i}")
        assert result["status"] == "succeeded"

    leaked = sorted(
        set(ex._cancelled)
        | set(ex._approvals)
        | {k.split(":")[0] for k in ex._join_wait}
        | {k.split(":")[0] for k in ex._join_wait_reason}
    )
    assert leaked == [], f"per-run state accumulated for {leaked}"


@pytest.mark.asyncio
async def test_join_wait_reason_is_reclaimed(tmp_path):
    """`_join_wait_reason` was the one structure never popped."""
    ex = _ex(tmp_path, _runner())
    await ex.run(_chain("a", "b"), {}, run_id="run-j")
    assert not [k for k in ex._join_wait_reason if k.startswith("run-j:")]


# ── Cancellation path ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancelled_run_is_reclaimed_after_terminal(tmp_path):
    ex = _ex(tmp_path, _runner())
    await ex.run(_chain("a", "b"), {}, run_id="run-c")
    # Simulate a cancel that arrived after the run finished.
    ex.cancel("run-c")
    assert "run-c" in ex._cancelled          # cancel records it
    await ex.run(_chain("a", "b"), {}, run_id="run-c2")
    ex._forget_run("run-c")
    assert "run-c" not in ex._cancelled


# ── The helper is safe to call repeatedly ────────────────────────────

def test_forget_run_is_idempotent(tmp_path):
    ex = _ex(tmp_path, _runner())
    for _ in range(3):
        ex._forget_run("never-existed")
    ex._cancelled.add("r")
    ex._approvals["r"] = {"n": True}
    ex._join_wait["r:n"] = 1.0
    ex._join_wait_reason["r:n"] = "waiting"
    ex._forget_run("r")
    assert _residue(ex, "r") == {
        "_cancelled": False, "_approvals": False,
        "_join_wait": [], "_join_wait_reason": [],
    }
    ex._forget_run("r")   # again: no error


def test_forget_run_only_touches_its_own_run(tmp_path):
    ex = _ex(tmp_path, _runner())
    ex._approvals["keep"] = {"n": True}
    ex._join_wait["keep:n"] = 1.0
    ex._forget_run("other")
    assert ex._approvals == {"keep": {"n": True}}
    assert ex._join_wait == {"keep:n": 1.0}
