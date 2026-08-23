"""Circuit breaker pattern for provider resilience.

Prevents cascade failures when provider is unhealthy by failing fast
and allowing recovery after a timeout.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Failing fast, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 5          # Consecutive failures before opening
    success_threshold: int = 2          # Successes in half-open before closing
    recovery_timeout: float = 30.0      # Seconds before attempting recovery
    excluded_exceptions: tuple[type[Exception], ...] = (KeyboardInterrupt, asyncio.CancelledError)


@dataclass
class CircuitBreaker:
    """Async circuit breaker for protecting downstream services."""

    config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    @property
    def state(self) -> CircuitState:
        """Current circuit state, checking for auto-transition to half-open."""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.config.recovery_timeout:
                return CircuitState.HALF_OPEN
        return self._state

    async def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Execute a function with circuit breaker protection.

        Args:
            func: Async or sync callable to execute
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func(*args, **kwargs)

        Raises:
            CircuitOpenError: If circuit is open and not in recovery
            Original exception: If func fails and circuit doesn't open
        """
        async with self._lock:
            if self.state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"Circuit breaker OPEN — failing fast. "
                    f"Recovery in {self.config.recovery_timeout - (time.monotonic() - self._last_failure_time):.1f}s"
                )

        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
        except self.config.excluded_exceptions:
            raise
        except Exception:
            await self._on_failure()
            raise
        else:
            await self._on_success()
            return result

    async def stream(
        self, generator: Callable[..., AsyncIterator[T]], *args: Any, **kwargs: Any
    ) -> AsyncIterator[T]:
        """Stream from an async iterator with circuit breaker protection.

        The circuit opens on the FIRST error from the generator.
        """
        async with self._lock:
            if self.state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"Circuit breaker OPEN — failing fast. "
                    f"Recovery in {self.config.recovery_timeout - (time.monotonic() - self._last_failure_time):.1f}s"
                )

        try:
            async for item in generator(*args, **kwargs):
                yield item
            await self._on_success()
        except self.config.excluded_exceptions:
            raise
        except Exception:
            await self._on_failure()
            raise

    async def _on_success(self) -> None:
        """Handle successful call."""
        async with self._lock:
            current_state = self.state  # Use computed state (handles OPEN->HALF_OPEN transition)
            if current_state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info("Circuit breaker CLOSED — service recovered")
            elif current_state == CircuitState.CLOSED:
                self._failure_count = 0  # Reset on success
            # If OPEN, success doesn't count (we're failing fast)

    async def _on_failure(self) -> None:
        """Handle failed call."""
        async with self._lock:
            self._last_failure_time = time.monotonic()

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in half-open goes back to open
                self._state = CircuitState.OPEN
                self._success_count = 0
                logger.warning("Circuit breaker OPEN — failure during recovery")
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.config.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        "Circuit breaker OPEN after %d consecutive failures. "
                        "Recovery in %.1fs",
                        self._failure_count, self.config.recovery_timeout
                    )


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and rejecting calls."""
    pass


# Global circuit breaker for provider calls (can be replaced for testing)
_default_breaker: Optional[CircuitBreaker] = None


def get_default_breaker() -> CircuitBreaker:
    """Get or create the default circuit breaker."""
    global _default_breaker
    if _default_breaker is None:
        _default_breaker = CircuitBreaker()
    return _default_breaker


def set_default_breaker(breaker: CircuitBreaker) -> None:
    """Set a custom default circuit breaker (for testing)."""
    global _default_breaker
    _default_breaker = breaker