"""Metrics transport — collects performance metrics from events.

Implements Transport ABC for observability. Tracks:
  - Turn counts and latency
  - Token estimates (prompt/completion)
  - Tool call success/failure rates
  - Error counts

Usage with MultiTransport:
    metrics = MetricsTransport()
    multi = MultiTransport([CLITransport(runtime), metrics])
    # After run:
    snapshot = metrics.snapshot()
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


class MetricsTransport(Transport):
    """Transport that collects performance metrics from events.

    No I/O — pure in-memory counters. Designed to be composed
    with other transports via MultiTransport.
    """

    def __init__(self):
        self.turns = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.content_chars = 0
        self.thinking_chars = 0
        self.errors = 0
        self._turn_start: float | None = None
        self._latency_ms = 0.0
        self._started = False

    def start(self) -> None:
        """Reset counters."""
        self.turns = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.content_chars = 0
        self.thinking_chars = 0
        self.errors = 0
        self._latency_ms = 0.0
        self._started = True
        logger.debug("MetricsTransport started")

    def stop(self) -> None:
        self._started = False
        logger.debug("MetricsTransport stopped")

    async def send(self, event: dict) -> None:
        """Collect metrics from an event."""
        etype = event.get("type")
        if etype == "content":
            text = event.get("text", "")
            self.content_chars += len(text)
        elif etype == "thinking":
            text = event.get("text", "")
            self.thinking_chars += len(text)
        elif etype == "tool_call":
            self.tool_calls += 1
        elif etype == "tool_result":
            if event.get("error") or "error" in str(event.get("result", "")).lower():
                self.tool_errors += 1
        elif etype == "error":
            self.errors += 1
        elif etype == "done":
            self.turns += 1
            if self._turn_start is not None:
                self._latency_ms += (time.time() - self._turn_start) * 1000
                self._turn_start = None

        # Start timing on first event of a turn
        if self._turn_start is None and etype != "done":
            self._turn_start = time.time()

    async def recv(self) -> str | None:
        """Metrics transport does not receive user input."""
        return None

    async def approve(self, tool_call: dict) -> bool:
        """Auto-approve all tool calls."""
        return True

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serializable dict of current metrics."""
        avg_latency = self._latency_ms / self.turns if self.turns else 0.0
        tool_success_rate = (
            (1 - self.tool_errors / self.tool_calls) * 100
            if self.tool_calls else 100.0
        )
        return {
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "tool_success_rate": round(tool_success_rate, 1),
            "content_chars": self.content_chars,
            "thinking_chars": self.thinking_chars,
            "errors": self.errors,
            "avg_latency_ms": round(avg_latency, 1),
            "total_latency_ms": round(self._latency_ms, 1),
        }

    def reset(self) -> None:
        """Reset all counters."""
        self.turns = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.content_chars = 0
        self.thinking_chars = 0
        self.errors = 0
        self._latency_ms = 0.0
