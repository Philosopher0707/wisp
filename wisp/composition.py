"""CompositionRoot — the wiring layer.

Replaces: scattered instantiation across __main__.py, server.py, cli.py.
One place to create and wire all services.

Design:
  - Creates infrastructure services (store, security, extensions, telemetry)
  - Wires them into AgentRuntime
  - Manages lifecycle via ServiceRegistry
  - Single entry point for creating a fully configured runtime
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wisp.infra.store import UnifiedStore
from wisp.infra.security import SecurityPolicy
from wisp.infra.extensions import ExtensionHost
from wisp.infra.telemetry import Telemetry
from wisp.infra.lifecycle import ServiceRegistry
from wisp.core.runtime import AgentRuntime
from wisp.core.engine import WispAgentCore

logger = logging.getLogger(__name__)


@dataclass
class CompositionRoot:
    """Creates and wires all services."""

    config: Any

    def __post_init__(self):
        if self.config is None:
            raise TypeError("config is required")

        # Create infrastructure services
        db_path = getattr(self.config, "db_path", None)
        if db_path is None:
            workspace = getattr(self.config, "workspace", ".")
            db_path = Path(workspace) / ".wisp" / "wisp.db"
        self.store = UnifiedStore(db_path)
        self.security = SecurityPolicy(
            permission_mode=self.config.permission_mode,
        )
        self.extensions = ExtensionHost()
        self.telemetry = Telemetry()

        # Register built-in extensions
        from wisp.extensions import PluginExtension, HookExtension, MCPExtension, SkillExtension
        workspace = getattr(self.config, "workspace", ".")
        self.extensions.register(PluginExtension())
        self.extensions.register(HookExtension())
        self.extensions.register(MCPExtension(workspace=str(workspace)))
        self.extensions.register(SkillExtension(workspace=str(workspace)))

        # Create runtime with injected dependencies
        self.runtime = AgentRuntime(
            store=self.store,
            security=self.security,
            extensions=self.extensions,
            telemetry=self.telemetry,
            core_factory=self._create_core,
        )

        # Register services for lifecycle management
        self._registry = ServiceRegistry()
        self._registry.register(self.store)
        self._registry.register(self.extensions)
        self._registry.register(self.telemetry)

    def _create_core(self) -> WispAgentCore:
        """Factory for creating stateless core instances."""
        from wisp.providers.factory import ProviderFactory

        provider_name = getattr(self.config, "provider", None)
        if provider_name:
            factory = ProviderFactory()
            provider = factory.from_config(self.config)
        else:
            provider = _NullProvider()

        return WispAgentCore(
            config=self.config,
            provider=provider,
            security=self.security,
            extensions=self.extensions,
            telemetry=self.telemetry,
        )

    def start(self) -> None:
        """Start all services."""
        self._registry.start()

    def stop(self) -> None:
        """Stop all services."""
        self._registry.stop()

    def shutdown(self) -> None:
        """Shutdown all services (alias for stop)."""
        self.stop()


class _NullProvider:
    """Placeholder provider that yields nothing."""

    def generate_stream_events(self, **kwargs):
        return iter([])
