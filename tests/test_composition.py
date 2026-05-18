"""TDD for CompositionRoot — the wiring layer.

Replaces: scattered instantiation across __main__.py, server.py, cli.py.
One place to create and wire all services.
"""

import pytest
from pathlib import Path


from dataclasses import dataclass
from pathlib import Path


@dataclass
class _TestConfig:
    db_path: Path
    permission_mode: str
    model: str


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
