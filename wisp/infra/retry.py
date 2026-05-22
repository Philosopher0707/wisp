"""Retry with exponential backoff and jitter.

Pluggable retry strategy for async operations. Applies to provider calls,
MCP tools, web_fetch, and any fallible I/O.

Design:
  - async retry with configurable base delay, max delay, max attempts
  - Full jitter (prevents thundering herd)
  - Optional callback for circuit breaker integration
  - Optional predicate for retryable vs terminal errors
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for the retry strategy."""

    max_attempts: int = 3
    base_delay: float = 1.0  # seconds
    max_delay: float = 30.0  # seconds
    jitter: bool = True
    # Called on each retry: on_retry(attempt, exception) → None
    on_retry: Optional[Callable[[int, Exception], None]] = None
    # Predicate: returns True if error is retryable, False if terminal
    retryable_predicate: Optional[Callable[[Exception], bool]] = None


@dataclass
class RetryStats:
    """Outcome of a retry operation."""

    attempts: int = 0
    total_delay: float = 0.0
    success: bool = False
    last_error: Optional[Exception] = None


async def retry_async(
    operation: Callable[[], Awaitable[Any]],
    config: RetryConfig | None = None,
    circuit_breaker: Any = None,  # CircuitBreaker
) -> Any:
    """Execute an async operation with retry and exponential backoff.

    Args:
        operation: Async callable to execute.
        config: Retry configuration.
        circuit_breaker: Optional circuit breaker to integrate with.

    Returns:
        The operation's return value.

    Raises:
        The last exception if all attempts are exhausted.
    """
    cfg = config or RetryConfig()
    stats = RetryStats()
    last_error: Optional[Exception] = None

    for attempt in range(1, cfg.max_attempts + 1):
        stats.attempts = attempt

        if circuit_breaker is not None and not circuit_breaker.can_execute():
            raise RuntimeError(f"Circuit breaker '{circuit_breaker.name}' is OPEN")

        try:
            result = await operation()
            if circuit_breaker is not None:
                circuit_breaker.record_success()
            stats.success = True
            return result
        except Exception as exc:
            last_error = exc
            is_retryable = True
            if cfg.retryable_predicate is not None:
                is_retryable = cfg.retryable_predicate(exc)

            if not is_retryable:
                logger.debug("Non-retryable error, giving up: %s", exc)
                if circuit_breaker is not None:
                    circuit_breaker.record_failure(str(exc))
                raise

            if attempt >= cfg.max_attempts:
                logger.warning("All %d attempts exhausted: %s", cfg.max_attempts, exc)
                if circuit_breaker is not None:
                    circuit_breaker.record_failure(str(exc))
                break

            delay = _compute_delay(attempt, cfg)
            stats.total_delay += delay

            if cfg.on_retry:
                try:
                    cfg.on_retry(attempt, exc)
                except Exception:
                    pass

            logger.debug("Retry %d/%d after %.2fs: %s", attempt, cfg.max_attempts, delay, exc)
            await asyncio.sleep(delay)

    stats.last_error = last_error
    raise last_error  # type: ignore[misc]


def _compute_delay(attempt: int, config: RetryConfig) -> float:
    """Compute delay with exponential backoff and optional jitter."""
    delay = min(config.base_delay * (2 ** (attempt - 1)), config.max_delay)
    if config.jitter:
        delay = delay * (0.5 + random.random())  # Full jitter: 50-150% of computed delay
    return delay


def terminal_on_http_4xx(exc: Exception) -> bool:
    """Retryable predicate: 4xx client errors are terminal, others retryable."""
    status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
    if status is not None and 400 <= status < 500:
        return False
    return True
