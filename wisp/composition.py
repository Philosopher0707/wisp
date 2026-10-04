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

import pathlib

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


def load_organization_policy(config: Any) -> Any:
    """ADR-0058 R1–R3. The organization policy bundle, or `None` when unconfigured.

    **The single load site** (ADR-0006). `None` when `WISP_POLICY_BUNDLE` is
    empty: absence is a configuration, and the runtime is then byte-for-byte
    today's behaviour — `ToolExecutor.policy` is `None` and `authorize()`'s L0
    receives nothing (R2). A bundle that is named but cannot be read, has no
    `.sig` sibling, or fails verification **raises**, so the process does not
    start with an expected control missing (R3). An expired-but-verifying bundle
    is served *trimmed* by the loader, not refused (R4).

    `policy_pubkey` is the key itself, base64 — not a path (ADR-0058 §Problem).
    """
    # Only a string is a configured path — `WispConfig` declares the setting as `str`. A config
    # that does not declare it (a `MagicMock` or an older test double answers `getattr` with a
    # truthy non-string) is ABSENT, R2's inert case; stringifying it would try to load a path
    # named after the object's repr.
    bundle_path = getattr(config, "policy_bundle", "")
    if not isinstance(bundle_path, str) or not bundle_path:
        return None
    from wisp.policy.loader import load_local

    pubkey = getattr(config, "policy_pubkey", "")
    return load_local(bundle_path, pubkey if isinstance(pubkey, str) else "")


