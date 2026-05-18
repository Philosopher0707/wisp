"""Telemetry — collect and aggregate subagent execution metrics."""

from __future__ import annotations

import time
from typing import Any

from .task import SubagentResult


class Telemetry:
    """Collect per-model telemetry: latency, success rate, token usage."""

    def __init__(self):
        self._records: dict[str, list[dict]] = {}

    def record(
        self,
        model: str,
        result: SubagentResult,
    ) -> None:
        """Record a single subagent execution."""
        self._records.setdefault(model, []).append({
            "task_id": result.task_id,
            "success": result.success,
            "elapsed_seconds": result.elapsed_seconds,
            "tokens_used": result.tokens_used,
            "timestamp": time.time(),
        })

    def get(self) -> dict[str, list[dict]]:
        """Return raw telemetry records."""
        return {k: list(v) for k, v in self._records.items()}

    def summary(self) -> dict[str, dict[str, Any]]:
        """Return aggregated telemetry per model."""
        summary = {}
        for model, records in self._records.items():
            if not records:
                continue
            latencies = [r["elapsed_seconds"] for r in records]
            successes = [r["success"] for r in records]
            tokens = [r["tokens_used"] for r in records]
            summary[model] = {
                "count": len(records),
                "success_rate": sum(successes) / len(successes),
                "avg_latency": sum(latencies) / len(latencies),
                "max_latency": max(latencies),
                "total_tokens": sum(tokens),
            }
        return summary

    def aggregate(self, results: list[SubagentResult]) -> dict[str, dict[str, Any]]:
        """Calculate telemetry summary purely from a given batch of results without storing them."""
        grouped: dict[str, list[SubagentResult]] = {}
        for result in results:
            model = result.model_used or "unknown"
            grouped.setdefault(model, []).append(result)
            
        summary = {}
        for model, runs in grouped.items():
            if not runs:
                continue
            latencies = [r.elapsed_seconds for r in runs]
            successes = [r.success for r in runs]
            tokens = [r.tokens_used for r in runs]
            summary[model] = {
                "count": len(runs),
                "success_rate": sum(successes) / len(successes),
                "avg_latency": sum(latencies) / len(latencies),
                "max_latency": max(latencies),
                "total_tokens": sum(tokens),
            }
        return summary

    def clear(self) -> None:
        """Clear all telemetry."""
        self._records.clear()
