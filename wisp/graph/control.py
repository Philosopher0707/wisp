"""Deterministic graph control: routing tables, join evaluation, splitting.

MODEL → CLASSIFICATION, CODE → AUTHORITY. The model returns a label; the
route table (code) decides the target. Joins are decision points, not bare
``await gather``. Merge helpers are deterministic (dedup/sort/set ops);
LLMs are only for semantic judgment.
"""

from __future__ import annotations

from typing import Any

from wisp.graph.types import JoinPolicy, NodeResult, NodeStatus


# ── Router ────────────────────────────────────────────────────────────

def resolve_route(routes: dict[str, str], default: str, label: str) -> str:
    """Deterministic label -> node lookup. Unknown labels never gain authority."""
    if label in routes:
        return routes[label]
    if "unknown" in routes and label not in routes:
        return routes["unknown"]
    return default


def classify_label(output: dict[str, Any], label_key: str = "label") -> str:
    value = output.get(label_key, "unknown")
    return str(value) if value else "unknown"


# ── Joins ─────────────────────────────────────────────────────────────

def evaluate_join(policy: JoinPolicy, param: int, results: dict[str, NodeResult]) -> tuple[bool, str]:
    """Decide whether a join releases. Returns (released, reason)."""
    settled = {k: v for k, v in results.items()}
    successes = {k: v for k, v in settled.items() if v.status == NodeStatus.SUCCESS}
    total = len(settled)
    if policy == JoinPolicy.ALL:
        if any(v.status != NodeStatus.SUCCESS for v in settled.values()):
            return False, "all: waiting for every branch to succeed"
        return (True, "all: every branch succeeded") if total else (False, "all: no branches settled")
    if policy == JoinPolicy.ANY:
        return (True, "any: first branch settled") if total else (False, "any: nothing settled")
    if policy == JoinPolicy.QUORUM:
        need = max(param, 1)
        if len(successes) >= need:
            return True, f"quorum: {len(successes)}/{need} successes"
        return False, f"quorum: {len(successes)}/{need} successes"
    if policy == JoinPolicy.MIN_SUCCESS:
        need = max(param, 1)
        if len(successes) >= need:
            return True, f"min_success: {len(successes)}>={need}"
        return False, f"min_success: {len(successes)}<{need}"
    if policy == JoinPolicy.BEST_EFFORT:
        return (True, "best_effort: all branches settled") if total else (False, "best_effort: waiting")
    if policy == JoinPolicy.STREAMING:
        return (True, "streaming: no barrier") if total else (False, "streaming: waiting for first")
    return False, f"unknown join policy {policy}"


# ── Merge (deterministic; never positional) ───────────────────────────

def merge_by_node(results: dict[str, NodeResult]) -> dict[str, NodeResult]:
    """Key merge by node id — never results[i]."""
    return dict(results)


def collect_successes(results: dict[str, NodeResult]) -> dict[str, dict[str, Any]]:
    return {k: v.output for k, v in results.items() if v.status == NodeStatus.SUCCESS}


def collect_failures(results: dict[str, NodeResult]) -> dict[str, str]:
    return {k: (v.message or v.error_code) for k, v in results.items()
            if v.status != NodeStatus.SUCCESS}


def dedupe_findings(findings: list[dict[str, Any]], key: str = "id") -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for f in findings:
        k = str(f.get(key, ""))
        if k and k in seen:
            continue
        seen.add(k)
        out.append(f)
    return sorted(out, key=lambda f: str(f.get(key, "")))


# ── Splitter ──────────────────────────────────────────────────────────

def split_by_items(items: list[Any], max_branches: int = 16) -> list[list[Any]]:
    """Partition independent items into branch slices (blast-radius splits)."""
    if not items or max_branches < 1:
        return []
    n = min(max_branches, len(items))
    slices: list[list[Any]] = [[] for _ in range(n)]
    for i, item in enumerate(items):
        slices[i % n].append(item)
    return [s for s in slices if s]


def detect_write_conflicts(write_sets: dict[str, set[str]]) -> list[str]:
    """File-overlap detection across branch write-sets (deterministic)."""
    owners: dict[str, str] = {}
    conflicts: list[str] = []
    for branch in sorted(write_sets):
        for path in sorted(write_sets[branch]):
            if path in owners and owners[path] != branch:
                conflicts.append(f"{path}: {owners[path]} vs {branch}")
            else:
                owners[path] = branch
    return conflicts
