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

import logging
import math
import os
from collections.abc import Mapping
from typing import Any

from wisp.graph.security import scrub_text as redact
from wisp.graph.types import GraphNode, NodeResult, NodeStatus

MAX_TASK_CHARS = 8000

logger = logging.getLogger(__name__)


class SubagentNodeRunner:
    """Callable runner backed by a SubagentOrchestrator (or compatible).

    *model_tiers* is the operator's map from a node's tier (`cheap | standard | strong`) to a model of the session's provider (`wisp.graph.tiers`). It is
    the only way a model reaches a subagent from here: a model a graph names, and anything in a node's inputs, is never read.
    """

    def __init__(self, orchestrator: Any, workspace: str = ".", model_tiers: Mapping[str, str] | None = None) -> None:
        self._orch = orchestrator
        self._workspace = workspace
        self._tiers = dict(model_tiers or {})

    @property
    def model_tiers(self) -> dict[str, str]:
        return dict(self._tiers)

    def _session_model(self) -> str:
        return str(getattr(getattr(self._orch, "config", None), "model", "") or "")

    async def __call__(self, node: GraphNode, inputs: dict[str, Any]) -> NodeResult:
        from wisp.multi_agent.task import SubagentContract
        contract = node.effective_contract()
        model = self._tiers.get(contract.model_policy.model_class) or None
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
        isolated = inputs.get("isolated_workspace", "")
        workspace = self._workspace
        if isinstance(isolated, str) and isolated:
            # Non-git fallback prepared by a setup node: run the worker with
            # its file tools rooted at the isolated copy. Contained below.
            try:
                from wisp.workspace import _contain
                _contain(self._workspace, os.path.relpath(isolated, self._workspace))
                workspace = isolated
            except (OSError, ValueError):
                workspace = self._workspace
        sub = SubagentContract(
            name=node.id, role=str(node.config.get("role", "generalist"))[:64],
            task=f"{task_text}\n\n[Node input]\n{_compact(inputs)}",
            tools=list(contract.allowed_tools) or ["all"],
            max_iterations=max_iter,
            timeout_seconds=float(timeout),
            workspace=workspace,
            worktree_isolated=bool(node.config.get("isolated", False)),
            model=model,
            output_format="json" if contract.output_schema else "text",
            output_schema=contract.output_schema or None,
            max_retries=max(min(contract.retry_policy.max_attempts - 1, 10), 0))
        result = await self._orch.run(sub)
        status = NodeStatus.SUCCESS if result.success else NodeStatus.FAILURE
        output = result.output if isinstance(result.output, dict) else {"text": str(result.output)}
        patch = getattr(result, "worktree_patch", "")
        if isinstance(patch, str) and patch and len(patch) < 4_000_000:
            output = {**output, "worktree_patch": patch}
        return NodeResult(node.id, status, output=output,
                          model=str(getattr(result, "model", "") or getattr(result, "model_used", "") or model or self._session_model())[:256],
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


def _configured_tiers(config: Any) -> dict[str, str]:
    """The operator's tier map: from *config* when it carries one, else from the environment and the config file. A map that cannot be read is empty
    (every node on the session model), never a reason to stop the graph."""
    from wisp.graph.tiers import parse_model_tiers

    try:
        raw = getattr(config, "graph_model_tiers", None)
        if raw is None:
            from wisp.config import get_setting

            raw = get_setting("graph_model_tiers", "")
        return parse_model_tiers(raw)
    except Exception:
        logger.warning("graph_model_tiers could not be read; every graph node uses the session model", exc_info=True)
        return {}


def default_executor(workspace: str = ".", max_concurrency: int = 8,
                     store: Any = None, emit: Any = None, config: Any = None) -> Any:
    """Stock executor: SubagentOrchestrator runner + reference functions.

    Single construction point shared by CLI `graph run/resume/execute` and
    the SDK, so planner-approved graphs execute through the same governed
    runner as manually authored ones. *config* (a WispConfig) is the
    workers' provider/identity; None keeps the legacy default config.
    """
    from wisp.graph.executor import GraphExecutor
    from wisp.graph.reference import default_functions
    ex = GraphExecutor(workspace=workspace, max_concurrency=max_concurrency,
                       store=store, emit=emit or (lambda e: None))
    try:
        from wisp.multi_agent import SubagentOrchestrator
        ex._runner = SubagentNodeRunner(
            SubagentOrchestrator(config=config) if config is not None
            else SubagentOrchestrator(), workspace, model_tiers=_configured_tiers(config))
    except Exception:
        pass
    for name, fn in default_functions().items():
        ex.register_function(name, fn)
    try:
        from wisp.workspace import isolation_functions
        for name, fn in isolation_functions(workspace).items():
            ex.register_function(name, fn)
    except Exception:
        pass
    return ex
