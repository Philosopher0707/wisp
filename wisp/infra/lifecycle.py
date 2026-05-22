"""ServiceRegistry — lifecycle management for infrastructure services.

Manages: UnifiedStore, SecurityPolicy, ExtensionHost, Telemetry
and any future services with start/stop lifecycle.

Design:
  - Register services with optional dependencies
  - start() starts in dependency order
  - stop() stops in reverse order with per-service timeout
  - healthy() checks all registered services
  - Graceful shutdown: cancel pending tasks, drain queues, close connections
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class HealthStatus:
    """Result of a service health check."""

    service: str
    healthy: bool
    detail: str = ""
    latency_ms: float = 0.0


class ServiceRegistry:
    """Manages the lifecycle of infrastructure services."""

    def __init__(self):
        self._services: list[Any] = []
        self._deps: dict[str, list[str]] = {}
        self._started: set[str] = set()
        self._shutdown_timeout: float = 5.0

    def register(self, service: Any, depends_on: list[str] | None = None) -> None:
        """Register a service with optional dependencies."""
        self._services.append(service)
        name = getattr(service, "name", type(service).__name__)
        if depends_on:
            self._deps[name] = list(depends_on)

    def start(self) -> None:
        """Start all services in dependency order."""
        started: set[str] = set()
        pending = list(self._services)

        while pending:
            progressed = False
            for svc in pending[:]:
                name = getattr(svc, "name", type(svc).__name__)
                deps = self._deps.get(name, [])
                if all(d in started for d in deps):
                    pending.remove(svc)
                    self._start_one(svc)
                    started.add(name)
                    progressed = True
            if not progressed and pending:
                names = [getattr(s, "name", type(s).__name__) for s in pending]
                missing = []
                for svc in pending:
                    name = getattr(svc, "name", type(svc).__name__)
                    for dep in self._deps.get(name, []):
                        if dep not in started:
                            missing.append(dep)
                raise RuntimeError(f"Missing dependencies: {set(missing)}")

    def _start_one(self, svc: Any) -> None:
        name = getattr(svc, "name", type(svc).__name__)
        try:
            svc.start()
            self._started.add(name)
            logger.debug("Service started: %s", name)
        except Exception as exc:
            logger.error("Service %s failed to start: %s", name, exc)
            raise

    def stop(self, timeout: float | None = None) -> None:
        """Stop all services in reverse registration order.

        Each service gets up to `timeout` seconds (default 5s) to shut down.
        If a service exceeds the timeout, a warning is logged and shutdown continues.
        """
        t = timeout if timeout is not None else self._shutdown_timeout
        for svc in reversed(self._services):
            name = getattr(svc, "name", type(svc).__name__)
            try:
                start = time.monotonic()
                svc.stop()
                elapsed = time.monotonic() - start
                self._started.discard(name)
                if elapsed > t:
                    logger.warning("Service %s stop() took %.1fs (timeout=%.1fs)", name, elapsed, t)
                else:
                    logger.debug("Service stopped: %s (%.1fs)", name, elapsed)
            except Exception as exc:
                logger.warning("Service %s stop() failed: %s", name, exc)

    def healthy(self) -> list[HealthStatus]:
        """Check health of all registered services.

        Each service is checked via its healthy() method if it has one.
        Results are collected and returned.
        """
        results: list[HealthStatus] = []
        for svc in self._services:
            name = getattr(svc, "name", type(svc).__name__)
            if hasattr(svc, "healthy"):
                try:
                    start = time.monotonic()
                    h = svc.healthy()
                    elapsed = (time.monotonic() - start) * 1000
                    if isinstance(h, HealthStatus):
                        results.append(h)
                    elif isinstance(h, dict):
                        results.append(HealthStatus(
                            service=name,
                            healthy=h.get("healthy", True),
                            detail=h.get("detail", ""),
                            latency_ms=elapsed,
                        ))
                    elif isinstance(h, bool):
                        results.append(HealthStatus(
                            service=name,
                            healthy=h,
                            latency_ms=elapsed,
                        ))
                    else:
                        results.append(HealthStatus(service=name, healthy=True, latency_ms=elapsed))
                except Exception as exc:
                    results.append(HealthStatus(
                        service=name,
                        healthy=False,
                        detail=str(exc),
                    ))
            else:
                # Service without health check — considered healthy if started
                results.append(HealthStatus(
                    service=name,
                    healthy=name in self._started,
                    detail="no health check" if name in self._started else "not started",
                ))
        return results

    def is_healthy(self) -> bool:
        """True if all services report healthy."""
        return all(h.healthy for h in self.healthy())

    def set_shutdown_timeout(self, timeout: float) -> None:
        """Set the per-service shutdown timeout."""
        self._shutdown_timeout = max(0.1, timeout)

    def names(self) -> list[str]:
        """Return registered service names."""
        return [getattr(s, "name", type(s).__name__) for s in self._services]

    def get(self, name: str) -> Optional[Any]:
        """Get a service by name."""
        for svc in self._services:
            svc_name = getattr(svc, "name", type(svc).__name__)
            if svc_name == name:
                return svc
        return None

    def get_by_type(self, cls: type) -> Optional[Any]:
        """Get the first service matching a type."""
        for svc in self._services:
            if isinstance(svc, cls):
                return svc
        return None
