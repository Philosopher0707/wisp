"""Optimizer pass registry: name -> pass function. Fixed order in PASS_ORDER."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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


def artifact_transport_pass(graph: Graph, ctx: OptimizationContext) -> PassResult:
    """Decide INLINE vs ARTIFACT transport per mapping (OPT-003). Annotation only.

    COMPILE-TIME BOUNDARY (hard rule): this pass never executes nodes, never
    sees runtime values, never calls ArtifactStore.put(), never writes files,
    never hashes content. It records a transport CONTRACT (which mappings
    should travel as artifact references at runtime). Actual artifact
    creation, scrubbing, sizing, and containment stay exclusively runtime
    responsibilities of ArtifactStore.

    Rules: conditional edges carry control -> always INLINE, silent. Unknown
    size (no host hint) -> INLINE + silent (never speculate). Known size over
    threshold -> ARTIFACT recommendation + OPT-003 diagnostic. Known size over
    the artifact hard limit -> INLINE + advisory (runtime would reject).
    The graph itself is never mutated by this pass.
    """
    from wisp.graph.artifacts import MAX_ARTIFACT_BYTES
    nodes = {n.id: n for n in graph.nodes}
    transports: dict[str, dict[str, Any]] = {}
    diagnostics: list = []
    for e in graph.edges:
        if not e.mapping:
            continue
        dst_node = nodes.get(e.to_node)
        for dst_path, src_path in e.mapping.items():
            key = f"{e.from_node}->{e.to_node}:{dst_path}"
            if e.condition:
                transports[key] = {"transport": "INLINE",
                                   "reason": "control edge: verdicts/labels stay inline"}
                continue
            size = ctx.size_hints.get((e.from_node, e.to_node, dst_path))
            if size is None or not isinstance(size, (int, float)) or size < 0:
                transports[key] = {"transport": "INLINE",
                                   "reason": "size UNKNOWN: preserve inline, no speculation"}
                continue
            declared = _schema_declares(dst_node, dst_path) if dst_node else False
            if size > MAX_ARTIFACT_BYTES:
                transports[key] = {"transport": "INLINE",
                                   "reason": "exceeds artifact hard limit; runtime would reject",
                                   "estimated_bytes": int(size),
                                   "schema_declared": declared}
                diagnostics.append(diag(
                    "OPT-003", "context",
                    "large transfer exceeds artifact limit; kept inline, may fail at runtime",
                    "reduce payload or split the mapping", node=e.to_node,
                    extra={"mapping": key, "bytes": str(int(size))}))
                continue
            if size > ctx.inline_threshold_bytes:
                transports[key] = {"transport": "ARTIFACT",
                                   "reason": "declared transfer exceeds inline threshold",
                                   "source": str(src_path)[:256],
                                   "estimated_bytes": int(size),
                                   "schema_declared": declared}
                diagnostics.append(diag(
                    "OPT-003", "context",
                    "large transfer should travel as artifact reference",
                    "runtime resolves artifact:// transparently via mappings",
                    node=e.to_node,
                    extra={"mapping": key, "bytes": str(int(size))}))
            else:
                transports[key] = {"transport": "INLINE",
                                   "reason": "below inline threshold",
                                   "estimated_bytes": int(size),
                                   "schema_declared": declared}
    return PassResult(graph, changed=False, diagnostics=diagnostics,
                      meta={"transports": transports})


def _schema_declares(node, dst_path: str) -> bool:
    """Static check: does the consumer input schema declare the target field?"""
    try:
        schema = node.effective_contract().input_schema
    except Exception:
        return False
    if not isinstance(schema, dict):
        return False
    props = schema.get("properties")
    if not isinstance(props, dict):
        return False  # no declared properties -> UNKNOWN, not incompatible
    return dst_path.split(".")[0] in props


PASSES: dict[str, "PassFn"] = {
    "dependency": dependency_pass,
    "artifact_transport": artifact_transport_pass,
}
