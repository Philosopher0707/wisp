"""Typed SDK for graph execution: run / wait / status / cancel / resume / trace."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from wisp.graph.executor import GraphExecutor, NodeRunner
from wisp.graph.store import GraphStore
from wisp.graph.trace import quality_metrics, render_ascii
from wisp.graph.types import Graph


class GraphHandle:
    def __init__(self, executor: GraphExecutor, run_id: str,
                 result: dict[str, Any] | None = None) -> None:
        self._ex = executor
        self.run_id = run_id
        self._result = result

    def wait(self) -> dict[str, Any]:
        """Return the completed result. Raises if the run paused for approval
        or was cancelled — callers MUST check ``status``; a paused dict is
        not a completion."""
        if self._result is None:
            raise RuntimeError("run has not completed (paused or cancelled?)")
        if self._result.get("status") not in ("succeeded", "failed", "cancelled"):
            raise RuntimeError(f"run is {self._result.get('status')}: "
                               f"{self._result.get('error', '')} "
                               "(resume to continue)")
        return self._result

    def status(self) -> str:
        if self._result is not None:
            return self._result["status"]
        store = self._ex._store or GraphStore(workspace=self._ex.workspace)
        row = store.get_run(self.run_id)
        return row["status"] if row else "unknown"

    def cancel(self) -> None:
        self._ex.cancel(self.run_id)

    def resume(self, graph: Graph, approvals: dict[str, bool] | None = None) -> "GraphHandle":
        result = _run_async(self._ex.resume(graph, self.run_id, approvals))
        return GraphHandle(self._ex, self.run_id, result)

    def trace(self) -> str:
        if self._result is None:
            return f"run {self.run_id}: no result yet"
        return render_ascii(self._result)

    def metrics(self) -> dict[str, Any]:
        return quality_metrics(self._result or {})


def run_graph(graph: Graph, inputs: dict[str, Any], runner: NodeRunner | None = None,
              workspace: str = ".", max_concurrency: int = 8,
              on_event: Callable[[dict], None] | None = None,
              run_id: str = "") -> GraphHandle:
    from wisp.graph.validator import validate_graph
    errors = validate_graph(graph)
    if errors:
        raise ValueError("invalid graph: " + "; ".join(errors[:5]))
    if not isinstance(inputs, dict):
        raise ValueError("inputs must be a mapping")
    try:
        max_concurrency = max(1, min(int(max_concurrency), 32))
    except (TypeError, ValueError):
        max_concurrency = 8
    ex = GraphExecutor(runner=runner, workspace=workspace,
                       max_concurrency=max_concurrency,
                       emit=on_event or (lambda e: None))
    result = _run_async(ex.run(graph, inputs, run_id))
    return GraphHandle(ex, result["run_id"], result)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures as _fut
    with _fut.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
