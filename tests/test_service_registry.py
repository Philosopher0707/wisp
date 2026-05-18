"""TDD for ServiceRegistry — lifecycle management for infrastructure services.

Manages: UnifiedStore, SecurityPolicy, ExtensionHost, Telemetry
and any future services with start/stop lifecycle.
"""

import pytest


# ── Test fixtures ──────────────────────────────────────────────────

@pytest.fixture
def registry():
    from wisp.infra.lifecycle import ServiceRegistry
    return ServiceRegistry()


# ── Minimal service implementations for testing ────────────────────

class _TestService:
    def __init__(self, name):
        self.name = name
        self.started = False
        self.stopped = False
        self.start_order = None
        self.stop_order = None

    def start(self):
        self.started = True
        self.start_order = _TestService._start_counter
        _TestService._start_counter += 1

    def stop(self):
        self.stopped = True
        self.stop_order = _TestService._stop_counter
        _TestService._stop_counter += 1

    @classmethod
    def reset_counter(cls):
        cls._start_counter = 0
        cls._stop_counter = 0

_TestService._start_counter = 0
_TestService._stop_counter = 0


class _BrokenStartService:
    name = "broken_start"
    def start(self): raise RuntimeError("start failed")
    def stop(self): pass


class _BrokenStopService:
    name = "broken_stop"
    def start(self): pass
    def stop(self): raise RuntimeError("stop failed")


# ═══════════════════════════════════════════════════════════════════
# 1. Registration and basic lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestServiceLifecycle:
    """Services are started on registry.start(), stopped on registry.stop()."""

    def test_register_adds_service(self, registry):
        svc = _TestService("a")
        registry.register(svc)
        assert "a" in registry.names()

    def test_start_starts_all_services(self, registry):
        _TestService.reset_counter()
        svc = _TestService("a")
        registry.register(svc)
        registry.start()
        assert svc.started is True

    def test_stop_stops_all_services(self, registry):
        _TestService.reset_counter()
        svc = _TestService("a")
        registry.register(svc)
        registry.start()
        registry.stop()
        assert svc.stopped is True


# ═══════════════════════════════════════════════════════════════════
# 2. Ordering
# ═══════════════════════════════════════════════════════════════════

class TestServiceOrdering:
    """Start order = registration order. Stop order = reverse."""

    def test_start_order_matches_registration(self, registry):
        _TestService.reset_counter()
        a = _TestService("a")
        b = _TestService("b")
        registry.register(a)
        registry.register(b)
        registry.start()
        assert a.start_order == 0
        assert b.start_order == 1

    def test_stop_order_is_reversed(self, registry):
        _TestService.reset_counter()
        a = _TestService("a")
        b = _TestService("b")
        registry.register(a)
        registry.register(b)
        registry.start()
        registry.stop()
        assert b.stop_order == 0  # b stopped first
        assert a.stop_order == 1  # a stopped second


# ═══════════════════════════════════════════════════════════════════
# 3. Error handling
# ═══════════════════════════════════════════════════════════════════

class TestServiceErrorHandling:
    """Broken services don't crash the registry."""

    def test_broken_start_does_not_stop_others(self, registry):
        _TestService.reset_counter()
        good = _TestService("good")
        registry.register(good)
        registry.register(_BrokenStartService())
        with pytest.raises(RuntimeError):
            registry.start()
        # good was started before the broken one
        assert good.started is True

    def test_broken_stop_does_not_stop_others(self, registry):
        _TestService.reset_counter()
        good = _TestService("good")
        registry.register(good)
        registry.register(_BrokenStopService())
        registry.start()
        registry.stop()  # should not raise
        assert good.stopped is True


# ═══════════════════════════════════════════════════════════════════
# 4. Dependency resolution
# ═══════════════════════════════════════════════════════════════════

class TestServiceDependencies:
    """Services can declare dependencies."""

    def test_dependency_started_first(self, registry):
        _TestService.reset_counter()
        db = _TestService("db")
        api = _TestService("api")
        registry.register(api, depends_on=["db"])
        registry.register(db)
        registry.start()
        assert db.start_order == 0
        assert api.start_order == 1

    def test_missing_dependency_raises(self, registry):
        api = _TestService("api")
        registry.register(api, depends_on=["db"])
        with pytest.raises(RuntimeError) as exc_info:
            registry.start()
        assert "missing dependencies" in str(exc_info.value).lower()


# ═══════════════════════════════════════════════════════════════════
# 5. Retrieval
# ═══════════════════════════════════════════════════════════════════

class TestServiceRetrieval:
    """Services can be retrieved by name or type."""

    def test_get_by_name(self, registry):
        svc = _TestService("my_svc")
        registry.register(svc)
        assert registry.get("my_svc") is svc

    def test_get_missing_returns_none(self, registry):
        assert registry.get("missing") is None

    def test_get_by_type(self, registry):
        svc = _TestService("my_svc")
        registry.register(svc)
        found = registry.get_by_type(_TestService)
        assert found is svc
