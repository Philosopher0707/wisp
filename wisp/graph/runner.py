"""Default node runner — executes AGENT nodes via SubagentOrchestrator.

Node -> ModelPolicy -> ProviderRuntime. Never provider-specific code here:
the contract becomes a SubagentContract and the orchestrator (with the
configured provider factory) does the rest. Each branch gets its own
contract, context slice, tool permissions, retry budget, and trace.
"""

from __future__ import annotations

from typing import Any

from wisp.graph.types import GraphNode, NodeResult, NodeStatus


class SubagentNodeRunner:
    """Callable runner backed by a SubagentOrchestrator (or compatible)."""

    def __init__(self, orchestrator: Any, workspace: str = ".") -> None:
        self._orch = orchestrator
        self._workspace = workspace

    async def __call__(self, node: GraphNode, inputs: dict[str, Any]) -> NodeResult:
        from wisp.multi_agent.task import SubagentContract
        contract = node.effective_contract()
        task_text = str(inputs.get("task", node.config.get("prompt", node.contract.description
                                   if node.contract else node.id)))
        sub = SubagentContract(
            name=node.id, role=str(node.config.get("role", "generalist")),
            task=f"{task_text}\n\n[Node input]\n{_compact(inputs)}",
            tools=list(contract.allowed_tools) or ["all"],
            max_iterations=int(node.config.get("max_iterations", 15)),
            timeout_seconds=float(contract.timeout_s),
            workspace=self._workspace,
            output_format="json" if contract.output_schema else "text",
            output_schema=contract.output_schema or None,
            max_retries=max(contract.retry_policy.max_attempts - 1, 0))
        result = await self._orch.run(sub)
        status = NodeStatus.SUCCESS if result.success else NodeStatus.FAILURE
        output = result.output if isinstance(result.output, dict) else {"text": str(result.output)}
        return NodeResult(node.id, status, output=output,
                          model=getattr(result, "model", ""),
                          provider=getattr(result, "provider", ""),
                          input_tokens=getattr(result, "input_tokens", 0) or 0,
                          output_tokens=getattr(result, "output_tokens", 0) or 0,
                          cost_usd=getattr(result, "cost_usd", 0.0) or 0.0,
                          duration_s=getattr(result, "elapsed_seconds", 0.0) or 0.0,
                          error_code="" if result.success else "NODE_FAILED",
                          message="" if result.success else str(result.error or "")[:500])


def _compact(inputs: dict[str, Any], limit: int = 4000) -> str:
    import json as _json
    raw = _json.dumps(inputs, default=str)
    return raw if len(raw) <= limit else raw[:limit] + "…(truncated; see artifacts)"
