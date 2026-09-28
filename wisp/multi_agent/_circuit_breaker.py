"""Production circuit breaker for subagent execution.

Opens after a threshold of consecutive failures within a time window.
Tripped state must be reset manually (no auto-recovery).

Thread-safe via asyncio locks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Circuit breaker settings."""

    failure_threshold: int = 3        # Consecutive failures to trip
    window_seconds: float = 120.0     # Rolling window for failures
    cooldown_seconds: float = 30.0  # Minimum seconds before manual reset is honoured


class CircuitBreaker:
    """Async-safe circuit breaker for subagent failures.

    When open, all calls are rejected immediately with a descriptive error.
    """

    def __init__(self, config: CircuitBreakerConfig | None = None):
        self.config = config or CircuitBreakerConfig()
        self._failures: list[float] = []
        self._tripped_at: float | None = None
        self._lock = asyncio.Lock()

    async def call(self, fn) -> any:
        """Execute ``fn`` if breaker is closed, else raise."""
        async with self._lock:
            if self._is_open():
                raise CircuitBreakerOpenError(
                    f"Subagent circuit breaker OPEN since {self._tripped_at:.1f}s ago. "
                    "At least 3 consecutive failures in the last 120s."
                )
        try:
            result = await fn()
            await self._record_success()
            return result
        except Exception:
            await self._record_failure()
            raise

    async def _record_success(self) -> None:
        async with self._lock:
            self._failures.clear()

    async def _record_failure(self) -> None:
        now = time.monotonic()
        async with self._lock:
            self._failures.append(now)
            # Trim outside window
            cutoff = now - self.config.window_seconds
            self._failures = [t for t in self._failures if t > cutoff]
            if len(self._failures) >= self.config.failure_threshold:
                self._tripped_at = now
                logger.warning(
                    "Circuit breaker TRIPPED after %d failures within %.0fs",
                    len(self._failures), self.config.window_seconds,
                )

    def _is_open(self) -> bool:
        if self._tripped_at is None:
            return False
        # Manual reset only; cooldown just prevents accidental immediate reset
        return True

    async def reset(self) -> bool:
        """Manually reset the breaker. Returns True if reset was allowed."""
        async with self._lock:
            if self._tripped_at is None:
                return False
            elapsed = time.monotonic() - self._tripped_at
            if elapsed < self.config.cooldown_seconds:
                logger.info(
                    "Circuit breaker reset blocked (cooldown %.1f / %.1fs)",
                    elapsed, self.config.cooldown_seconds,
                )
                return False
            self._failures.clear()
            self._tripped_at = None
            logger.info("Circuit breaker manually RESET")
            return True

    @property
    def state(self) -> str:
        return "OPEN" if self._tripped_at is not None else "CLOSED"


class CircuitBreakerOpenError(Exception):
    """Raised when a call is rejected because the circuit breaker is open."""
