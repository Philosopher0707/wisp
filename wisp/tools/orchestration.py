"""Orchestration pattern tools — vote, map_reduce, chain, dag.

Extracted from ToolExecutor as part of the monolith extraction program.
Every function receives its dependencies explicitly (OrchestrationDeps),
so patterns are testable without a ToolExecutor and the executor keeps
only one-line delegates. Tests drive them through the executor methods,
which remain stable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import Any, Callable

from wisp.multi_agent.task import SubagentContract


@dataclass(frozen=True)
class OrchestrationDeps:
    """What each pattern needs from its host executor."""

    orchestrator: Any
    build_contract: Callable[..., tuple[Any | None, str]]
    tool_error: Callable[[str, str], str]


def pattern_result(tool: str, pattern: str, result: Any) -> str:
    """Shared JSON envelope for vote/map-reduce/chain outcomes."""
    ok = bool(getattr(result, "success", False)) if result is not None else False
    output = (getattr(result, "output", "") or "")[:2000] if result is not None else ""
    return json.dumps({
        "status": "ok" if ok else "error",
        "tool": tool,
        "data": {
            "ok": ok,
            "pattern": pattern,
            "summary": output,
            "files": list(getattr(result, "files_changed", []) or []) if result is not None else [],
            "error": getattr(result, "error", None) if result is not None else "no result",
            "elapsed_seconds": round(getattr(result, "elapsed_seconds", 0.0) or 0.0, 1),
        },
        "metadata": {
            "pattern": pattern,
            "task_id": getattr(result, "task_id", "") if result is not None else "",
        },
    }, ensure_ascii=False)


async def vote(deps: OrchestrationDeps, func_args: dict[str, Any], workspace: str) -> str:
    """orchestrate_vote — N independent voters, majority wins."""
    orch = deps.orchestrator
    if orch is None:
        return deps.tool_error("orchestrate_vote",
                               "Subagent orchestrator not available — wire it via CompositionRoot")
    task = func_args.get("task", "")
    if not task:
        return deps.tool_error("orchestrate_vote", "orchestrate_vote requires a 'task' argument")

    try:
        n_voters = max(2, min(6, int(func_args.get("voters", 3) or 3)))
    except (TypeError, ValueError):
        n_voters = 3
    try:
        threshold = float(func_args.get("consensus_threshold", 0.6) or 0.6)
    except (TypeError, ValueError):
        threshold = 0.6
    threshold = min(1.0, max(0.1, threshold))

    base_raw, err = deps.build_contract({"task": task, **{
        k: func_args[k] for k in ("role", "timeout_seconds", "model") if k in func_args
    }}, workspace, name="vote-voter")
    if err or not isinstance(base_raw, SubagentContract):
        return deps.tool_error("orchestrate_vote", err or "contract build failed")
    base: SubagentContract = base_raw

    voters = [
        dc_replace(base, name=f"vote-{i}")
        for i in range(n_voters)
    ]
    result = await orch.run_vote(
        task=task, agents=voters,
        consensus_threshold=threshold,
    )
    return pattern_result("orchestrate_vote", "vote", result)


async def map_reduce(deps: OrchestrationDeps, func_args: dict[str, Any], workspace: str) -> str:
    """orchestrate_map_reduce — parallel mappers + synthesis."""
    orch = deps.orchestrator
    if orch is None:
        return deps.tool_error("orchestrate_map_reduce",
                               "Subagent orchestrator not available — wire it via CompositionRoot")
    task = func_args.get("task", "")
    items = func_args.get("items") or []
    if not task:
        return deps.tool_error("orchestrate_map_reduce", "orchestrate_map_reduce requires a 'task' argument")
    if not isinstance(items, list) or not items:
        return deps.tool_error("orchestrate_map_reduce",
                               "orchestrate_map_reduce requires a non-empty 'items' array")
    items = [str(i) for i in items[:20]]

    role = func_args.get("role", "generalist")
    try:
        max_concurrent = max(1, min(8, int(func_args.get("max_concurrent", 4) or 4)))
    except (TypeError, ValueError):
        max_concurrent = 4

    template_raw, err = deps.build_contract(
        {"task": task, "role": role}, workspace, name="map")
    if err or not isinstance(template_raw, SubagentContract):
        return deps.tool_error("orchestrate_map_reduce",
                               err or "contract build failed")
    template: SubagentContract = template_raw

    def mapper(item: str) -> SubagentContract:
        return dc_replace(template, task=f"{task}\n\nItem:\n{item}")

    reducer_task = (
        f"Synthesize the following per-item findings into one coherent answer. "
        f"Overall goal: {task}"
    )
    result = await orch.run_map_reduce(
        task=task, items=items, mapper=mapper,
        reducer=reducer_task, max_concurrent=max_concurrent,
    )
    return pattern_result("orchestrate_map_reduce", "map_reduce", result)


async def chain(deps: OrchestrationDeps, func_args: dict[str, Any], workspace: str) -> str:
    """orchestrate_chain — sequential pipeline with context passing."""
    orch = deps.orchestrator
    if orch is None:
        return deps.tool_error("orchestrate_chain",
                               "Subagent orchestrator not available — wire it via CompositionRoot")
    steps = func_args.get("steps") or []
    if not isinstance(steps, list) or len(steps) < 2:
        return deps.tool_error("orchestrate_chain",
                               "orchestrate_chain requires a 'steps' array with at least 2 steps")

    contracts = []
    for i, step in enumerate(steps[:6]):
        if not isinstance(step, dict) or not step.get("task"):
            return deps.tool_error("orchestrate_chain", f"step {i} requires a 'task'")
        contract, err = deps.build_contract(
            {"task": step["task"], "role": step.get("role", "generalist")},
            workspace, name=f"chain-{i}",
        )
        if err:
            return deps.tool_error("orchestrate_chain", err)
        contracts.append(contract)

    pass_context = bool(func_args.get("pass_context", True))
    result = await orch.run_chain(contracts, pass_context=pass_context)
    return pattern_result("orchestrate_chain", "chain", result)


async def dag(deps: OrchestrationDeps, func_args: dict[str, Any], workspace: str) -> str:
    """orchestrate_dag — dependency-ordered parallel subagents.

    Independent nodes run in parallel per level; upstream outputs are
    injected into dependents by the scheduler (dataflow edges, not
    just ordering).
    """
    from wisp.multi_agent.dag import TaskDAG, TaskNode

    orch = deps.orchestrator
    if orch is None:
        return deps.tool_error("orchestrate_dag",
                               "Subagent orchestrator not available — wire it via CompositionRoot")
    nodes_spec = func_args.get("nodes") or []
    if not isinstance(nodes_spec, list) or not nodes_spec:
        return deps.tool_error("orchestrate_dag",
                               "orchestrate_dag requires a non-empty 'nodes' array")

    dag = TaskDAG()
    seen_names: set[str] = set()
    for i, spec in enumerate(nodes_spec):
        if not isinstance(spec, dict) or not spec.get("name") or not spec.get("task"):
            return deps.tool_error(
                "orchestrate_dag", f"node {i} requires 'name' and 'task'")
        name = str(spec["name"])
        if name in seen_names:
            return deps.tool_error("orchestrate_dag", f"duplicate node name '{name}'")
        seen_names.add(name)

    for spec in nodes_spec:
        name = str(spec["name"])
        contract, err = deps.build_contract(
            {"task": spec["task"], "role": spec.get("role", "generalist")},
            workspace, name=name,
        )
        if err:
            return deps.tool_error("orchestrate_dag", err)
        node_deps = spec.get("depends_on") or []
        unknown = [d for d in node_deps if d not in seen_names]
        if unknown:
            return deps.tool_error(
                "orchestrate_dag",
                f"node '{name}' depends on unknown node(s): {', '.join(unknown)}")
        dag.add_node(TaskNode(name=name, task=contract, dependencies=list(node_deps)))

    cycle_errors = dag.validate()
    if cycle_errors:
        return deps.tool_error("orchestrate_dag",
                               f"invalid DAG: {'; '.join(cycle_errors[:3])}")

    try:
        max_par = max(1, min(8, int(func_args.get("max_parallelism", 4) or 4)))
    except (TypeError, ValueError):
        max_par = 4

    dag_result = await orch.run_dag(dag, max_parallelism=max_par)
    ok = bool(getattr(dag_result, "success", False))
    node_results = getattr(dag_result, "node_results", {}) or {}
    level_order = getattr(dag_result, "level_order", []) or []
    ordered_names = [n for level in level_order for n in level] or list(node_results)
    summary_lines = []
    for node_name in ordered_names:
        r = node_results.get(node_name)
        out = (getattr(r, "output", "") or "").strip() if r is not None else ""
        status = "ok" if (r is not None and getattr(r, "success", False)) else "FAILED"
        snippet = out[:160].replace("\n", " ")
        summary_lines.append(f"[{status}] {node_name}: {snippet}" if snippet
                             else f"[{status}] {node_name}")
    all_files = sorted({f for r in node_results.values()
                        for f in (getattr(r, "files_changed", []) or [])})
    return json.dumps({
        "status": "ok" if ok else "error",
        "tool": "orchestrate_dag",
        "data": {
            "ok": ok,
            "pattern": "dag",
            "summary": "\n".join(summary_lines)[:2000],
            "files": all_files,
            "error": "; ".join(getattr(dag_result, "errors", [])[:3]) or None,
            "elapsed_seconds": round(getattr(dag_result, "total_elapsed", 0.0) or 0.0, 1),
            "level_order": level_order,
        },
        "metadata": {
            "pattern": "dag",
            "nodes": len(node_results),
        },
    }, ensure_ascii=False)
