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

# Pass registry: fixed deterministic order. 10D+ append here.
PASS_ORDER: tuple[str, ...] = ("dependency", "artifact_transport")
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

    def __post_init__(self) -> None:
        if not (1024 <= self.inline_threshold_bytes <= 4_000_000):
            raise ValueError("inline_threshold_bytes must be 1KB..4MB")


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
    """optimized_authority ⊆ original_authority. Returns (ok, reason)."""
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
    if set(after["nodes"]) - set(before["nodes"]):
        return False, "nodes added"
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
    available = passes or optimizer_passes.PASSES
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
