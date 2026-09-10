"""Backward compatibility — legacy orchestration lowered onto graph primitives.

Existing spawn/fanout/orchestrate_* APIs keep working. Chain and DAG lower
directly to Graph; vote/map_reduce remain orchestrator-backed (their
semantics — consensus thresholds, reducer prompts — are judgment-heavy and
gain nothing from graph lowering today).
"""

from __future__ import annotations

from typing import Any

from wisp.graph.types import (
    EdgeMapping,
    Graph,
    GraphNode,
    GraphPolicy,
    JoinPolicy,
    NodeContract,
    NodeType,
)


def chain_to_graph(name: str, tasks: list[str]) -> Graph:
    nodes = [GraphNode(id=f"step-{i}", type=NodeType.AGENT,
                       contract=NodeContract(id=f"step-{i}", name=f"step-{i}",
                                             description=t))
             for i, t in enumerate(tasks)]
    edges = [EdgeMapping(f"step-{i}", f"step-{i+1}",
                         reason=f"step-{i+1} consumes step-{i}.output")
             for i in range(len(tasks) - 1)]
    return Graph(id=name or "chain", version="1",
                 entrypoint="step-0" if nodes else "",
                 nodes=tuple(nodes), edges=tuple(edges),
                 policies=GraphPolicy(max_concurrency=1))


def dag_to_graph(name: str, nodes: list[dict[str, Any]]) -> Graph:
    """Lower an orchestrate_dag node list [{name, depends_on}] to a Graph."""
    if len(nodes) > 1024:
        raise ValueError("too many dag nodes (max 1024)")
    gnodes = []
    for n in nodes:
        if not isinstance(n, dict) or not n.get("name"):
            raise ValueError(f"malformed dag node: {str(n)[:200]}")
        gnodes.append(GraphNode(id=str(n["name"])[:128], type=NodeType.AGENT,
                                contract=NodeContract(id=str(n["name"])[:128],
                                                      name=str(n["name"])[:128],
                                                      description=str(n.get("task", ""))[:4096])))
    edges = [EdgeMapping(str(dep)[:128], str(n["name"])[:128],
                         reason=f"{n['name']} consumes {dep}.output")
             for n in nodes for dep in (n.get("depends_on", []) or [])]
    roots = [str(n["name"])[:128] for n in nodes if not n.get("depends_on")]
    return Graph(id=(name or "dag")[:128], version="1",
                 entrypoint=roots[0] if roots else "",
                 nodes=tuple(gnodes), edges=tuple(edges))


def fan_to_graph(name: str, items: list[str], join: str = "best_effort") -> Graph:
    if len(items) > 1024:
        raise ValueError("too many fan items (max 1024; partition instead)")
    try:
        policy = JoinPolicy(join)
    except ValueError:
        raise ValueError(f"unknown join policy {join!r}") from None
    branches = [GraphNode(id=f"branch-{i}", type=NodeType.AGENT,
                          contract=NodeContract(id=f"branch-{i}",
                                                description=f"process: {str(item)[:2048]}"))
                for i, item in enumerate(items)]
    join_node = GraphNode(id="join", type=NodeType.JOIN,
                          join_policy=policy,
                          contract=NodeContract(id="join", name="join"))
    nodes = [GraphNode(id="split", type=NodeType.FUNCTION, function="split_work",
                       contract=NodeContract(id="split", name="split")),
             *branches, join_node]
    edges = [EdgeMapping("split", b.id, reason=f"{b.id} consumes split.output.slice")
             for b in branches]
    edges += [EdgeMapping(b.id, "join", reason=f"join consumes {b.id}.output")
              for b in branches]
    return Graph(id=name or "fan", version="1", entrypoint="split",
                 nodes=tuple(nodes), edges=tuple(edges))
