"""Shared fixtures for graph security tests."""

from __future__ import annotations



from wisp.graph.executor import GraphExecutor
from wisp.graph.store import GraphStore
from wisp.graph.types import NodeResult, NodeStatus


async def _no_sleep(_: float) -> None:
    return None


def make_executor(tmp_path, runner, **kw):
    ws = str(tmp_path)
    kw.setdefault("sleep", _no_sleep)
    return GraphExecutor(runner=runner, workspace=ws,
                         store=GraphStore(workspace=ws), **kw)


def ok_runner(outputs: dict | None = None, fail: set | None = None, calls: list | None = None):
    async def run(node, inputs):
        if calls is not None:
            calls.append(node.id)
        if fail and node.id in fail:
            return NodeResult(node.id, NodeStatus.FAILURE, error_code="X", message="x")
        return NodeResult(node.id, NodeStatus.SUCCESS,
                          output=dict((outputs or {}).get(node.id, {"ok": True})))
    return run
