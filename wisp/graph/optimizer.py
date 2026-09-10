"""Deterministic graph optimization passes (Phase 10A–10C).

Compiler passes, not agents: pure, local, zero model calls. Pipeline:

    validate → passes (fixed order) → revalidate → fingerprint

Passes are plain functions (no framework classes beyond a result record).
Authority rule: a pass may only narrow. The driver enforces
``optimized_authority ⊆ original_authority`` mechanically and drops any
pass that violates it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from wisp.graph.types import Graph, GraphPolicy, NodeType
from wisp.graph.validator import validate_graph

logger = logging.getLogger(__name__)

# Pass registry: fixed deterministic order. 10E+ append here.
PASS_ORDER: tuple[str, ...] = ("dependency", "artifact_transport", "verification",
                               "resource")
MAX_PASSES = 1  # single pipeline run; no fixed-point loop (cycle protection)


@dataclass(frozen=True)
class OptimizationContext:
    """Minimum optimizer context. No providers, no I/O, no credentials."""

    policy: GraphPolicy | None = None
    inline_threshold_bytes: int = 65536
    # Host-supplied static size hints: (from_node, to_node, dst_path) -> bytes.
    # Absent hint = UNKNOWN size -> conservative (inline + advisory).
    size_hints: dict[tuple[str, str, str], int] = field(default_factory=dict)
    max_diagnostics: int = 256
    # Host-supplied verifier profile for OPT-002 insertion. Absent keys or
    # any value outside graph+policy authority -> advisory only, never insert.
    # Shape: {"tools": [...], "model": "...", "provider": "..."}.
    verifier_profile: dict[str, Any] | None = None
    # Host resource thresholds for OPT-004 (bounded, deterministic).
    max_fanout: int = 64
    max_branches: int = 128

    def __post_init__(self) -> None:
        if not (1024 <= self.inline_threshold_bytes <= 4_000_000):
            raise ValueError("inline_threshold_bytes must be 1KB..4MB")
        if not (1 <= self.max_fanout <= 1024):
            raise ValueError("max_fanout must be 1..1024")
        if not (1 <= self.max_branches <= 4096):
            raise ValueError("max_branches must be 1..4096")


@dataclass
class PassResult:
    graph: Graph
    changed: bool = False
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    # Pass-specific metadata (e.g. transport plans). Bounded, scrubbed.
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class OptimizationResult:
    status: str = "UNCHANGED"  # UNCHANGED | OPTIMIZED | REJECTED
    graph: Graph | None = None
    passes_applied: list[str] = field(default_factory=list)
    diagnostics: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    error: str = ""


PassFn = Callable[[Graph, OptimizationContext], PassResult]


def diag(code: str, type: str, reason: str, action: str = "",
         node: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bounded machine-readable diagnostic."""
    d: dict[str, Any] = {"code": code[:16], "type": type[:32],
                         "severity": "advisory", "node": str(node)[:128],
                         "reason": str(reason)[:500], "action": str(action)[:500]}
    if extra:
        d["detail"] = {str(k)[:64]: str(v)[:256] for k, v in list(extra.items())[:8]}
    return d


# ── envelopes (§36): KNOWN values or the UNKNOWN sentinel ─────────────

UNKNOWN = "unknown"
_AMP_CAP = 10 ** 12


def structural_envelope(graph: Graph) -> tuple[int, int, int]:
    """S(G) = (N node executions, E edges, M mappings)."""
    return (len(graph.nodes), len(graph.edges),
            sum(len(e.mapping) for e in graph.edges))


def resource_envelope(graph: Graph, policy: GraphPolicy | None = None) -> dict[str, Any]:
    """R(G) = (F, C, T, B, X). X is always UNKNOWN: the graph model cannot
    prove a tool-execution envelope (never treat UNKNOWN as zero)."""
    downstream: dict[str, int] = {}
    for e in graph.edges:
        downstream[e.from_node] = downstream.get(e.from_node, 0) + 1
    f = max(downstream.values()) if downstream else 0
    cap = graph.policies.max_concurrency
    if policy is not None:
        cap = min(cap, policy.max_concurrency)
    t = 1
    for n in graph.nodes:
        try:
            t = max(t, int(n.effective_contract().retry_policy.max_attempts))
        except (TypeError, ValueError):
            continue
    p = graph.policies
    return {"F": f, "C": cap, "T": t,
            "B": {"max_tokens": p.max_tokens, "max_cost_usd": p.max_cost_usd,
                  "max_runtime_s": p.max_runtime_s},
            "X": UNKNOWN}


def _budget_le(b: Any, a: Any) -> bool:
    """Budget dimension ≤ with None meaning unbounded (+∞)."""
    bi = float("inf") if b is None else b
    ai = float("inf") if a is None else a
    try:
        return bool(ai <= bi)
    except TypeError:
        return False


def envelope_le(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str]:
    """Component-wise R(G') ≤ R(G). UNKNOWN→UNKNOWN is equal; KNOWN→UNKNOWN
    (or any larger KNOWN) is NOT proven."""
    for key in ("F", "C", "T"):
        b, a = before.get(key), after.get(key)
        if not isinstance(b, int) or not isinstance(a, int):
            return False, f"resource dimension {key} not comparable"
        if a > b:
            return False, f"resource dimension {key} grew {b}->{a}"
    bb, ab = before.get("B", {}), after.get("B", {})
    for key in ("max_tokens", "max_cost_usd", "max_runtime_s"):
        if not _budget_le(bb.get(key), ab.get(key)):
            return False, f"budget dimension {key} grew"
    bx, ax = before.get("X"), after.get("X")
    if bx != ax:
        # X is UNKNOWN in this implementation; any drift (including a pass
        # inventing a tool envelope) is unproven by construction.
        return False, "resource dimension X changed"
    return True, ""


