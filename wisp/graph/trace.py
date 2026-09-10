"""Graph trace — ASCII / JSON / DOT rendering + quality metrics. Pure functions."""

from __future__ import annotations

import json
from typing import Any


def render_ascii(trace: dict[str, Any]) -> str:
    lines = [f"Graph: {trace.get('graph_id')}  Run: {trace.get('run_id')}  "
             f"Status: {trace.get('status')}"]
    results = trace.get("results_by_node", {})
    for nid in sorted(results):
        r = results[nid]
        mark = "✓" if r.get("status") == "success" else "✗"
        lines.append(f"{mark} {nid}  {r.get('duration_s', 0):.1f}s"
                     + (f"  [{r.get('error_code')}]" if r.get("error_code") else ""))
    lines.append(f"wall {trace.get('wall_s', 0)}s  "
                 f"tokens {trace.get('tokens', {})}  cost ${trace.get('cost_usd', 0)}")
    return "\n".join(lines)


def render_json(trace: dict[str, Any]) -> str:
    return json.dumps(trace, indent=2, default=str)


def render_dot(graph_def: dict[str, Any]) -> str:
    lines = [f'digraph "{graph_def.get("id", "graph")}" {{']
    for n in graph_def.get("nodes", []):
        lines.append(f'  "{n}";')
    for e in graph_def.get("edges", []):
        label = f' [label="{e[2]}"]' if len(e) > 2 and e[2] else ""
        lines.append(f'  "{e[0]}" -> "{e[1]}"{label};')
    lines.append("}")
    return "\n".join(lines)


def quality_metrics(trace: dict[str, Any]) -> dict[str, Any]:
    """parallelism_factor = sum(node_time) / wall_time, plus failure/retry rates."""
    results = trace.get("results_by_node", {})
    node_time = sum(r.get("duration_s", 0) for r in results.values())
    wall = trace.get("wall_s", 0) or 0.0
    failed = sum(1 for r in results.values() if r.get("status") != "success")
    retried = sum(max(r.get("attempt", 1) - 1, 0) for r in results.values())
    return {
        "parallelism_factor": round(node_time / wall, 2) if wall > 0 else 0.0,
        "node_count": len(results),
        "branch_failure_rate": round(failed / len(results), 3) if results else 0.0,
        "retry_rate": round(retried / len(results), 3) if results else 0.0,
        "token_amplification": trace.get("tokens", {}),
        "cost_per_node": round(trace.get("cost_usd", 0) / len(results), 6) if results else 0.0,
    }
