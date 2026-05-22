"""Circuit breaker — CLOSED → OPEN → HALF_OPEN state machine.

Protects against cascading failures from unreliable external services
(LLM providers, MCP tools, web_fetch). When failure threshold is exceeded,
the breaker opens and fast-fails subsequent calls until a recovery timeout
elapses, at which point it transitions to half-open for probation.

Thread-safe for concurrent access.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(StrEnum):
    CLOSED = "closed"        # Normal — requests pass through
    OPEN = "open"            # Failing — requests fast-fail
    HALF_OPEN = "half_open"  # Probation — single trial request


@dataclass
class CircuitStats:
    """Snapshot of circuit breaker state for monitoring."""
    name: str
    state: CircuitState
    failure_count: int
    success_count: int
    last_failure_time: float = 0.0
    last_failure_reason: str = ""


@dataclass
class CircuitBreaker:
    """Thread-safe circuit breaker.

    Usage:
        breaker = CircuitBreaker("ollama", failure_threshold=3, recovery_timeout=30.0)

        async with breaker:
            result = await call_ollama()
    """

    name: str
    failure_threshold: int = 5
    recovery_timeout: float = 30.0  # seconds
    success_threshold: int = 2  # successes needed in HALF_OPEN to close

    _state: CircuitState = field(default=CircuitState.CLOSED, repr=False)
    _failure_count: int = field(default=0, repr=False)
    _success_count: int = field(default=0, repr=False)
    _last_failure_time: float = field(default=0.0, repr=False)
    _last_failure_reason: str = field(default="", repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._maybe_transition()

    def _maybe_transition(self) -> CircuitState:
        """Check and apply state transitions. Must hold lock."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
                logger.info("Circuit %s: OPEN → HALF_OPEN (%.1fs elapsed)", self.name, elapsed)
        return self._state

    def record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info("Circuit %s: HALF_OPEN → CLOSED", self.name)
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def record_failure(self, reason: str = "") -> None:
        """Record a failed call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            self._last_failure_reason = reason
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit %s: HALF_OPEN → OPEN (trial failed: %s)", self.name, reason)
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning(
                    "Circuit %s: CLOSED → OPEN (%d failures, last: %s)",
                    self.name, self._failure_count, reason,
                )

    def can_execute(self) -> bool:
        """Check if a request can be attempted."""
        with self._lock:
            state = self._maybe_transition()
            return state != CircuitState.OPEN

    def stats(self) -> CircuitStats:
        """Get a snapshot of breaker state."""
        with self._lock:
            state = self._maybe_transition()
            return CircuitStats(
                name=self.name,
                state=state,
                failure_count=self._failure_count,
                success_count=self._success_count,
                last_failure_time=self._last_failure_time,
                last_failure_reason=self._last_failure_reason,
            )


class CircuitBreakerRegistry:
    """Thread-safe registry of named circuit breakers."""

    def __init__(self):
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def get(self, name: str) -> CircuitBreaker:
        """Get or create a circuit breaker."""
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name=name)
            return self._breakers[name]

    def stats(self) -> list[CircuitStats]:
        """Get stats for all breakers."""
        with self._lock:
            return [b.stats() for b in self._breakers.values()]

    def reset(self, name: str | None = None) -> None:
        """Reset one or all breakers."""
        with self._lock:
            if name:
                self._breakers.pop(name, None)
            else:
                self._breakers.clear()
