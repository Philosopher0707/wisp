"""Graph trace — ASCII / JSON / DOT rendering + quality metrics. Pure functions."""

from __future__ import annotations

import json
import re
from typing import Any

_ANSI_RX = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][AB0]|\r")


def _safe(value: Any, max_len: int = 256) -> str:
    """Strip ANSI escapes / carriage returns so hostile node ids, error
    codes, and labels cannot inject terminal sequences into CLI output."""
    text = value if isinstance(value, str) else str(value)
    return _ANSI_RX.sub("", text).replace("\n", " ")[:max_len]


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    import math
    return value if math.isfinite(value) else default


def totals_from_nodes(nodes: Any) -> dict[str, Any]:
    """Tokens, cost and the unpriced nodes of a run, summed from its node results (the stored rows or the live results). Junk is zero and a negative number is not
    credit; a total is never read from anywhere but the nodes."""
    tokens_in = tokens_out = 0
    cost = 0.0
    unpriced: list[str] = []
    for nid, row in (nodes.items() if isinstance(nodes, dict) else ()):
        if not isinstance(row, dict):
            continue
        tokens_in += max(0, int(_num(row.get("input_tokens", 0))))
        tokens_out += max(0, int(_num(row.get("output_tokens", 0))))
        cost += max(0.0, _num(row.get("cost_usd", 0)))
        if row.get("unpriced") is True:
            unpriced.append(str(nid))
    return {"tokens": {"input": tokens_in, "output": tokens_out}, "cost_usd": round(cost, 6), "unpriced_nodes": unpriced}


_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


def trace_from_store(store: Any, run_id: str) -> dict[str, Any] | None:
    """The trace of a stored run, with the real totals: the one builder behind `/graph trace`, `wisp graph trace|inspect|metrics`. None for an unknown run."""
    import json

    row = store.get_run(run_id)
    if row is None:
        return None
    nodes: dict[str, Any] = {}
    for n in store.node_runs(run_id):
        try:
            payload = json.loads(n["result"] or "{}")
        except (ValueError, TypeError):
            payload = {"status": "corrupt"}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        # Stored outputs win over row columns on conflict.
        nodes[n["node_id"]] = {"status": n["status"], "attempt": n["attempt"], **payload}
    wall = 0.0
    if row.get("status") in _TERMINAL:
        wall = round(max(0.0, _num(row.get("updated_at", 0)) - _num(row.get("created_at", 0))), 2)
    return {"graph_id": row["graph_id"], "run_id": row["run_id"], "status": row["status"], "results_by_node": nodes, "wall_s": wall, **totals_from_nodes(nodes)}


def _cost_text(trace: dict[str, Any]) -> str:
    cost = _num(trace.get("cost_usd", 0))
    unpriced = trace.get("unpriced_nodes")
    unpriced = [_safe(n, 64) for n in unpriced[:16]] if isinstance(unpriced, list) else []
    if not unpriced:
        return f"cost ${cost}"
    known = f"${cost} + " if cost > 0 else ""
    return f"cost {known}unpriced: {', '.join(unpriced)} (no price for the model)"


def render_ascii(trace: dict[str, Any]) -> str:
    if not isinstance(trace, dict):
        return "(invalid trace)"
    lines = [f"Graph: {_safe(trace.get('graph_id'))}  Run: {_safe(trace.get('run_id'))}  "
             f"Status: {_safe(trace.get('status'))}"]
    results = trace.get("results_by_node", {})
    if not isinstance(results, dict):
        return "\n".join(lines)
    for nid in sorted(results, key=str):
        r = results[nid] if isinstance(results[nid], dict) else {}
        mark = "✓" if r.get("status") == "success" else "✗"
        model = r.get("model")
        spent_in, spent_out = int(_num(r.get("input_tokens", 0))), int(_num(r.get("output_tokens", 0)))
        lines.append(f"{mark} {_safe(nid)}  {_num(r.get('duration_s', 0)):.1f}s"
                     + (f"  ({_safe(model, 64)})" if isinstance(model, str) and model else "")
                     + (f"  {spent_in} in / {spent_out} out" if spent_in or spent_out else "")
                     + (f"  [{_safe(r.get('error_code'))}]" if r.get("error_code") else ""))
    tokens = trace.get("tokens", {}) if isinstance(trace.get("tokens"), dict) else {}
    estimated = " (estimated)" if any(_num(v) > 0 for v in tokens.values()) else ""
    lines.append(f"wall {_num(trace.get('wall_s', 0))}s  "
                 f"tokens {_safe(tokens, 512)}{estimated}  {_cost_text(trace)}")
    return "\n".join(lines)


def render_json(trace: dict[str, Any]) -> str:
    return json.dumps(trace, indent=2, default=str)


def render_dot(graph_def: dict[str, Any]) -> str:
    if not isinstance(graph_def, dict):
        return 'digraph "invalid" {}'
    lines = [f'digraph "{_dot_id(str(graph_def.get("id", "graph")))}" {{']
    nodes = graph_def.get("nodes", [])
    edges = graph_def.get("edges", [])
    for n in nodes[:4096]:
        lines.append(f'  "{_dot_id(str(n))}";')
    for e in edges[:8192]:
        if not isinstance(e, (list, tuple)) or len(e) < 2:
            continue
        label = f' [label="{_dot_id(str(e[2]))}"]' if len(e) > 2 and e[2] else ""
        lines.append(f'  "{_dot_id(str(e[0]))}" -> "{_dot_id(str(e[1]))}"{label};')
    lines.append("}")
    return "\n".join(lines)


def _dot_id(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")[:256]


def quality_metrics(trace: dict[str, Any]) -> dict[str, Any]:
    """parallelism_factor = sum(node_time) / wall_time, plus failure/retry rates."""
    if not isinstance(trace, dict):
        return {}
    results = trace.get("results_by_node", {})
    if not isinstance(results, dict):
        return {}
    rows = [r for r in results.values() if isinstance(r, dict)]
    node_time = sum(_num(r.get("duration_s", 0)) for r in rows)
    wall = _num(trace.get("wall_s", 0))
    failed = sum(1 for r in rows if r.get("status") != "success")
    retried = sum(max(int(_num(r.get("attempt", 1), 1)) - 1, 0) for r in rows)
    tokens = trace.get("tokens", {}) if isinstance(trace.get("tokens"), dict) else {}
    return {
        "parallelism_factor": round(node_time / wall, 2) if wall > 0 else 0.0,
        "node_count": len(rows),
        "branch_failure_rate": round(failed / len(rows), 3) if rows else 0.0,
        "retry_rate": round(retried / len(rows), 3) if rows else 0.0,
        "token_amplification": {str(k)[:64]: _num(v) for k, v in list(tokens.items())[:16]},
        "cost_per_node": round(_num(trace.get("cost_usd", 0)) / len(rows), 6) if rows else 0.0,
    }
