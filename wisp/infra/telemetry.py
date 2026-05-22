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

    # Tool counters (aggregate)
    tool_calls_total: int = 0
    tool_errors_total: int = 0
    tool_duration_ms_sum: float = 0.0

    # Labeled per-tool counters: tool_calls_by_name["read_file"] = 42
    tool_calls_by_name: dict[str, int] = field(default_factory=dict)
    tool_errors_by_name: dict[str, int] = field(default_factory=dict)
    tool_durations_by_name: dict[str, list[float]] = field(default_factory=dict)

    # Turn latency histogram (buckets in ms)
    _latency_buckets: list[float] = field(default_factory=list, repr=False)

    # Health thresholds
    error_rate_threshold: float = 0.5  # 50% errors = degraded

    def record_turn(self, latency_ms: float, prompt_tokens: int, completion_tokens: int) -> None:
        with self._lock:
            self.turns_total += 1
            self.turn_latency_ms_sum += latency_ms
            self.prompt_tokens_total += prompt_tokens
            self.completion_tokens_total += completion_tokens
            self._latency_buckets.append(latency_ms)
            # Keep last 1000 samples for histogram
            if len(self._latency_buckets) > 1000:
                self._latency_buckets = self._latency_buckets[-1000:]

    def record_tool(self, name: str, duration_ms: float, success: bool = True) -> None:
        with self._lock:
            self.tool_calls_total += 1
            self.tool_duration_ms_sum += duration_ms
            if not success:
                self.tool_errors_total += 1
            # Per-tool labeled counters
            self.tool_calls_by_name[name] = self.tool_calls_by_name.get(name, 0) + 1
            if not success:
                self.tool_errors_by_name[name] = self.tool_errors_by_name.get(name, 0) + 1
            self.tool_durations_by_name.setdefault(name, []).append(duration_ms)
            # Cap per-tool duration lists
            if len(self.tool_durations_by_name[name]) > 100:
                self.tool_durations_by_name[name] = self.tool_durations_by_name[name][-100:]

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

            # Latency histogram (p50/p95/p99)
            latency = {}
            if self._latency_buckets:
                sorted_lat = sorted(self._latency_buckets)
                n = len(sorted_lat)
                latency["p50_ms"] = round(sorted_lat[n // 2], 1)
                latency["p95_ms"] = round(sorted_lat[int(n * 0.95)], 1)
                latency["p99_ms"] = round(sorted_lat[int(n * 0.99)], 1)
                latency["samples"] = n

            # Per-tool stats
            per_tool: dict[str, dict] = {}
            for name in self.tool_calls_by_name:
                total = self.tool_calls_by_name[name]
                errors = self.tool_errors_by_name.get(name, 0)
                durs = self.tool_durations_by_name.get(name, [])
                avg_dur = sum(durs) / len(durs) if durs else 0.0
                per_tool[name] = {
                    "calls": total,
                    "errors": errors,
                    "avg_duration_ms": round(avg_dur, 1),
                }

            return {
                "turns_total": self.turns_total,
                "turn_latency_ms_avg": round(avg_latency, 1),
                "turn_latency": latency,
                "prompt_tokens_total": self.prompt_tokens_total,
                "completion_tokens_total": self.completion_tokens_total,
                "tool_calls_total": self.tool_calls_total,
                "tool_errors_total": self.tool_errors_total,
                "tool_duration_ms_avg": round(avg_tool_duration, 1),
                "tool_success_rate": round(success_rate, 1),
                "per_tool": per_tool,
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
        m = self.metrics()
        return {
            "turns": {
                "total": self.turns_total,
                "avg_latency_ms": m["turn_latency_ms_avg"],
                "prompt_tokens": self.prompt_tokens_total,
                "completion_tokens": self.completion_tokens_total,
            },
            "tools": {
                "total": self.tool_calls_total,
                "errors": self.tool_errors_total,
                "avg_duration_ms": m["tool_duration_ms_avg"],
                "success_rate": m["tool_success_rate"],
            },
            "turn_latency": m.get("turn_latency", {}),
            "per_tool": m.get("per_tool", {}),
            "health": self.check_health(),
            "timestamp": time.time(),
        }

    def start(self) -> None:
        """Lifecycle start — metrics are already initialized."""
        logger.debug("Telemetry started")

    def stop(self) -> None:
        """Lifecycle stop — flush any pending metrics."""
        logger.debug("Telemetry stopped")


# ── Structured logging setup ────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """JSON log lines with trace context injected on every record."""

    def format(self, record: logging.LogRecord) -> str:
        import json
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        payload = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "trace_id", ""),
            "span_id": getattr(record, "span_id", ""),
            "session_id": getattr(record, "session_id", ""),
        }
        if record.exc_info and record.exc_info[1]:
            payload["error"] = str(record.exc_info[1])
        return json.dumps(payload, default=str)


def setup_structured_logging(level: int = logging.INFO) -> None:
    """Install JSON log format with trace context injection.

    Call once at startup. Adds the TraceLogFilter to all handlers
    and switches the root logger to JSON output.
    """
    from wisp.infra.tracing import TraceLogFilter

    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers, install JSON handler
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    handler.addFilter(TraceLogFilter())
    root.addHandler(handler)


# ── Metrics export ─────────────────────────────────────────────────

def export_metrics(telemetry: Telemetry, path: str | None = None) -> str:
    """Export telemetry snapshot as JSON.

    Args:
        telemetry: The Telemetry instance to snapshot.
        path: Optional file path. If None, writes to
            ``~/.config/wisp/metrics.json``.

    Returns:
        The file path written to.
    """
    import json
    from pathlib import Path

    if path is None:
        path = str(Path.home() / ".config" / "wisp" / "metrics.json")

    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    snapshot = telemetry.snapshot()
    dest.write_text(json.dumps(snapshot, indent=2, default=str))
    return str(dest)
