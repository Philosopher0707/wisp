"""Telemetry — structured observability for Wisp.

Replaces: AgentMetrics (in-memory counters) + ad-hoc logging
with structured metrics, health checks, and tracing.

Design:
  - Thread-safe counters using threading.Lock
  - Health checks based on error rates and thresholds
  - JSON-serializable snapshots for export
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Telemetry:
    """Observability for the agent."""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _start_time: float = field(default_factory=time.time, repr=False)

    # Turn counters
    turns_total: int = 0
    turn_latency_ms_sum: float = 0.0
    prompt_tokens_total: int = 0
    completion_tokens_total: int = 0

    # Tool counters
    tool_calls_total: int = 0
    tool_errors_total: int = 0
    tool_duration_ms_sum: float = 0.0

    # Health thresholds
    error_rate_threshold: float = 0.5  # 50% errors = degraded

    def record_turn(self, latency_ms: float, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            self.turns_total += 1
            self.turn_latency_ms_sum += latency_ms
            self.prompt_tokens_total += prompt_tokens
            self.completion_tokens_total += completion_tokens

    def record_tool(self, name: str, duration_ms: float, success: bool = True) -> None:
        with self._lock:
            self.tool_calls_total += 1
            self.tool_duration_ms_sum += duration_ms
            if not success:
                self.tool_errors_total += 1

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            avg_latency = (
                self.turn_latency_ms_sum / self.turns_total if self.turns_total else 0.0
            )
            avg_tool_duration = (
                self.tool_duration_ms_sum / self.tool_calls_total if self.tool_calls_total else 0.0
            )
            error_rate = (
                self.tool_errors_total / self.tool_calls_total if self.tool_calls_total else 0.0
            )
            success_rate = (1 - error_rate) * 100

            return {
                "turns_total": self.turns_total,
                "turn_latency_ms_avg": round(avg_latency, 1),
                "prompt_tokens_total": self.prompt_tokens_total,
                "completion_tokens_total": self.completion_tokens_total,
                "tool_calls_total": self.tool_calls_total,
                "tool_errors_total": self.tool_errors_total,
                "tool_duration_ms_avg": round(avg_tool_duration, 1),
                "tool_success_rate": round(success_rate, 1),
            }

    def check_health(self) -> dict[str, Any]:
        metrics = self.metrics()
        error_rate = metrics["tool_errors_total"] / max(metrics["tool_calls_total"], 1)
        uptime = time.time() - self._start_time

        if error_rate >= self.error_rate_threshold and metrics["tool_calls_total"] >= 5:
            return {
                "status": "degraded",
                "reason": f"error_rate={error_rate:.0%} exceeds threshold",
                "uptime_seconds": round(uptime, 1),
            }

        return {
            "status": "healthy",
            "reason": "",
            "uptime_seconds": round(uptime, 1),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "turns": {
                "total": self.turns_total,
                "avg_latency_ms": self.metrics()["turn_latency_ms_avg"],
                "prompt_tokens": self.prompt_tokens_total,
                "completion_tokens": self.completion_tokens_total,
            },
            "tools": {
                "total": self.tool_calls_total,
                "errors": self.tool_errors_total,
                "avg_duration_ms": self.metrics()["tool_duration_ms_avg"],
                "success_rate": self.metrics()["tool_success_rate"],
            },
            "health": self.check_health(),
            "timestamp": time.time(),
        }

    def start(self) -> None:
        """Lifecycle start — metrics are already initialized."""
        logger.debug("Telemetry started")

    def stop(self) -> None:
        """Lifecycle stop — flush any pending metrics."""
        logger.debug("Telemetry stopped")
