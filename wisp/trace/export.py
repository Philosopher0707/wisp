"""Evidence export + replay plans (M5, pure over a TraceStore).

Spans are redacted at append, so export output is safe by construction —
export_evidence additionally asserts the invariant on its own output.
replay_plan returns the ordered tool-call sequence for --dry-run review;
it contains no executor reference and cannot execute.
"""
from __future__ import annotations
from typing import Any

from wisp.auth.secrets import scan_for_secrets


def export_evidence(store: Any, trace_id: str) -> dict[str, Any]:
    spans = store.query(trace_id)
    evident = [s.to_dict() for s in spans]
    blob = str(evident)
    leaks = scan_for_secrets(blob)
    assert not leaks, f"evidence leaks secrets: {leaks}"
    return {"trace_id": trace_id, "span_count": len(evident),
            "redacted": True, "version": 1, "spans": evident}


def replay_plan(store: Any, trace_id: str) -> list[dict[str, Any]]:
    """Ordered tool-call steps (dry-run only). seq counts tool steps;
    routing metadata (run_id) is excluded — only tool arguments replay."""
    plan = []
    for span in store.query(trace_id):
        if span.kind != "tool_call":
            continue
        args = {k: v for k, v in span.attrs.items() if k != "run_id"}
        plan.append({"seq": len(plan), "tool": span.name, "args": args})
    return plan
