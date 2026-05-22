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
from wisp.infra.audit import ImmutableAuditTrail
from wisp.infra.extensions import ExtensionHost
from wisp.infra.telemetry import Telemetry
from wisp.infra.lifecycle import ServiceRegistry
from wisp.core.runtime import AgentRuntime
from wisp.core.engine import WispAgentCore
from wisp.core.compaction import Compactor
from wisp.tool_executor import ToolExecutor
from wisp.tools.registry import ToolRegistry
from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

logger = logging.getLogger(__name__)


@dataclass
class CompositionRoot:
    """Creates and wires all services."""

    config: Any

    def __post_init__(self):
        if self.config is None:
            raise TypeError("config is required")

        # Structured logging (before any services log)
        log_format = getattr(self.config, "log_format", None) or "text"
        if log_format == "json":
            from wisp.infra.telemetry import setup_structured_logging
            setup_structured_logging()

        # Create infrastructure services
        db_path = getattr(self.config, "db_path", None)
        if db_path is None:
            workspace = getattr(self.config, "workspace", ".")
            db_path = Path(workspace) / ".wisp" / "wisp.db"
        self.store = UnifiedStore(db_path)
        self.audit_trail = ImmutableAuditTrail(self.store)
        self.security = SecurityPolicy(
            permission_mode=self.config.permission_mode,
            _audit_trail=self.audit_trail,
        )
        self.extensions = ExtensionHost()
        self.telemetry = Telemetry()

        # Configure shared thread pool (before any async work starts)
        import asyncio
        from wisp.async_utils import get_shared_executor, set_shared_executor_size
        pool_size = getattr(self.config, "thread_pool_size", None) or 8
        try:
            set_shared_executor_size(pool_size)
        except RuntimeError:
            pass  # already configured
        # Register as default asyncio executor so asyncio.to_thread() uses it
        loop = None
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if loop is not None:
            loop.set_default_executor(get_shared_executor())

        # Register built-in extensions
        from wisp.extensions import PluginExtension, HookExtension, MCPExtension, SkillExtension
        from wisp.mcp import MCPManager
        from wisp.file_lock import FileLock
        from wisp.infra.hook_types import HookManager
        workspace = getattr(self.config, "workspace", ".")
        wsp = Path(workspace).resolve()

        # Create HookManager — shared between HookExtension (intercept) and ToolExecutor (hooks)
        self._hook_manager = HookManager(workspace=str(workspace))

        # Create MCPManager — shared between MCPExtension (tools) and ToolExecutor (dispatch)
        self._mcp_manager = MCPManager(str(workspace))

        self.extensions.register(PluginExtension())
        self.extensions.register(HookExtension(manager=self._hook_manager))
        self.extensions.register(MCPExtension(workspace=str(workspace), manager=self._mcp_manager))
        self.extensions.register(SkillExtension(workspace=str(workspace)))

        # Create subagent orchestrator
        self.subagent_orchestrator = SubagentOrchestrator(
            config=self.config,
            workspace=wsp,
        )

        # Ensure .wisp dir exists for persistence
        (wsp / ".wisp").mkdir(parents=True, exist_ok=True)

        # Create FileLock for concurrent file operation safety
        self._file_lock = FileLock(str(workspace))

        # Create LSPManager for language server operations (singleton — server endpoints share this instance)
        from wisp.lsp.manager import get_lsp_manager
        self._lsp_manager = get_lsp_manager(str(workspace))

        # Create ToolRegistry (shared state with module-level TOOL_SCHEMAS/TOOL_IMPLS)
        self.tool_registry = ToolRegistry()

        # Create ToolExecutor wired with all dependencies
        self.tool_executor = ToolExecutor(
            config=self.config,
            hook_manager=self._hook_manager,
            mcp=self._mcp_manager,
            file_lock=self._file_lock,
            lsp_manager=self._lsp_manager,
            subagent_orchestrator=self.subagent_orchestrator,
        )

        # Create Compactor for LLM-powered summarization
        compaction_model = getattr(self.config, "compaction_model", "") or ""
        self.compactor = Compactor(
            provider_factory=self._create_compaction_provider,
            compaction_model=compaction_model,
            chars_per_token=getattr(self.config, "chars_per_token", 4),
        )

        # Create runtime with injected dependencies
        self.runtime = AgentRuntime(
            store=self.store,
            security=self.security,
            extensions=self.extensions,
            telemetry=self.telemetry,
            core_factory=self._create_core,
            compactor=self.compactor,
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
            tool_executor=self.tool_executor,
        )

    def _create_compaction_provider(self, model: str):
        """Create a provider for compaction summarization.

        Returns None if model is empty or provider creation fails,
        signalling the Compactor to use truncation fallback.
        """
        if not model:
            return None
        try:
            from wisp.providers.factory import ProviderFactory
            factory = ProviderFactory()
            provider = factory.create(
                "ollama",
                config=self.config,
                base_url=getattr(self.config, "ollama_url", "http://localhost:11434"),
                model=model,
            )
            return provider
        except Exception:
            logger.warning("Failed to create compaction provider for model=%s", model, exc_info=True)
            return None

    def start(self) -> None:
        """Start all services."""
        self._registry.start()

    def stop(self) -> None:
        """Stop all services."""
        self._registry.stop()

    def shutdown(self) -> None:
        """Shutdown all services gracefully with timeouts."""
        self.stop()
        try:
            from wisp.infra.telemetry import export_metrics
            export_metrics(self.telemetry)
        except Exception:
            pass
        try:
            self._lsp_manager.shutdown_all()
        except Exception:
            pass
        try:
            from wisp.async_utils import shutdown_background_loop, _SHARED_EXECUTOR
            shutdown_background_loop(timeout=5.0)
            if _SHARED_EXECUTOR is not None:
                _SHARED_EXECUTOR.shutdown(wait=False)
        except Exception:
            pass

    def health(self) -> list:
        """Check health of all services."""
        return self._registry.healthy()

    def is_healthy(self) -> bool:
        """True if all services are healthy."""
        return self._registry.is_healthy()


class _NullProvider:
    """Placeholder provider that yields nothing."""

    def generate_stream_events(self, **kwargs):
        return iter([])
