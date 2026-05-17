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
        """Auto-aggregate telemetry from a batch of results."""
        for result in results:
            if result.model_used:
                self._records.setdefault(result.model_used, []).append({
                    "elapsed_seconds": result.elapsed_seconds,
                    "success": result.success,
                    "tokens_used": result.tokens_used,
                    "timestamp": time.time(),
                })
        return self.summary()

    def clear(self) -> None:
        """Clear all telemetry."""
        self._records.clear()