def authority_of(graph: Graph) -> dict[str, Any]:
    """Comparable authority surface of a graph (for monotonicity)."""
    tools: set[str] = set()
    models: set[str] = set()
    providers: set[str] = set()
    for n in graph.nodes:
        c = n.effective_contract()
        tools.update(c.allowed_tools)
        mp = c.model_policy
        models.add(mp.model or f"class:{mp.model_class}")
        providers.add(mp.provider or "default")
    p = graph.policies
    return {"tools": sorted(tools), "models": sorted(models),
            "providers": sorted(providers), "workspace": p.workspace,
            "max_tokens": p.max_tokens, "max_cost_usd": p.max_cost_usd,
            "max_runtime_s": p.max_runtime_s,
            "max_concurrency": p.max_concurrency, "max_retries": p.max_retries,
            "max_nodes": p.max_nodes,
            "nodes": sorted(n.id for n in graph.nodes),
            "approval_nodes": sorted(n.id for n in graph.nodes
                                     if n.type == NodeType.APPROVAL)}


def is_narrower_or_equal(before: dict[str, Any], after: dict[str, Any]) -> tuple[bool, str]:
    """optimized_authority ⊆ original_authority. Returns (ok, reason).

    Structural node changes (add/remove) are allowed — revalidation governs
    structure. What must never widen: tools, models, providers, workspace,
    budgets, and approval topology (human-authority semantics).
    """
    for key in ("tools", "models", "providers"):
        extra = set(after[key]) - set(before[key])
        if extra:
            return False, f"authority widened: {key} +{sorted(extra)[:5]}"
    if after["workspace"] != before["workspace"]:
        return False, "workspace changed"
    for key in ("max_tokens", "max_cost_usd", "max_runtime_s",
                "max_concurrency", "max_retries", "max_nodes"):
        b, a = before[key], after[key]
        if b is not None and (a is None or a > b):
            return False, f"budget/limit widened: {key} {b}->{a}"
    if set(after["approval_nodes"]) - set(before["approval_nodes"]):
        return False, "approval requirements changed"
    if len(after["approval_nodes"]) < len(before["approval_nodes"]):
        return False, "approval node removed"
    return True, ""


def optimize_graph(graph: Graph, policy: GraphPolicy | None = None,
                   context: OptimizationContext | None = None,
                   passes: dict[str, PassFn] | None = None) -> OptimizationResult:
    """Validate → ordered passes → monotonicity gate → revalidate."""
    from wisp.graph import optimizer_passes  # local import: pass registry
    errors = validate_graph(graph)
    if errors:
        return OptimizationResult(status="REJECTED", error="; ".join(errors[:5]))
    ctx = context or OptimizationContext(policy=policy)
    available = optimizer_passes.PASSES if passes is None else passes
    original_auth = authority_of(graph)
    current = graph
    applied: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    for _ in range(MAX_PASSES):
        for name in PASS_ORDER:
            fn = available.get(name)
            if fn is None:
                continue
            try:
                res = fn(current, ctx)
            except Exception as exc:
                logger.exception("optimizer pass %s failed", name)
                return OptimizationResult(status="REJECTED",
                                          error=f"pass {name} failed: {exc}")
            diagnostics.extend(res.diagnostics[:ctx.max_diagnostics])
            meta[name] = res.meta
            if not res.changed:
                continue
            ok, reason = is_narrower_or_equal(original_auth, authority_of(res.graph))
            if not ok:
                diagnostics.append(diag("OPT-REJECT", "authority", reason,
                                        "pass output dropped; original kept", node=name))
                try:
                    _audit_opt_reject(graph, name, reason)
                except Exception:
                    pass
                continue  # drop this pass's changes, keep prior passes
            ok, reason = envelope_le(resource_envelope(current, ctx.policy),
                                     resource_envelope(res.graph, ctx.policy))
            if not ok:
                diagnostics.append(diag("OPT-REJECT", "resource", reason,
                                        "pass output dropped; original kept", node=name))
                try:
                    _audit_opt_reject(graph, name, reason)
                except Exception:
                    pass
                continue
            current = res.graph
            applied.append(name)
    errors = validate_graph(current)
    if errors:
        try:
            _audit_opt_reject(graph, "revalidate", "; ".join(errors[:3]))
        except Exception:
            pass
        return OptimizationResult(status="REJECTED",
                                  error="revalidation: " + "; ".join(errors[:5]),
                                  diagnostics=diagnostics, meta=meta)
    if not applied:
        return OptimizationResult(status="UNCHANGED", graph=current,
                                  diagnostics=diagnostics, meta=meta)
    return OptimizationResult(status="OPTIMIZED", graph=current,
                              passes_applied=applied,
                              diagnostics=diagnostics, meta=meta)


def _audit_opt_reject(graph: Graph, where: str, reason: str) -> None:
    from wisp.graph.audit import GraphSecurityAuditor
    GraphSecurityAuditor().emit(
        "graph.optimization_rejected", graph_id=graph.id,
        graph_hash=graph.fingerprint(), allowed=False,
        reason=f"{where}: {reason[:400]}")