@dataclass
class CompositionRoot:
    """Creates and wires all services."""

    config: Any

    def __post_init__(self):
        if self.config is None:
            raise TypeError("config is required")

        # Stream hygiene: isolate provider diagnostics to .agent/runtime.log
        # so user stdout stays clean (rich tables + badges). Idempotent.
        try:
            from agent.logger import install as _install_agent_logger  # type: ignore
            _install_agent_logger()
        except Exception:
            pass
        # Disk sink for run_bash: full logs → .agent/logs/ before UI truncation
        try:
            from agent.tools.runner import install_sink as _install_sink  # type: ignore
            _install_sink()
        except Exception:
            pass
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
            autonomous=bool(getattr(self.config, "autonomous", False)),
            _audit_trail=self.audit_trail,
        )
        # autonomous=True silently relaxes AUTO_EDIT to FULL (see
        # SecurityPolicy.effective_permission_mode) — nothing else reports
        # that escalation, so it must be logged here or it is invisible.
        if self.security.autonomous and self.security.permission_mode != self.security.effective_permission_mode:
            logger.warning(
                "WISP_AUTONOMOUS is relaxing permission_mode=%s to effective "
                "%s — every write/bash/git tool now runs without approval",
                self.security.permission_mode, self.security.effective_permission_mode,
            )
        self.extensions = ExtensionHost()
        # ── The four run bounds, and the meter that makes one of them fire ──
        #
        # `wisp/configs/default.yaml` is the Config layer — "the only place
        # capabilities are named" — and until now NOTHING READ IT. A layer that is
        # not loaded is not a layer, so this is the load.
        #
        # ONE meter, shared: `Telemetry` charges it and `ToolExecutor` gates on it.
        # Two meters would measure and gate different things, which is the defect
        # class this repository names most often.
        #
        # `FAIL_CLOSED` is affordable here only because `try_charge` COUNTS an
        # unpriceable model instead of raising: a new model name degrades the meter
        # (visibly, in `summary()["uncharged_calls"]`), not the run.
        from wisp.runtime.bounds import BoundsError, load as _load_bounds
        from wisp.runtime.cost import CostMeter, UnknownPolicy

        _bounds_path = pathlib.Path(__file__).resolve().parent / "configs" / "default.yaml"
        try:
            self.run_bounds = _load_bounds(_bounds_path)
        except BoundsError as _exc:
            # Loud, not silent: a run whose ceiling cannot be read has no ceiling,
            # and that must be visible rather than assumed.
            logger.warning(
                "no run bounds loaded from %s (%s) — the cost bound is INACTIVE",
                _bounds_path, _exc)
            self.run_bounds = None
        self.cost_meter = CostMeter(on_unknown=UnknownPolicy.FAIL_CLOSED)

        self.telemetry = Telemetry(cost_meter=self.cost_meter)
        # Durable run registry (migration P0). Built here — before its two
        # consumers (ToolExecutor's lazy background-manager fallback and the
        # BackgroundAgentManager below) — so exactly one instance exists per
        # root and the rollback flag has a single decision point.
        self.run_store = self._create_run_store()
        # Durable span sink (migration P0). Same construction doctrine as the
        # run store: owned here, one instance per root, gated by one flag.
        self.trace_store = self._create_trace_store()

        # Configure shared thread pool (before any async work starts)
        import asyncio
        from wisp.async_utils import set_shared_executor_size
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
            from wisp.async_utils import non_owning_executor
            loop.set_default_executor(non_owning_executor())

        # Register built-in extensions
        from wisp.extensions import PluginExtension, HookExtension, MCPExtension, SkillExtension
        from wisp.mcp import MCPManager
        from wisp.file_lock import FileLock
        from wisp.infra.hook_types import InterceptHookManager, ToolHookManager
        from agent.tools.batch_reader import BatchReaderExtension  # type: ignore
        workspace = getattr(self.config, "workspace", ".")
        wsp = Path(workspace).resolve()

        # Create separate hook managers for intercept and tool execution paths
        self._intercept_hook_manager = InterceptHookManager(workspace=str(workspace))
        self._tool_hook_manager = ToolHookManager(workspace=str(workspace))

        # Create MCPManager — shared between MCPExtension (tools) and ToolExecutor (dispatch)
        self._mcp_manager = MCPManager(str(workspace))

        self.extensions.register(PluginExtension())
        self.extensions.register(HookExtension(manager=self._intercept_hook_manager))
        self.extensions.register(MCPExtension(workspace=str(workspace), manager=self._mcp_manager))
        self.extensions.register(SkillExtension(workspace=str(workspace)))
        self.extensions.register(BatchReaderExtension())

        # Ensure .wisp dir exists for persistence
        (wsp / ".wisp").mkdir(parents=True, exist_ok=True)

        # Create FileLock for concurrent file operation safety
        self._file_lock = FileLock(str(workspace))

        # Create LSPManager for language server operations (singleton — server endpoints share this instance)
        from wisp.lsp.manager import get_lsp_manager
        self._lsp_manager = get_lsp_manager(str(workspace))

        # Create ToolRegistry (shared state with module-level TOOL_SCHEMAS/TOOL_IMPLS)
        self.tool_registry = ToolRegistry()

        # The organization policy layer, loaded ONCE, here — the single load site
        # ADR-0006 names. `None` when unconfigured; raises when a bundle is named
        # but unverifiable, which is ADR-0058 R3's refusal to boot.
        #
        # ADR-0059 R5: held on the root so the *second* consumer — the REST gate —
        # reads this same instance instead of loading one of its own. One load
        # site, two readers; not two authorities.
        self.organization_policy = load_organization_policy(self.config)

        # Create ToolExecutor first (subagent_orchestrator wired below).
        self.tool_executor = ToolExecutor(
            config=self.config,
            hook_manager=self._tool_hook_manager,
            mcp=self._mcp_manager,
            file_lock=self._file_lock,
            lsp_manager=self._lsp_manager,
            subagent_orchestrator=None,
            extensions=self.extensions,
            run_store=self.run_store,
            policy=self.organization_policy,
            # The gate. Both are needed for it to be active; `max_cost_usd` comes
            # from the loaded bounds so the ceiling is the CONFIG's, not a literal
            # buried here.
            cost_meter=self.cost_meter,
            max_cost_usd=(self.run_bounds.max_cost_usd
                          if self.run_bounds is not None else None),
        )

        # Create Compactor for LLM-powered summarization
        compaction_model = getattr(self.config, "compaction_model", "") or ""
        self.compactor = Compactor(
            provider_factory=self._create_compaction_provider,
            compaction_model=compaction_model,
            chars_per_token=getattr(self.config, "chars_per_token", 4),
        )

        # Create runtime before orchestrator so orchestrator can receive it
        from wisp.core.session_repo import SessionRepository
        session_repo = SessionRepository(self.store)

        self.runtime = AgentRuntime(
            store=self.store,
            security=self.security,
            extensions=self.extensions,
            telemetry=self.telemetry,
            core_factory=self._create_core,
            compactor=self.compactor,
            orchestrator=None,
            session_repo=session_repo,
            config=self.config,
            trace_store=self.trace_store,
        )

        # Create subagent orchestrator with tool_executor wired at construction time
        self.subagent_orchestrator = SubagentOrchestrator(
            config=self.config,
            workspace=wsp,
            tool_executor=self.tool_executor,
            hook_manager=self._tool_hook_manager,
            agent_runtime=self.runtime,
            store=self.store,
        )

        # Budget enforcement at admission: the ceiling must reach the
        # orchestrator or its check() compares against None forever.
        # getattr-tolerant because test configs may be partial.
        self.subagent_orchestrator.set_global_token_budget(
            getattr(self.config, "subagent_token_budget", 0) or None
        )

        # Wire orchestrator back into runtime and tool_executor for spawn/fanout dispatch
        self.runtime.orchestrator = self.subagent_orchestrator
        self.tool_executor.subagent_orchestrator = self.subagent_orchestrator

        # Background agents share the orchestrator's execution path; the
        # manager only tracks lifecycle and continuation between turns.
        #
        # Migration P0: the manager has always accepted a `run_store` and
        # fully implements durable persistence (create/transition/lease),
        # durable admission via Scheduler, and crash recovery — but both
        # production construction sites passed no store, so every
        # `_persist_*` returned early at background.py:165-166,188-189 and
        # the Scheduler was never built. The durable layer was complete,
        # tested, and unreachable. Wire it here, at the composition root,
        # which is the single place that owns service construction.
        from wisp.multi_agent.background import BackgroundAgentManager
        self.background_agents = BackgroundAgentManager(
            self.subagent_orchestrator, run_store=self.run_store)
        self.tool_executor.background_agents = self.background_agents
        # Reachable from slash commands via runtime.orchestrator.
        self.subagent_orchestrator.background_agents = self.background_agents
        # One shared ring set: blocking fanout children (orchestrator) and
        # background agents (manager) are visible in the same monitor (GH#10).
        self.subagent_orchestrator.worker_telemetry = self.background_agents.telemetry
        # Park runs abandoned by dead processes (stale RUNNING → PAUSED).
        # recover() existed with no production caller, so a restart left
        # every in-flight background run dangling with a live lease while
        # collection answered "Unknown agent_id" — timed out and nothing.
        try:
            recovered = self.background_agents.recover()
            if any(recovered.values()):
                logger.info("background agents recovered at boot: %s", recovered)
        except Exception:
            logger.debug("background agent recovery failed", exc_info=True)

        # ADR-0057: the REST approval bridge. A REST request for an
        # executable-config action (`hooks.create`, `mcp.add_server`,
        # `plugins.install`) asks a human over the WebSocket channel, because
        # ADR-0055 measured that REST otherwise gets the agent's *no-approver*
        # behaviour. The bridge holds the connected channels; the WebSocket
        # route registers one per connection. Flag-gated by
        # `WISP_REST_APPROVAL` (default OFF), so with it off nothing consults
        # this object.
        from wisp.server.approval_bridge import ApprovalBridge
        self.approval_bridge = ApprovalBridge()

        # Owned HTTP session registry (Phase 2.1, D4): pools acquired
        # through this registry are closed in shutdown(). Providers that
        # predate the registry still construct their own sessions; those
        # remain their own responsibility until migrated.
        from wisp.core.transport_seam import SessionRegistry

        self.http_sessions = SessionRegistry()

        # Register services for lifecycle management
        self._registry = ServiceRegistry()
        self._registry.register(self.store)
        self._registry.register(self.extensions)
        self._registry.register(self.telemetry)

    def _create_run_store(self) -> Any:
        """Build the durable run registry (migration P0), or None when the
        rollback flag is off.

        Returns None under `durable_runs=False`, which restores the
        pre-migration behavior exactly: `BackgroundAgentManager` keeps its
        in-memory registry and never writes `background_runs` rows.

        Failure to build the store is non-fatal — a durable-observability
        feature must never prevent the agent from starting. Mirrors the
        best-effort posture the manager itself uses for writes
        (WISP_ARCHITECTURE_DECISIONS.md ADR-0004).
        """
        if not getattr(self.config, "durable_runs", True):
            return None
        try:
            from wisp.runs.store import SQLiteRunStore
            return SQLiteRunStore(self.store)
        except Exception:
            logger.warning(
                "Durable run store unavailable — background runs will not "
                "persist (durable_runs=True).", exc_info=True)
            return None

    def _create_trace_store(self) -> Any:
        """Build the durable span sink (migration P0), or None when the
        rollback flag is off.

        Before P0, `SQLiteTraceStore` was constructed only by
        `wisp/trace/cli.py` — a read-only viewer — so `trace_spans` was
        always empty and `infra/tracing.new_span()` had no caller anywhere
        in the tree. Same best-effort posture as the run store (ADR-0004).
        """
        if not getattr(self.config, "turn_spans", True):
            return None
        try:
            from wisp.trace.store import SQLiteTraceStore
            return SQLiteTraceStore(self.store)
        except Exception:
            logger.warning(
                "Trace span store unavailable — no spans will be recorded "
                "(turn_spans=True).", exc_info=True)
            return None

    def _create_core(self) -> WispAgentCore:
        """Factory for creating stateless core instances.

        Reads the RUNTIME's live config when present so /provider and /model
        switches (which mutate runtime.config) take effect on the next turn;
        root.config is only the bootstrap value.

        Before building, the selection contract resolves + validates the
        effective (provider, model): an unset model picks the first model
        the provider actually serves; an unknown one warns with real
        alternatives instead of failing mid-turn with a provider 404.
        """
        from wisp.providers.factory import ProviderFactory
        from wisp.provider_catalog import resolve_selection

        cfg = getattr(getattr(self, "runtime", None), "config", None) or self.config
        resolution = resolve_selection(cfg)
        # Helper to handle both real WispConfig (has replace) and test
        # doubles like _TestConfig in test_integration_e2e.py.
        def _replace_cfg(c, **kw):
            if hasattr(c, "replace"):
                return c.replace(**kw)  # type: ignore[attr-defined]
            # Fallback for test doubles: mutate in place and return
            for k, v in kw.items():
                try:
                    object.__setattr__(c, k, v)
                except Exception:
                    setattr(c, k, v)
            return c

        if resolution.status == "model_unset":
            logger.info(
                "No model configured — serving '%s' (%s). Alternatives: %s",
                resolution.suggested, resolution.provider,
                ", ".join(resolution.alternatives[:5]),
            )
            cfg = _replace_cfg(cfg, model=resolution.suggested)
        elif resolution.status == "unknown_model":
            # Unknown model on a live listing is a certain 404 at chat time.
            # Auto-correct only for cloud providers where the catalog is
            # authoritative (nvidia/openai/openrouter). For ollama, local
            # models may not appear in the daemon's tag list yet (e.g.
            # freshly pulled qwen2.5-coder) — warning and serving is safer
            # than silently swapping to a cloud model.
            # Also skip for test doubles (MagicMock) which have no real
            # provider catalog.
            is_mock = hasattr(cfg, "assert_called_once_with") or hasattr(cfg, "_mock_name")
            from wisp.provider_select import is_strict_provider

            should_autocorrect = (
                resolution.suggested
                and not is_mock
                and is_strict_provider(resolution.provider)
            )
            if should_autocorrect:
                logger.warning(
                    "%s Auto-correcting to '%s'. Did you mean: %s? "
                    "Persist with /model %s %s.",
                    resolution.detail, resolution.suggested,
                    ", ".join(resolution.alternatives[:5]) or "(none listed)",
                    resolution.provider, resolution.suggested,
                )
                cfg = _replace_cfg(cfg, model=resolution.suggested)
                # Also persist the correction so the next turn doesn't repeat
                # the warning — this is the same seam `provider_catalog`
                # documents as the single source of truth.
                try:
                    runtime = getattr(self, "runtime", None)
                    if runtime is not None:
                        runtime.config = cfg  # type: ignore[attr-defined]
                except Exception:
                    pass
            else:
                alts = ", ".join(resolution.alternatives[:5]) or "(none listed)"
                logger.warning(
                    "%s Serving '%s' anyway. Did you mean: %s? "
                    "Switch with /model <provider> <model>.",
                    resolution.detail, resolution.model, alts,
                )
        elif resolution.status == "unreachable":
            # No API key or provider down — surface as a clear error at
            # turn time instead of letting the provider 401/404 with 0 tools.
            # _NullProvider yields an error event that the transport renders
            # as an error card, not silent thinking.
            logger.error("Provider '%s' unreachable: %s", resolution.provider, resolution.detail)
            return WispAgentCore(
                config=cfg,
                provider=_NullProvider(detail=resolution.detail),
                security=self.security,
                extensions=self.extensions,
                tool_executor=self.tool_executor,
            )
        provider_name = getattr(cfg, "provider", None)
        if provider_name:
            factory = ProviderFactory()
            provider = factory.from_config(cfg)
        else:
            logger.warning(
                "No LLM provider configured. Set WISP_PROVIDER or provider in config. "
                "Using null provider — turns will produce no output."
            )
            provider = _NullProvider()

        return WispAgentCore(
            config=cfg,
            provider=provider,
            security=self.security,
            extensions=self.extensions,
            tool_executor=self.tool_executor,
        )

    def _create_compaction_provider(self, model: str):
        """Create a provider for compaction summarization.

        Uses the same provider as the main config (Ollama, OpenAI, etc.)
        rather than hardcoding Ollama. Falls back to truncation on failure.
        """
        if not model:
            return None
        try:
            from wisp.providers.factory import ProviderFactory
            factory = ProviderFactory()
            provider = factory.from_config(self.config)
            provider.model = model
            return provider
        except Exception:
            logger.warning("Failed to create compaction provider for model=%s", model, exc_info=True)
            return None

    def bind_loop(self, loop: Any) -> None:
        """Register the shared thread pool as *loop*'s default executor.

        Must be called after the event loop exists but before turns run —
        the pool cannot be registered in __post_init__ because no loop is
        running there, and asyncio.to_thread()/run_in_executor(None) only
        reach the shared pool through the loop's default executor.
        """
        try:
            from wisp.async_utils import non_owning_executor
            loop.set_default_executor(non_owning_executor())
        except Exception:
            logger.warning("Could not register shared executor", exc_info=True)

    def start(self) -> None:
        """Start all services."""
        # Validate config before starting services
        self.config.validate_or_raise()
        self._registry.start()

    def stop(self) -> None:
        """Stop all services."""
        self._registry.stop()

    def shutdown(self) -> None:
        """Shutdown all services gracefully with timeouts."""
        # Cancel detached agent work BEFORE closing the store underneath it.
        import contextlib
        with contextlib.suppress(Exception):
            self.background_agents.shutdown_pending()
        with contextlib.suppress(Exception):
            self.subagent_orchestrator.request_cancel_live()
        # Restore tool implementations replaced by install_sink() (F14a):
        # the sink patch is process-global, so a root that never uninstalled
        # would silently change tool behavior for everything after it.
        with contextlib.suppress(Exception):
            from agent.tools.runner import uninstall_sink as _uninstall_sink  # type: ignore
            _uninstall_sink()
        # Reverse the logging interceptor the same way (BadgeFilter + file
        # sink are process-global too; without this, retry diagnostics stay
        # file-routed for every later turn/test in the process).
        with contextlib.suppress(Exception):
            from agent.logger import uninstall as _uninstall_agent_logger  # type: ignore
            _uninstall_agent_logger()
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
        # Disconnect MCP stdio subprocesses explicitly; atexit is the
        # backstop, not the owner — shutdown() should leave nothing running.
        try:
            self._mcp_manager.shutdown()
        except Exception:
            pass
        # The tool pool is root-LOCAL (not a process-global singleton), so
        # this root owns its lifecycle. wait=False: orphaned timed-out tool
        # threads can't be joined anyway, and healthy workers finish fast.
        try:
            self.tool_executor._tool_pool.shutdown(wait=False)
        except Exception:
            pass
        # The network pool mirrors the tool pool lifecycle (root-LOCAL);
        # getattr-guarded: older executors lack the attr.
        try:
            network_pool = getattr(self.tool_executor, "_network_pool", None)
            if network_pool is not None:
                network_pool.shutdown(wait=False)
        except Exception:
            pass
        # Owned HTTP pools (Phase 2.1, D4): close what this root tracked.
        # Sessions predate the registry are unaffected; close_all is a
        # safe no-op when nothing was tracked.
        with contextlib.suppress(Exception):
            registry = getattr(self, "http_sessions", None)
            if registry is not None:
                registry.close_all()
        # NOTE: deliberately NOT shutting down the process-global shared
        # executor or background loop here. They are singletons shared by
        # every root in the process (tests, server restarts, embedded use);
        # killing them here poisoned all later roots with "cannot schedule
        # new futures after shutdown". asyncio's atexit hooks join the
        # loop's daemon threads at interpreter exit, which is the correct
        # owner for this lifecycle.

    def health(self) -> list:
        """Check health of all services."""
        return self._registry.healthy()

    def is_healthy(self) -> bool:
        """True if all services are healthy."""
        return self._registry.is_healthy()


class _NullProvider:
    """Placeholder provider that yields an error event when no provider is configured."""

    def __init__(self, detail: str | None = None):
        self.detail = detail

    def generate_stream_events(self, **kwargs):
        msg = self.detail or "No LLM provider configured. Set WISP_PROVIDER or add 'provider' to config."
        return iter([{
            "type": "error",
            "text": msg,
        }])
