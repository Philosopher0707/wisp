"""ServiceRegistry — lifecycle management for infrastructure services.

Manages: UnifiedStore, SecurityPolicy, ExtensionHost, Telemetry
and any future services with start/stop lifecycle.

Design:
  - Register services with optional dependencies
  - start() starts in dependency order, then registration order
  - stop() stops in reverse registration order
  - Broken services are logged but don't crash the registry
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Type

logger = logging.getLogger(__name__)


class ServiceRegistry:
    """Manages the lifecycle of infrastructure services."""

    def __init__(self):
        self._services: list[Any] = []
        self._deps: dict[str, list[str]] = {}
        self._started: set[str] = set()

    def register(self, service: Any, depends_on: list[str] | None = None) -> None:
        """Register a service with optional dependencies."""
        self._services.append(service)
        name = getattr(service, "name", type(service).__name__)
        if depends_on:
            self._deps[name] = list(depends_on)

    def start(self) -> None:
        """Start all services in dependency order."""
        # Topological sort by dependencies
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

    def stop(self) -> None:
        """Stop all services in reverse registration order."""
        for svc in reversed(self._services):
            name = getattr(svc, "name", type(svc).__name__)
            try:
                svc.stop()
                self._started.discard(name)
                logger.debug("Service stopped: %s", name)
            except Exception as exc:
                logger.warning("Service %s stop() failed: %s", name, exc)

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

    def get_by_type(self, cls: Type) -> Optional[Any]:
        """Get the first service matching a type."""
        for svc in self._services:
            if isinstance(svc, cls):
                return svc
        return None
