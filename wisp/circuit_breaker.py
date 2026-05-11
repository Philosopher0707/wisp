"""Circuit breaker for Wisp tool calls.

Tracks consecutive failures per tool. After `failure_threshold` consecutive
failures, the breaker OPENs and the tool is temporarily disabled. After
`recovery_timeout` seconds, the breaker enters HALF_OPEN state and allows
one probe call. If the probe succeeds, CLOSED; otherwise OPEN again.

Used automatically by WispAgentCore before calling `execute_tool()`.

Usage::
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
    if not cb.is_open("web_search"):
        try:
            result = call_tool("web_search", ...)
            cb.record("web_search", success=True)
        except Exception as e:
            cb.record("web_search", success=False, error=str(e))
    else:
        return "Circuit breaker open for web_search"
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from logging import getLogger
from typing import Optional

logger = getLogger(__name__)


@dataclass
class _ToolState:
    """Per-tool breaker state."""
    state: str = "CLOSED"          # CLOSED | OPEN | HALF_OPEN
    failures: int = 0
    last_failure: float = 0.0      # monotonic timestamp
    last_error: str = ""
    total_failures: int = 0        # lifetime count
    total_successes: int = 0       # lifetime count


class CircuitBreaker:
    """Fail-fast for tools that are persistently failing.

    Args:
        failure_threshold: Consecutive failures before OPEN (default 3).
        recovery_timeout: Seconds before HALF_OPEN probe (default 60).
        half_open_successes: Successes needed to close from HALF_OPEN (default 1).
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
        half_open_successes: int = 1,
    ):
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.0, recovery_timeout)
        self.half_open_successes = max(1, half_open_successes)
        self._states: dict[str, _ToolState] = {}

    def record(self, tool_name: str, success: bool, error: str = "") -> None:
        """Record a tool execution outcome."""
        ts = time.monotonic()
        s = self._states.setdefault(tool_name, _ToolState())

        if success:
            s.total_successes += 1
            if s.state == "HALF_OPEN":
                s.state = "CLOSED"
                s.failures = 0
                s.last_error = ""
                logger.info("Circuit breaker for %s is now CLOSED", tool_name)
            else:
                s.failures = 0
                s.last_error = ""
        else:
            s.total_failures += 1
            s.failures += 1
            s.last_failure = ts
            s.last_error = error[:200]
            if s.state == "HALF_OPEN":
                s.state = "OPEN"
                logger.warning(
                    "Circuit breaker for %s OPENED again after HALF_OPEN probe failed: %s",
                    tool_name, error[:100]
                )
            elif s.state == "CLOSED" and s.failures >= self.failure_threshold:
                s.state = "OPEN"
                logger.warning(
                    "Circuit breaker for %s OPENED after %d consecutive failures: %s",
                    tool_name, s.failures, error[:100]
                )

    def is_open(self, tool_name: str) -> bool:
        """Return True if this tool is currently blocked (OPEN or HALF_OPEN still risky)."""
        s = self._states.get(tool_name)
        if s is None:
            return False

        if s.state == "CLOSED":
            return False

        if s.state == "OPEN":
            ts = time.monotonic()
            if ts - s.last_failure >= self.recovery_timeout:
                s.state = "HALF_OPEN"
                s.failures = 0
                logger.info(
                    "Circuit breaker for %s entering HALF_OPEN (one probe allowed)",
                    tool_name,
                )
                return False  # allow one probe call
            return True

        # HALF_OPEN: allow one probe
        return False

    def status(self, tool_name: str) -> str:
        """CLOSED, OPEN, or HALF_OPEN."""
        s = self._states.get(tool_name)
        return s.state if s else "CLOSED"

    def reset(self, tool_name: str = "") -> None:
        """Reset a specific tool or all tools if name is empty."""
        if tool_name:
            s = self._states.get(tool_name)
            if s:
                s.state = "CLOSED"
                s.failures = 0
                s.last_error = ""
                logger.info("Circuit breaker for %s manually reset", tool_name)
        else:
            for s in self._states.values():
                s.state = "CLOSED"
                s.failures = 0
                s.last_error = ""
            logger.info("All circuit breakers manually reset")

    def snapshot(self) -> dict:
        """JSON-serializable state snapshot."""
        return {
            name: {
                "state": s.state,
                "failures": s.failures,
                "total_failures": s.total_failures,
                "total_successes": s.total_successes,
                "last_error": s.last_error,
            }
            for name, s in self._states.items()
        }

    def summary(self) -> str:
        """Human-readable summary for REPL display."""
        open_tools = [
            name for name, s in self._states.items() if s.state == "OPEN"
        ]
        half_tools = [
            name for name, s in self._states.items() if s.state == "HALF_OPEN"
        ]
        parts: list[str] = []
        if open_tools:
            parts.append(f"OPEN: {', '.join(open_tools)}")
        if half_tools:
            parts.append(f"HALF_OPEN: {', '.join(half_tools)}")
        if not parts:
            return "All circuits CLOSED"
        return " | ".join(parts)
