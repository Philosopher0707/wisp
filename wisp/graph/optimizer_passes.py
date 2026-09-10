"""Optimizer pass registry: name -> pass function. Fixed order in PASS_ORDER."""

from __future__ import annotations

from typing import TYPE_CHECKING

from wisp.graph.optimizer import PassResult, OptimizationContext, diag
from wisp.graph.types import Graph, NodeType

if TYPE_CHECKING:
    from wisp.graph.optimizer import PassFn

# Control-decision sources: their outputs gate downstream even unmapped.
_CONTROL_SOURCES = frozenset({NodeType.ROUTER, NodeType.VERIFIER, NodeType.GATE})
# Targets whose incoming edges carry completion/control signals.
_SIGNAL_TARGETS = frozenset({NodeType.JOIN, NodeType.APPROVAL,
                             NodeType.FUNCTION, NodeType.GATE})


def _cycle_members(graph: Graph) -> set[str]:
    members: set[str] = set()
    for n in graph.nodes:
        if n.cycle is not None:
            members.add(n.cycle.entry)
            members.update(n.cycle.body)
            members.add(n.cycle.exit_gate)
    return members


def dependency_pass(graph: Graph, ctx: OptimizationContext) -> PassResult:
    """Remove provably-fake dependencies (OPT-001). Conservative by default.

    An edge A->B is removed ONLY when: no mapping, no condition, source is
    not a control decision, target needs no completion signal, neither end
    is side-effect-unsafe, the edge is outside declared cycles, and B keeps
    another incoming edge (or B is the entrypoint, whose incoming edges are
    runtime-dead). Everything else -> advisory, graph untouched.
    """
    nodes = {n.id: n for n in graph.nodes}
    members = _cycle_members(graph)
    incoming: dict[str, int] = {n.id: 0 for n in graph.nodes}
    for e in graph.edges:
        if e.to_node in incoming:
            incoming[e.to_node] += 1
    keep: list = []
    removed: list = []
    diagnostics: list = []
    dropped_per_target: dict[str, int] = {}
    for e in graph.edges:
        verdict = _removable(graph, nodes, members, incoming, e,
                             dropped_per_target)
        if verdict is None:
            keep.append(e)
        elif verdict is True:
            removed.append(e)
            dropped_per_target[e.to_node] = dropped_per_target.get(e.to_node, 0) + 1
            diagnostics.append(diag(
                "OPT-001", "dependency",
                "B consumes no declared output/control state from A",
                "edge removed; B may run without waiting for A",
                node=e.to_node,
                extra={"edge": f"{e.from_node}->{e.to_node}"}))
        else:  # advisory string reason
            keep.append(e)
            if not e.mapping and not e.condition:
                diagnostics.append(diag(
                    "OPT-001", "dependency",
                    f"unmapped edge preserved: {verdict}",
                    "no transformation; original kept", node=e.to_node,
                    extra={"edge": f"{e.from_node}->{e.to_node}"}))
    if not removed:
        return PassResult(graph, changed=False, diagnostics=diagnostics)
    return PassResult(
        Graph(id=graph.id, version=graph.version, entrypoint=graph.entrypoint,
              nodes=graph.nodes, edges=tuple(keep), policies=graph.policies),
        changed=True, diagnostics=diagnostics)


def _removable(graph: Graph, nodes: dict, members: set[str],
               incoming: dict[str, int], e, dropped: dict[str, int]) -> bool | str | None:
    """True = remove; None = not a candidate; str = advisory keep-reason."""
    if e.mapping or e.condition:
        return None  # declared data/control dependence: never touch
    src = nodes.get(e.from_node)
    dst = nodes.get(e.to_node)
    if src is None or dst is None:
        return None  # invalid input rejected upstream; don't guess
    if src.type in _CONTROL_SOURCES:
        return "control-decision source"
    if dst.type in _SIGNAL_TARGETS:
        return "target needs completion signal"
    if e.from_node in members and e.to_node in members:
        return "declared cycle member"
    for n in (src, dst):
        c = n.effective_contract()
        if not c.idempotent or c.retry_policy.unsafe_side_effects:
            return "side-effect-unsafe endpoint"
    remaining = incoming.get(e.to_node, 0) - dropped.get(e.to_node, 0)
    if e.to_node != graph.entrypoint and remaining < 2:
        return "sole incoming edge"
    return True


PASSES: dict[str, "PassFn"] = {
    "dependency": dependency_pass,
}

