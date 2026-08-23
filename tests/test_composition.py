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
