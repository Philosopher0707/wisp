"""TDD for CompositionRoot — the wiring layer.

Replaces: scattered instantiation across __main__.py, server.py, cli.py.
One place to create and wire all services.
"""

import pytest
from pathlib import Path


from dataclasses import dataclass


@dataclass
class _TestConfig:
    db_path: Path
    permission_mode: str
    model: str
    turn_timeout: int = 600

    def validate_or_raise(self) -> None:
        """CompositionRoot.start() validates config before wiring services."""


@pytest.fixture
def config():
    from wisp.infra.security import PermissionMode
    return _TestConfig(
        db_path=Path("/tmp/test.db"),
        permission_mode=PermissionMode.FULL,
        model="qwen2.5-coder",
    )


# ═══════════════════════════════════════════════════════════════════
# 1. Construction
# ═══════════════════════════════════════════════════════════════════

class TestCompositionConstruction:
    """CompositionRoot creates all services."""

    def test_creates_store(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.store is not None

    def test_creates_security(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.security is not None

    def test_creates_extensions(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.extensions is not None

    def test_creates_telemetry(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.telemetry is not None

    def test_creates_runtime(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.runtime is not None


# ═══════════════════════════════════════════════════════════════════
# 2. Lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestCompositionLifecycle:
    """CompositionRoot starts and stops all services."""

    def test_start_starts_services(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        root.start()
        assert root._registry._started  # services were started

    def test_stop_stops_services(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        root.start()
        root.stop()
        assert not root._registry._started  # services were stopped


# ═══════════════════════════════════════════════════════════════════
# 3. Dependency injection
# ═══════════════════════════════════════════════════════════════════

class TestDependencyInjection:
    """Services are wired together correctly."""

    def test_runtime_uses_store(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.runtime.store is root.store

    def test_runtime_uses_security(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.runtime.security is root.security

    def test_runtime_uses_extensions(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.runtime.extensions is root.extensions

    def test_runtime_uses_telemetry(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.runtime.telemetry is root.telemetry


# ═══════════════════════════════════════════════════════════════════
# 4. Configuration propagation
# ═══════════════════════════════════════════════════════════════════

class TestConfigurationPropagation:
    """Config values propagate to services."""

    def test_permission_mode_propagates(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.security.permission_mode == config.permission_mode

    def test_db_path_propagates(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.store.db_path == config.db_path


# ═══════════════════════════════════════════════════════════════════
# 5. Error handling
# ═══════════════════════════════════════════════════════════════════

class TestCompositionErrors:
    """Broken services don't crash composition."""

    def test_missing_db_path_raises(self):
        from wisp.composition import CompositionRoot
        with pytest.raises(TypeError):
            CompositionRoot(None)


class TestTwoPhaseElimination:
    """CompositionRoot must wire dependencies at construction time,
    not via post-hoc private-attribute patching."""

    def test_no_private_runner_patching(self, config):
        import inspect
        from wisp.composition import CompositionRoot
        src = inspect.getsource(CompositionRoot.__post_init__)
        assert "_runner._tool_executor" not in src, (
            "CompositionRoot patches private _runner._tool_executor; "
            "use constructor injection instead"
        )

    def test_orchestrator_receives_tool_executor_at_construction(self, config):
        from wisp.composition import CompositionRoot
        root = CompositionRoot(config)
        assert root.subagent_orchestrator._runner._tool_executor is root.tool_executor


class TestHookManagerSeparation:
    """Issue 9: CompositionRoot wires separate InterceptHookManager and ToolHookManager."""

    def test_composition_creates_separate_hook_managers(self, config):
        from wisp.composition import CompositionRoot
        from wisp.infra.hook_types import InterceptHookManager, ToolHookManager
        root = CompositionRoot(config)
        assert isinstance(root._intercept_hook_manager, InterceptHookManager)
        assert isinstance(root._tool_hook_manager, ToolHookManager)
        assert root._intercept_hook_manager is not root._tool_hook_manager

    def test_hook_extension_uses_intercept_manager(self, config):
        from wisp.composition import CompositionRoot
        from wisp.extensions.hooks import HookExtension
        from wisp.infra.hook_types import InterceptHookManager
        root = CompositionRoot(config)
        for ext in root.extensions._extensions:
            if isinstance(ext, HookExtension):
                assert isinstance(ext._manager, InterceptHookManager)
                return
        pytest.fail("HookExtension not found in CompositionRoot")

    def test_tool_executor_uses_tool_hook_manager(self, config):
        from wisp.composition import CompositionRoot
        from wisp.infra.hook_types import ToolHookManager
        root = CompositionRoot(config)
        assert isinstance(root.tool_executor.hook_manager, ToolHookManager)

    def test_subagent_orchestrator_uses_tool_hook_manager(self, config):
        from wisp.composition import CompositionRoot
        from wisp.infra.hook_types import ToolHookManager
        root = CompositionRoot(config)
        assert isinstance(root.subagent_orchestrator.hook_manager, ToolHookManager)


# ═══════════════════════════════════════════════════════════════════
# Executor binding + MCP teardown (wiring-audit round)
# ═══════════════════════════════════════════════════════════════════

class TestBindLoop:
    def test_bind_loop_registers_shared_executor_as_default(self, config):
        import asyncio
        from wisp.composition import CompositionRoot
        from wisp.async_utils import (
            NonOwningExecutor,
            get_shared_executor,
        )

        root = CompositionRoot(config)
        loop = asyncio.new_event_loop()
        try:
            root.bind_loop(loop)
            # to_thread / run_in_executor(None) resolve through the default
            # executor; it must DELEGATE to the shared configured pool.
            # Deliberately NOT identity: loops get a non-owning proxy so
            # asyncio's loop-close shutdown can never kill the process-
            # global pool for later roots (see test_shared_pool_isolation).
            ex = loop._default_executor
            assert isinstance(ex, NonOwningExecutor)
            assert ex.submit(lambda: "via-shared").result() == "via-shared"
            assert get_shared_executor().submit(
                lambda: "pool-alive"
            ).result() == "pool-alive"
        finally:
            loop.close()
            # closing this loop must NOT have poisoned the shared pool
            assert get_shared_executor().submit(
                lambda: "still-alive"
            ).result() == "still-alive"

    def test_bind_loop_survives_executor_failure(self, config):
        import asyncio
        from unittest.mock import patch
        from wisp.composition import CompositionRoot

        root = CompositionRoot(config)
        loop = asyncio.new_event_loop()
        try:
            with patch("wisp.async_utils.get_shared_executor",
                       side_effect=RuntimeError("pool broken")):
                root.bind_loop(loop)  # must not raise
        finally:
            loop.close()


class TestShutdownTearsDownMCP:
    def test_shutdown_disconnects_mcp_servers(self, config):
        from unittest.mock import MagicMock
        from wisp.composition import CompositionRoot

        root = CompositionRoot(config)
        root._mcp_manager = MagicMock()
        root._lsp_manager = MagicMock()
        root.shutdown()
        root._mcp_manager.shutdown.assert_called_once()
