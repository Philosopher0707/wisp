"""Default node runner — executes AGENT nodes via SubagentOrchestrator.

Node -> ModelPolicy -> ProviderRuntime. Never provider-specific code here:
the contract becomes a SubagentContract and the orchestrator (with the
configured provider factory) does the rest. Each branch gets its own
contract, context slice, tool permissions, retry budget, and trace.

Trust note: node inputs may carry upstream model output (prompt-injection
surface). Inputs are size-capped and passed as *data* (a [Node input]
block), never as authority: tool visibility is filtered downstream by the
agent loop against the contract's allowed_tools, and every tool call still
passes ToolExecutor.authorize().
"""

from __future__ import annotations

import math
from typing import Any

from wisp.graph.security import scrub_text as redact
from wisp.graph.types import GraphNode, NodeResult, NodeStatus

MAX_TASK_CHARS = 8000


class SubagentNodeRunner:
    """Callable runner backed by a SubagentOrchestrator (or compatible)."""

    def __init__(self, orchestrator: Any, workspace: str = ".") -> None:
        self._orch = orchestrator
        self._workspace = workspace

    async def __call__(self, node: GraphNode, inputs: dict[str, Any]) -> NodeResult:
        from wisp.multi_agent.task import SubagentContract
        contract = node.effective_contract()
        task_text = str(inputs.get("task", node.config.get("prompt", node.contract.description
                                   if node.contract else node.id)))[:MAX_TASK_CHARS]
        try:
            max_iter = int(node.config.get("max_iterations", 15))
        except (TypeError, ValueError):
            max_iter = 15
        max_iter = max(1, min(max_iter, 100))
        timeout = contract.timeout_s
        if not (isinstance(timeout, (int, float)) and math.isfinite(timeout)
                and timeout > 0):
            timeout = 300.0
        sub = SubagentContract(
            name=node.id, role=str(node.config.get("role", "generalist"))[:64],
            task=f"{task_text}\n\n[Node input]\n{_compact(inputs)}",
            tools=list(contract.allowed_tools) or ["all"],
            max_iterations=max_iter,
            timeout_seconds=float(timeout),
            workspace=self._workspace,
            output_format="json" if contract.output_schema else "text",
            output_schema=contract.output_schema or None,
            max_retries=max(min(contract.retry_policy.max_attempts - 1, 10), 0))
        result = await self._orch.run(sub)
        status = NodeStatus.SUCCESS if result.success else NodeStatus.FAILURE
        output = result.output if isinstance(result.output, dict) else {"text": str(result.output)}
        return NodeResult(node.id, status, output=output,
                          model=str(getattr(result, "model", ""))[:256],
                          provider=str(getattr(result, "provider", ""))[:128],
                          input_tokens=_nonneg(getattr(result, "input_tokens", 0)),
                          output_tokens=_nonneg(getattr(result, "output_tokens", 0)),
                          cost_usd=_nonneg_f(getattr(result, "cost_usd", 0.0)),
                          duration_s=_nonneg_f(getattr(result, "elapsed_seconds", 0.0)),
                          error_code="" if result.success else "NODE_FAILED",
                          message="" if result.success
                          else redact(str(result.error or ""))[:500])


def _compact(inputs: dict[str, Any], limit: int = 4000) -> str:
    import json as _json
    try:
        raw = _json.dumps(inputs, default=str)
    except (TypeError, ValueError):
        return "(unserializable inputs omitted)"
    return raw if len(raw) <= limit else raw[:limit] + "…(truncated; see artifacts)"


def _nonneg(v: Any) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0
    return max(0, int(v))


def _nonneg_f(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0
    return max(0.0, float(v))


def default_executor(workspace: str = ".", max_concurrency: int = 8,
                     store: Any = None) -> Any:
    """Stock executor: SubagentOrchestrator runner + reference functions.

    Single construction point shared by CLI `graph run/resume/execute` and
    the SDK, so planner-approved graphs execute through the same governed
    runner as manually authored ones.
    """
    from wisp.graph.executor import GraphExecutor
    from wisp.graph.reference import default_functions
    ex = GraphExecutor(workspace=workspace, max_concurrency=max_concurrency,
                       store=store)
    try:
        from wisp.multi_agent import SubagentOrchestrator
        ex._runner = SubagentNodeRunner(SubagentOrchestrator(), workspace)
    except Exception:
        pass
    for name, fn in default_functions().items():
        ex.register_function(name, fn)
    return ex
