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
        lines.append(f"{mark} {_safe(nid)}  {_num(r.get('duration_s', 0)):.1f}s"
                     + (f"  [{_safe(r.get('error_code'))}]" if r.get("error_code") else ""))
    tokens = trace.get("tokens", {}) if isinstance(trace.get("tokens"), dict) else {}
    lines.append(f"wall {_num(trace.get('wall_s', 0))}s  "
                 f"tokens {_safe(tokens, 512)}  cost ${_num(trace.get('cost_usd', 0))}")
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
