"""Materialized task graph (migration P4).

Turns a turn's work into an explicit, inspectable graph — and makes that graph
**persistent state** rather than a recomputation.

Three things the existing code already had, and one it did not:

| Concern | Before | After |
|---|---|---|
| Node vocabulary | `graph/types.py::NodeType` / `NodeStatus` | reused unchanged |
| Grouping rule | `graph/scheduler.py::ready_nodes` recomputed readiness on every pass and **stored nothing** | `ready` is a field on each node, written by `apply_transition` |
| Write path | ~30 direct status mutations across the codebase, 11 of them unpersisted (audit) | `apply_transition()` is the only writer, and it validates |
| Persistence | `GraphStore` uses its **own** SQLite file | the session journal (`UnifiedStore`), so the graph is a **projection of the durable record** |

**Why the journal and not `GraphStore`.** `WISP_MIGRATION_PLAN.md` says to reuse
`wisp/graph/`'s store. This reuses its *types and legality rules* but journals through
`UnifiedStore`, deliberately: `GraphStore` opens a second SQLite database
(`graph/store.py::_conn`), and a second database would fragment the durable record that P0-P3 spent
four phases consolidating into one append-only journal. A graph that replays from the same log as
everything else is also what lets the message list become a *projection* of the graph — the plan's own
stated mitigation for P4's risk. Recorded as ADR-0019.

**Ready is materialized, and that is the point.** `ready_nodes()` in `graph/scheduler.py` is a pure
function of `(graph, state)`. Materializing it is what makes readiness *state*: it can be inspected
without re-deriving, persisted, and compared against a recomputation — which is how a divergence
between the graph and the scheduler becomes visible instead of silent.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable

from wisp.graph.types import NodeStatus, NodeType

#: The node state machine. `apply_transition` refuses anything not listed here,
#: so an illegal move is a loud error rather than a status that quietly means
#: two things. Terminal states have no outgoing edges — the same discipline as
#: `runs/record.py::LEGAL_TRANSITIONS`.
#:
#: PENDING may settle DIRECTLY (→ SUCCESS/FAILURE/TIMEOUT) as well as via
#: RUNNING. That is not laxity: a graph materialized retroactively from a
#: completed turn, or a node whose work was instantaneous, has no observed
#: RUNNING step to record, and refusing the settlement would force a caller to
#: write a transition that never happened. What the machine still catches is the
#: error class that matters — anything leaving a TERMINAL state, and a stale
#: `from_status`.
LEGAL_NODE_TRANSITIONS: dict[NodeStatus, tuple[NodeStatus, ...]] = {
    NodeStatus.PENDING: (NodeStatus.RUNNING, NodeStatus.SUCCESS,
                         NodeStatus.FAILURE, NodeStatus.TIMEOUT,
                         NodeStatus.SKIPPED, NodeStatus.CANCELLED),
    NodeStatus.RUNNING: (NodeStatus.SUCCESS, NodeStatus.FAILURE,
                         NodeStatus.TIMEOUT, NodeStatus.CANCELLED,
                         NodeStatus.SKIPPED),
    NodeStatus.SUCCESS: (),
    NodeStatus.FAILURE: (),
    NodeStatus.TIMEOUT: (),
    NodeStatus.CANCELLED: (),
    NodeStatus.SKIPPED: (),
}

TERMINAL_NODE_STATUSES = frozenset({
    NodeStatus.SUCCESS, NodeStatus.FAILURE, NodeStatus.TIMEOUT,
    NodeStatus.CANCELLED, NodeStatus.SKIPPED,
})


def is_legal_node_transition(from_status: NodeStatus | str,
                             to_status: NodeStatus | str) -> bool:
    return NodeStatus(to_status) in LEGAL_NODE_TRANSITIONS[NodeStatus(from_status)]


@dataclass(frozen=True)
class TaskNode:
    """One unit of a turn's work.

    `ready` is MATERIALIZED — written by `apply_transition`, never derived at
    read time. A caller asking "what can run now?" reads a stored field.
    """

    node_id: str
    kind: NodeType = NodeType.AGENT
    iteration: int = 0
    status: NodeStatus = NodeStatus.PENDING
    ready: bool = False
    deps: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "kind": self.kind.value,
                "iteration": self.iteration, "status": self.status.value,
                "ready": self.ready, "deps": list(self.deps),
                "detail": self.detail}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskNode":
        return cls(node_id=d["node_id"],
                   kind=NodeType(d.get("kind", "agent")),
                   iteration=int(d.get("iteration", 0)),
                   status=NodeStatus(d.get("status", "pending")),
                   ready=bool(d.get("ready", False)),
                   deps=tuple(d.get("deps") or ()),
                   detail=d.get("detail", ""))


@dataclass(frozen=True)
class NodeTransition:
    """The ONLY write path for node state.

    Carries the reason and the sequence number, so the journal can answer not
    just *what* a node's status is but *why* it changed and *when* — which the
    ~30 direct mutations it replaces could not.
    """

    run_id: str
    node_id: str
    from_status: NodeStatus
    to_status: NodeStatus
    seq: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "node_id": self.node_id,
                "from_status": self.from_status.value,
                "to_status": self.to_status.value, "seq": self.seq,
                "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "NodeTransition":
        return cls(run_id=d["run_id"], node_id=d["node_id"],
                   from_status=NodeStatus(d["from_status"]),
                   to_status=NodeStatus(d["to_status"]),
                   seq=int(d.get("seq", 0)), reason=d.get("reason", ""))


@dataclass(frozen=True)
class TaskGraph:
    """A materialized view of one turn's work."""

    run_id: str
    entrypoint: str = ""
    nodes: tuple[TaskNode, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()

    def node(self, node_id: str) -> TaskNode | None:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def ready_ids(self) -> list[str]:
        """Read the MATERIALIZED readiness — no recomputation."""
        return sorted(n.node_id for n in self.nodes if n.ready)

    def with_node(self, node: TaskNode) -> "TaskGraph":
        return replace(self, nodes=tuple(
            node if n.node_id == node.node_id else n for n in self.nodes))

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "entrypoint": self.entrypoint,
                "nodes": [n.to_dict() for n in self.nodes],
                "edges": [list(e) for e in self.edges]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskGraph":
        return cls(run_id=d["run_id"], entrypoint=d.get("entrypoint", ""),
                   nodes=tuple(TaskNode.from_dict(n)
                               for n in d.get("nodes") or ()),
                   edges=tuple(tuple(e) for e in d.get("edges") or ()))


# ── Construction ────────────────────────────────────────────────────────


def build_turn_graph(run_id: str, iterations: int) -> TaskGraph:
    """One `AGENT` node per turn iteration, chained in order.

    The plan's mapping: *"the turn becomes a graph with one AGENT node per
    iteration."* Iteration 0 is the entrypoint; each later node depends on the
    one before it, so the chain is a real dependency structure rather than a
    list — which is what makes readiness meaningful rather than trivially
    "everything".
    """
    nodes: list[TaskNode] = []
    edges: list[tuple[str, str]] = []
    for i in range(max(0, int(iterations))):
        nid = f"turn:{i}"
        deps = (f"turn:{i - 1}",) if i else ()
        nodes.append(TaskNode(node_id=nid, kind=NodeType.AGENT, iteration=i,
                              deps=deps))
        if i:
            edges.append((f"turn:{i - 1}", nid))
    return TaskGraph(run_id=run_id,
                     entrypoint="turn:0" if nodes else "",
                     nodes=tuple(nodes), edges=tuple(edges))


def _compute_ready(graph: TaskGraph) -> set[str]:
    """Derive readiness. Used ONLY to materialize it, never at read time."""
    statuses = {n.node_id: n.status for n in graph.nodes}
    incoming: dict[str, list[str]] = {n.node_id: [] for n in graph.nodes}
    for src, dst in graph.edges:
        if dst in incoming:
            incoming[dst].append(src)
    ready: set[str] = set()
    for n in graph.nodes:
        if n.status is not NodeStatus.PENDING:
            continue
        if n.node_id == graph.entrypoint:
            ready.add(n.node_id)
            continue
        preds = incoming[n.node_id]
        if not preds:
            continue
        if all(statuses.get(p) in TERMINAL_NODE_STATUSES for p in preds):
            ready.add(n.node_id)
    return ready


def materialize(graph: TaskGraph) -> TaskGraph:
    """Write the derived readiness back onto the nodes.

    Idempotent: materializing twice yields the same graph. Returns a new graph;
    never mutates in place.
    """
    ready = _compute_ready(graph)
    return replace(graph, nodes=tuple(
        replace(n, ready=n.node_id in ready) for n in graph.nodes))


def apply_transition(graph: TaskGraph,
                     transition: NodeTransition) -> TaskGraph:
    """The single write path for node state.

    Validates legality, refuses an unknown node, refuses a stale
    `from_status`, and **re-materializes readiness** so the stored `ready`
    flags cannot go stale after a status change.

    Raises `ValueError` rather than returning a sentinel: an illegal
    transition is a defect, and a silently-ignored one is how a node ends up
    in a state nobody can explain.
    """
    node = graph.node(transition.node_id)
    if node is None:
        raise ValueError(f"unknown node: {transition.node_id}")
    if node.status is not transition.from_status:
        raise ValueError(
            f"stale transition: {transition.node_id} is {node.status.value}, "
            f"not {transition.from_status.value}")
    if not is_legal_node_transition(transition.from_status,
                                    transition.to_status):
        raise ValueError(
            f"illegal node transition: {transition.from_status.value} -> "
            f"{transition.to_status.value}")
    return materialize(graph.with_node(
        replace(node, status=transition.to_status)))


def replay_transitions(graph: TaskGraph,
                       transitions: Iterable[NodeTransition]) -> TaskGraph:
    """Rebuild a graph by applying transitions in sequence order.

    This is what makes the graph a *projection of the journal*: a fresh replay
    of the same rows produces the same graph, so the persisted graph is a
    cache and the journal is the truth.
    """
    out = materialize(graph)
    for t in sorted(transitions, key=lambda t: t.seq):
        out = apply_transition(out, t)
    return out


def divergences(graph: TaskGraph) -> list[str]:
    """Node ids whose materialized `ready` disagrees with a fresh derivation.

    A cheap integrity check. Materializing readiness buys inspectability at the
    cost of a value that can go stale; this is how staleness becomes *visible*
    rather than a silent difference between what the graph says and what the
    scheduler would do.
    """
    derived = _compute_ready(graph)
    return sorted(
        n.node_id for n in graph.nodes if n.ready != (n.node_id in derived))
