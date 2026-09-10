"""Ready-queue scheduler — dependency-aware, never list order.

A node is runnable only when its dependency predicates are satisfied:
  - unconditional edges: all sources settled (join policy decides sufficiency)
  - conditional edges (verifier.accept / router labels): taken branch only
Independent nodes are all eligible concurrently; the executor bounds them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wisp.graph.types import Graph, JoinPolicy, NodeStatus


@dataclass
class SchedulerState:
    statuses: dict[str, NodeStatus] = field(default_factory=dict)
    # Conditional edge labels selected so far: node_id -> label (e.g. "accept").
    taken: dict[str, str] = field(default_factory=dict)
    # Streaming-join cursors: join_id -> branch ids already consumed.
    consumed: dict[str, set[str]] = field(default_factory=dict)


def ready_nodes(graph: Graph, state: SchedulerState) -> list[str]:
    """Return node ids eligible to start now, in deterministic order."""
    nodes = {n.id: n for n in graph.nodes}
    incoming: dict[str, list] = {i: [] for i in nodes}
    for e in graph.edges:
        if e.to_node in incoming:
            incoming[e.to_node].append(e)
    ready: list[str] = []
    for nid in sorted(nodes):
        if state.statuses.get(nid, NodeStatus.PENDING) != NodeStatus.PENDING:
            continue
        if nid == graph.entrypoint:
            ready.append(nid)  # entrypoint always starts; corrections re-arm it
            continue
        preds = incoming[nid]
        if not preds:
            continue  # non-entry root without edges: dead definition, stays pending
        if _predicates_satisfied(graph, state, nid, preds):
            ready.append(nid)
    return ready


def _predicates_satisfied(graph, state: SchedulerState, nid: str, preds) -> bool:
    nodes = {n.id: n for n in graph.nodes}
    target = nodes[nid]
    # Conditional-only target: runnable when any incoming condition is taken.
    unconditional = [e for e in preds if not e.condition]
    conditional = [e for e in preds if e.condition]
    if conditional and not unconditional:
        for e in conditional:
            if _condition_matches(state.taken.get(e.from_node, ""), e.condition) \
                    and _settled_ok(state, e.from_node):
                return True
        return False
    # Unconditional (possibly mixed): join policy on the TARGET decides.
    # Streaming joins release per-branch (handled by executor); here they are
    # runnable once at least one unconsumed branch settled successfully.
    if target.join_policy == "streaming" if isinstance(target.join_policy, str) else target.join_policy == JoinPolicy.STREAMING:
        return any(_settled_ok(state, e.from_node) for e in unconditional)
    for e in unconditional:
        if state.statuses.get(e.from_node, NodeStatus.PENDING) not in (
            NodeStatus.SUCCESS, NodeStatus.FAILURE, NodeStatus.TIMEOUT,
            NodeStatus.CANCELLED, NodeStatus.SKIPPED,
        ):
            return False
    return bool(unconditional)


def _settled_ok(state: SchedulerState, nid: str) -> bool:
    return state.statuses.get(nid) == NodeStatus.SUCCESS


def condition_matches(taken_label: str, edge_condition: str) -> bool:
    """Lane-aware match: 'reject.tests' matches a 'reject' verdict."""
    return _condition_matches(taken_label, edge_condition)


def _condition_matches(taken_label: str, edge_condition: str) -> bool:
    if not taken_label or not edge_condition:
        return False
    return taken_label == edge_condition or edge_condition.startswith(taken_label + ".")


def is_finished(graph: Graph, state: SchedulerState) -> bool:
    nodes = {n.id: n for n in graph.nodes}
    sinks = [i for i in nodes if not any(e.from_node == i for e in graph.edges)]
    if not sinks:
        return all(s != NodeStatus.PENDING and s != NodeStatus.RUNNING
                   for s in (state.statuses.get(i, NodeStatus.PENDING) for i in nodes))
    return all(state.statuses.get(s, NodeStatus.PENDING) in (
        NodeStatus.SUCCESS, NodeStatus.FAILURE, NodeStatus.TIMEOUT,
        NodeStatus.CANCELLED, NodeStatus.SKIPPED) for s in sinks)
