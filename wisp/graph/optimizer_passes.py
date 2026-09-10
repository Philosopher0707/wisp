"""Optimizer pass registry: name -> pass function. Fixed order in PASS_ORDER."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wisp.graph.optimizer import PassResult, OptimizationContext, diag
from wisp.graph.types import Graph, NodeType

if TYPE_CHECKING:
    from wisp.graph.optimizer import PassFn

# Control-decision sources: their outputs gate downstream even unmapped.
# JOIN completion is a control signal: downstream waits for the join result.
_CONTROL_SOURCES = frozenset({NodeType.ROUTER, NodeType.VERIFIER, NodeType.GATE,
                              NodeType.JOIN})
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
            target = nodes.get(e.to_node)
            if (not e.mapping and not e.condition and target is not None
                    and target.type not in _SIGNAL_TARGETS):
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


def _adj(graph: Graph) -> dict[str, list]:
    adj: dict[str, list] = {}
    for e in graph.edges:
        adj.setdefault(e.from_node, []).append(e)
    return adj


def _reachable(graph: Graph, src: str, adj: dict[str, list] | None = None,
               skip_nodes: frozenset = frozenset(),
               skip_accept_from: frozenset = frozenset()) -> set[str]:
    """Reachability with exclusions. skip_accept_from: ignore accept-family
    edges out of these verifier nodes (accept = condition 'accept...' )."""
    adj = adj if adj is not None else _adj(graph)
    seen = {src} if src not in skip_nodes else set()
    stack = [src] if src not in skip_nodes else []
    while stack:
        for e in adj.get(stack.pop(), []):
            if e.to_node in seen or e.to_node in skip_nodes:
                continue
            if e.from_node in skip_accept_from and _is_accept(e.condition):
                continue
            seen.add(e.to_node)
            stack.append(e.to_node)
    return seen


def _is_accept(condition: str) -> bool:
    return bool(condition) and (condition == "accept"
                                or condition.startswith("accept."))


def verification_pass(graph: Graph, ctx: OptimizationContext) -> PassResult:
    """OPT-002: missing-verification obligations at agent sink boundaries.

    Obligation O-TERM: AGENT producer P can reach sink S (S agent-typed work
    node, not a verifier/approval/gate) with no proven verifier gate between
    them. Satisfied iff some VERIFIER V has P⇝V⇝S and S is unreachable from P
    without V's accept-edges, or every P⇝S path crosses an APPROVAL node.
    Remediation: single-choke insertion (P→V*→S accept-gated) only when every
    safety precondition holds; otherwise advisory. Existing verifiers and
    approvals are never touched.
    """
    diagnostics: list = []
    working = graph
    inserted_any = False
    adj = _adj(working)
    for S in sorted(_sinks(working), key=lambda n: n.id):
        if S.type in (NodeType.VERIFIER, NodeType.APPROVAL, NodeType.GATE,
                      NodeType.JOIN, NodeType.ROUTER):
            continue  # human/deterministic/fan-in/control sinks: no obligation.
            # (JOIN/ROUTER sinks cannot take a gated insertion, so enumerating
            # obligations there would be unactionable noise.)
        for P in sorted(n.id for n in working.nodes
                        if n.type == NodeType.AGENT and n.id != S.id):
            if S.id not in _reachable(working, P, adj):
                continue
            by, detail = _satisfied_by(working, P, S.id, adj)
            if by is not None:
                diagnostics.append(diag(
                    "OPT-002", "verification",
                    f"obligation ({P},{S.id}) satisfied by {by}",
                    "no change", node=S.id,
                    extra={"producer": P, "guard": by}))
                continue
            new_graph, note = _try_insert(working, ctx, P, S, detail)
            if new_graph is None:
                diagnostics.append(diag(
                    "OPT-002", "verification",
                    f"obligation ({P},{S.id}): {note}",
                    "advisory only; graph preserved", node=S.id,
                    extra={"producer": P}))
            else:
                working = new_graph
                inserted_any = True
                adj = _adj(working)
                diagnostics.append(diag(
                    "OPT-002", "verification",
                    f"inserted verifier gating {S.id} on {P} output",
                    "revalidate covers the new topology", node=S.id,
                    extra={"producer": P, "verifier": note}))
    return PassResult(working, changed=inserted_any, diagnostics=diagnostics)


def _sinks(graph: Graph) -> list:
    sources = {e.from_node for e in graph.edges}
    return [n for n in graph.nodes if n.id not in sources]


def _satisfied_by(graph: Graph, producer: str, sink: str,
                  adj: dict[str, list] | None = None) -> tuple[str | None, str]:
    """Returns (guard_id, detail) or (None, detail). Proof, not presence.

    A verifier credits an obligation only when it (a) directly consumes the
    producer's output (direct edge in), (b) can reach the sink, and (c) every
    producer⇝sink path requires its accept-edge (gating, not decoration).
    """
    fed_by: dict[str, set[str]] = {}
    for e in graph.edges:
        fed_by.setdefault(e.to_node, set()).add(e.from_node)
    verifiers = [n.id for n in graph.nodes if n.type == NodeType.VERIFIER]
    for v in sorted(verifiers):
        if producer not in fed_by.get(v, set()):
            continue  # V never sees P's output: cannot vouch for it
        if sink not in _reachable(graph, v, adj):
            continue
        if sink not in _reachable(graph, producer, adj,
                                  skip_accept_from=frozenset({v})):
            return v, f"all {producer}~{sink} paths gated by {v}"
    approvals = frozenset(n.id for n in graph.nodes if n.type == NodeType.APPROVAL)
    if approvals and sink not in _reachable(graph, producer, adj,
                                            skip_nodes=approvals):
        return "__approval__", "human gate on every path"
    return None, "no gating verifier or approval on the path"


def _try_insert(graph: Graph, ctx: OptimizationContext, producer: str,
                sink_node, detail: str) -> tuple[Graph | None, str]:
    """Single-choke insertion P→V*→S. Returns (new_graph, verifier_id) or
    (None, advisory_reason). Every precondition is explicit; any doubt keeps
    the original graph."""
    from wisp.graph.types import EdgeMapping as _E, Graph as _G
    from wisp.graph.types import GraphNode as _GN, ModelPolicy as _MP
    from wisp.graph.types import NodeContract as _NC, NodeType as _NT
    nodes = {n.id: n for n in graph.nodes}
    # 1. Direct unconditional unmapped choke edge P→S must exist.
    choke = [e for e in graph.edges if e.from_node == producer
             and e.to_node == sink_node.id and not e.condition]
    if len(choke) != 1:
        return None, "no single direct edge to gate"
    if choke[0].mapping:
        return None, "declared dataflow on the edge; manual verification design needed"
    # 2. Sink must be plain gateable work (never control/signal topology).
    if sink_node.type not in (_NT.AGENT, _NT.FUNCTION):
        return None, f"sink type {sink_node.type.value} cannot be gated safely"
    if sink_node.id == graph.entrypoint:
        return None, "entrypoint runs unconditionally; gating is vacuous"
    others = [e for e in graph.edges if e.to_node == sink_node.id
              and not e.condition and e.from_node != producer]
    if others:
        return None, f"sink has {len(others)} other unconditional predecessors"
    # 3. No cycle risk: sink must not already reach the producer.
    if producer in _reachable(graph, sink_node.id, _adj(graph)):
        return None, "insertion would close a cycle"
    # 4. Host verifier profile, fully specified and authority-contained.
    prof, why = _profile_ok(ctx, graph)
    if prof is None:
        return None, why
    # 5. Deterministic collision-free verifier id.
    vid = f"{producer}__verify"
    if vid in nodes:
        return None, "verifier id collision"
    p_contract = nodes[producer].effective_contract()
    try:
        timeout = min(300.0, float(p_contract.timeout_s))
    except (TypeError, ValueError):
        timeout = 300.0
    verifier = _GN(
        id=vid, type=_NT.VERIFIER,
        contract=_NC(id=vid, name=vid,
                     description=f"Independent verification of {producer} output "
                                 f"gating {sink_node.id}: return ALLOW only with "
                                 f"evidence, else REJECT/RETRY/ESCALATE",
                     allowed_tools=tuple(prof["tools"]),
                     model_policy=_MP(model_class="standard",
                                      provider=prof["provider"], model=prof["model"]),
                     timeout_s=timeout, idempotent=True))
    edges = [e for e in graph.edges if not (e.from_node == producer
                                            and e.to_node == sink_node.id
                                            and not e.condition and not e.mapping)]
    edges.append(_E(producer, vid,
                    reason=f"{vid} reviews {producer} output for {sink_node.id}"))
    edges.append(_E(vid, sink_node.id,
                    reason=f"{sink_node.id} proceeds only on verified {producer} output",
                    condition="accept"))
    ordered = sorted(edges, key=lambda e: (e.from_node, e.to_node, e.condition))
    return _G(id=graph.id, version=graph.version, entrypoint=graph.entrypoint,
              nodes=tuple(list(graph.nodes) + [verifier]),
              edges=tuple(ordered), policies=graph.policies), vid


def _profile_ok(ctx: OptimizationContext, graph: Graph) -> tuple[dict | None, str]:
    """Verifier profile must be complete and ⊆ graph+policy authority."""
    from wisp.graph.validator import _TOOL_RX
    prof = ctx.verifier_profile
    if not isinstance(prof, dict):
        return None, "no host verifier profile configured"
    tools = prof.get("tools")
    model = prof.get("model")
    provider = prof.get("provider")
    if not tools or not isinstance(tools, list):
        return None, "verifier profile missing tools"
    if not model or not isinstance(model, str):
        return None, "verifier profile missing model"
    if not provider or not isinstance(provider, str):
        return None, "verifier profile missing provider"
    tools = [str(t)[:128] for t in tools[:32]]
    if any(_TOOL_RX.fullmatch(t) is None for t in tools):
        return None, "verifier profile has invalid tool names"
    from wisp.graph.optimizer import authority_of
    auth = authority_of(graph)
    if set(tools) - set(auth["tools"]):
        return None, "verifier tools exceed graph authority"
    if model not in auth["models"]:
        return None, "verifier model outside graph authority"
    if provider not in auth["providers"]:
        return None, "verifier provider outside graph authority"
    pol = ctx.policy
    if pol is not None:
        if "all" not in pol.allowed_tools and set(tools) - set(pol.allowed_tools):
            return None, "verifier tools exceed policy"
        if pol.allowed_models and model not in pol.allowed_models:
            return None, "verifier model exceeds policy"
        if pol.allowed_providers and provider not in pol.allowed_providers:
            return None, "verifier provider exceeds policy"
    return {"tools": tools, "model": model[:256], "provider": provider[:128]}, ""




def resource_pass(graph: Graph, ctx: OptimizationContext) -> PassResult:
    """OPT-004: fanout/resource analysis + provable concurrency clamp.

    The ONLY topology-adjacent mutation is lowering
    ``policies.max_concurrency`` — overlap-only, so outputs, failures,
    joins, verifiers, approvals, and mappings are untouched. Everything else
    (fanout shape, branch count, duplicate similarity) is advisory: fanout
    encodes independence/diversity/isolation, never mere cost.

    Clamp target = min(graph, active policy, node count), applied only when
    strictly lower AND wall-safe (no finite runtime budget the sequential
    worst case could newly trip). Verifiers/approvals/joins/routers are
    never modified — preservation holds structurally.
    """
    import dataclasses
    diagnostics: list = []
    downstream: dict[str, list] = {}
    inbound_join: dict[str, int] = {}
    nodes = {n.id: n for n in graph.nodes}
    for e in graph.edges:
        downstream.setdefault(e.from_node, []).append(e)
        if e.to_node in nodes and nodes[e.to_node].type == NodeType.JOIN:
            inbound_join[e.to_node] = inbound_join.get(e.to_node, 0) + 1
    for nid in sorted(downstream):
        outs = downstream[nid]
        width = len(outs)
        if width > ctx.max_fanout:
            diagnostics.append(diag(
                "OPT-004", "resource",
                f"fanout {width} at {nid} exceeds recommended {ctx.max_fanout}",
                "advisory only: fanout encodes independence; not serialized",
                node=nid, extra={"width": str(width)}))
        # Bounded retry amplification: branches × worst branch retries.
        t = 1
        for e in outs:
            if e.to_node not in nodes:
                continue
            try:
                t = max(t, int(nodes[e.to_node].effective_contract()
                               .retry_policy.max_attempts))
            except (TypeError, ValueError):
                continue
        amp = min(width * t, 10 ** 12)
        if width > 1 and t > 1:
            diagnostics.append(diag(
                "OPT-004", "resource",
                f"effective work envelope at {nid}: {width} branches × {t} attempts",
                "bounded; retries unchanged", node=nid,
                extra={"envelope": str(amp)}))
    for jid in sorted(inbound_join):
        if inbound_join[jid] > ctx.max_branches:
            diagnostics.append(diag(
                "OPT-004", "resource",
                f"join {jid} has {inbound_join[jid]} inbound branches "
                f"(recommended ≤ {ctx.max_branches})",
                "advisory only: join encodes completeness/quorum", node=jid))
    _duplicates(graph, nodes, diagnostics)
    # Provable clamp: min(graph, active policy, node count).
    current = graph.policies.max_concurrency
    target = min(current, len(graph.nodes))
    if ctx.policy is not None:
        target = min(target, ctx.policy.max_concurrency)
    target = max(1, min(target, 128))
    if target >= current:
        return PassResult(graph, changed=False, diagnostics=diagnostics)
    if not _wall_safe(graph, target):
        diagnostics.append(diag(
            "OPT-004", "resource",
            f"concurrency {current}->{target} withheld: finite runtime budget "
            f"the sequential worst case could trip",
            "advisory only", extra={"kept": str(current)}))
        return PassResult(graph, changed=False, diagnostics=diagnostics)
    new_policies = dataclasses.replace(graph.policies, max_concurrency=target)
    diagnostics.append(diag(
        "OPT-004", "resource",
        f"max_concurrency {current}->{target} (node-count/policy bound)",
        "overlap-only change; semantics preserved", extra={"kept": str(target)}))
    return PassResult(
        Graph(id=graph.id, version=graph.version, entrypoint=graph.entrypoint,
              nodes=graph.nodes, edges=graph.edges, policies=new_policies),
        changed=True, diagnostics=diagnostics)


def _wall_safe(graph: Graph, target: int) -> bool:
    """Reduction is wall-safe only with a proof: the serial total (sum of
    node timeouts, an upper bound on wall time at ANY concurrency ≥ 1) must
    fit the finite runtime budget. Sufficient, not necessary — when it fails,
    preserve + advisory. All timeouts are validated finite upstream."""
    budget = graph.policies.max_runtime_s
    if budget is None or target <= 0:
        return budget is None
    total = 0.0
    for n in graph.nodes:
        try:
            total += float(n.effective_contract().timeout_s)
        except (TypeError, ValueError):
            return False  # unprovable -> conservative
    return total <= float(budget)


def _duplicates(graph: Graph, nodes: dict, diagnostics: list) -> None:
    """Exact-duplicate branches: advisory ONLY (similarity is not proof;
    duplicates may encode intentional diversity/fault isolation)."""
    incoming: dict[str, list] = {}
    for e in graph.edges:
        incoming.setdefault(e.to_node, []).append(e)

    def key(nid: str) -> tuple:
        n = nodes[nid]
        c = n.effective_contract()
        mp = c.model_policy
        inc = sorted((e.from_node, tuple(sorted(e.mapping.items())), e.condition)
                     for e in incoming.get(nid, []))
        return (n.type.value, tuple(sorted(c.allowed_tools)),
                mp.model or "", mp.provider or "", tuple(inc))

    groups: dict[tuple, list[str]] = {}
    for nid in nodes:
        groups.setdefault(key(nid), []).append(nid)
    for members in groups.values():
        if len(members) > 1 and len(members) <= 64:
            a, b = sorted(members)[:2]
            diagnostics.append(diag(
                "OPT-004", "resource",
                f"structurally duplicated branches {a},{b} (+{len(members) - 2} more)",
                "advisory only: assumed intentional diversity; never merged",
                node=a, extra={"group": ",".join(sorted(members)[:8])}))


PASSES: dict[str, "PassFn"] = {
    "dependency": dependency_pass,
    "artifact_transport": artifact_transport_pass,
    "verification": verification_pass,
    "resource": resource_pass,
}
