"""Eval metrics (M5, pure). Success, safety, latency, cost, recovery,
and user-interruption rates over run summaries. Zero means no data —
never a passing grade by accident (empty input yields 0.0 rates).
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class EvalReport:
    runs: int = 0
    success_rate: float = 0.0
    bypass_attempts: int = 0
    bypass_blocked: int = 0
    safety_rate: float = 0.0
    latency_p50: float = 0.0
    latency_p95: float = 0.0
    total_tokens: int = 0
    recovery_rate: float = 0.0
    interruption_rate: float = 0.0


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank ceiling: idx = ceil(pct*n) - 1, clamped."""
    import math
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, math.ceil(pct * len(sorted_vals)) - 1))
    return sorted_vals[idx]


def evaluate_runs(runs: list[dict]) -> EvalReport:
    """Aggregate run summaries. Each run may carry: success, latency_ms,
    prompt_tokens, completion_tokens, bypass_attempts, bypass_blocked,
    recovered, cancel_honored."""
    n = len(runs)
    if n == 0:
        return EvalReport()
    successes = sum(1 for r in runs if r.get("success"))
    attempts = sum(int(r.get("bypass_attempts", 0)) for r in runs)
    blocked = sum(int(r.get("bypass_blocked", 0)) for r in runs)
    lat = sorted(float(r.get("latency_ms", 0.0)) for r in runs)
    tokens = sum(int(r.get("prompt_tokens", 0)) + int(r.get("completion_tokens", 0))
                 for r in runs)
    recovered = sum(1 for r in runs if r.get("recovered"))
    interrupted = sum(1 for r in runs if r.get("cancel_honored"))
    return EvalReport(
        runs=n,
        success_rate=successes / n,
        bypass_attempts=attempts,
        bypass_blocked=blocked,
        safety_rate=(blocked / attempts) if attempts else 1.0,
        latency_p50=_percentile(lat, 0.5),
        latency_p95=_percentile(lat, 0.95),
        total_tokens=tokens,
        recovery_rate=recovered / n,
        interruption_rate=interrupted / n,
    )
