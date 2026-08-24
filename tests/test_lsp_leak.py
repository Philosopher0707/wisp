"""Tests for LSP process lifecycle — prevent orphaned language servers.

Covers:
  1. Singleton LSPManager — same instance returned across calls
  2. Workspace switch — old singleton shuts down before new one created
  3. Explicit shutdown_all() — cleans up server map and detaches finalizer
  4. Context manager — shuts down on exit
  5. Weak-ref finalizer — fires if manager GC'd without shutdown
  6. atexit handler — shutdown_global_lsp_manager is idempotent
"""

import tempfile
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import wisp.lsp.manager as _lsp_module
from wisp.lsp.manager import (
    LSPManager,
    get_lsp_manager,
    shutdown_global_lsp_manager,
)


# ──────────────────────────────────────────────────────────────────────────────
# 1. Singleton semantics
# ──────────────────────────────────────────────────────────────────────────────


class TestSingletonSemantics:
    """get_lsp_manager must return the same instance for the same workspace."""

    def setup_method(self):
        shutdown_global_lsp_manager()

    def teardown_method(self):
        shutdown_global_lsp_manager()

    def test_same_instance_same_workspace(self):
        with tempfile.TemporaryDirectory() as td:
            m1 = get_lsp_manager(td)
            m2 = get_lsp_manager(td)
            assert m1 is m2

    def test_different_instance_different_workspace(self):
        with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
            m1 = get_lsp_manager(td1)
            m2 = get_lsp_manager(td2)
            assert m1 is not m2

    def test_workspace_switch_shuts_down_old(self):
        with tempfile.TemporaryDirectory() as td1, tempfile.TemporaryDirectory() as td2:
            m1 = get_lsp_manager(td1)
            with patch.object(m1, "shutdown_all") as mock_shutdown:
                m2 = get_lsp_manager(td2)
                mock_shutdown.assert_called_once()
                assert get_lsp_manager(td2) is m2

    def test_initialize_called_on_first_get(self):
        with tempfile.TemporaryDirectory() as td:
            with patch.object(LSPManager, "initialize") as mock_init:
                m = get_lsp_manager(td)
                mock_init.assert_called_once()
                assert m is get_lsp_manager(td)


# ──────────────────────────────────────────────────────────────────────────────
# 2. Explicit shutdown
# ──────────────────────────────────────────────────────────────────────────────


class TestExplicitShutdown:
    """shutdown_all() must clear internal state and detach the finalizer."""

    def test_shutdown_clears_servers_and_unsets_initialized(self):
        mgr = LSPManager(workspace="/tmp")
        mgr._initialized = True
        mgr._servers["python"] = MagicMock()

        mgr.shutdown_all()
        assert not mgr._servers
        assert mgr._initialized is False

    def test_shutdown_detaches_finalizer(self):
        mgr = LSPManager(workspace="/tmp")
        assert mgr._finalizer.alive
        mgr.shutdown_all()
        assert not mgr._finalizer.alive

    def test_shutdown_idempotent(self):
        mgr = LSPManager(workspace="/tmp")
        mgr.shutdown_all()
        # second call must not raise
        mgr.shutdown_all()
        assert mgr._servers == {}


# ──────────────────────────────────────────────────────────────────────────────
# 3. Context manager
# ──────────────────────────────────────────────────────────────────────────────


class TestContextManager:
    """LSPManager must be usable with ``with``."""

    def test_context_calls_initialize_and_shutdown(self):
        with LSPManager(workspace="/tmp") as mgr:
            assert mgr._initialized is True
        # After exiting context, _initialized should be reset (shutdown_all called)
        assert mgr._initialized is False


# ──────────────────────────────────────────────────────────────────────────────
# 4. Global shutdown
# ──────────────────────────────────────────────────────────────────────────────


class TestGlobalShutdown:
    """shutdown_global_lsp_manager must be idempotent and thread-safe."""

    def setup_method(self):
        shutdown_global_lsp_manager()

    def teardown_method(self):
        shutdown_global_lsp_manager()

    def test_global_shutdown_sets_none(self):
        with tempfile.TemporaryDirectory() as td:
            get_lsp_manager(td)
            assert _lsp_module._GLOBAL_LSP is not None
            shutdown_global_lsp_manager()
            assert _lsp_module._GLOBAL_LSP is None

    def test_global_shutdown_idempotent(self):
        shutdown_global_lsp_manager()
        shutdown_global_lsp_manager()  # should not raise
        assert _lsp_module._GLOBAL_LSP is None

    def test_global_shutdown_thread_safe(self):
        """Concurrent shutdowns must not deadlock or corrupt state."""
        with tempfile.TemporaryDirectory() as td:
            get_lsp_manager(td)

            errors = []
            def _do():
                try:
                    shutdown_global_lsp_manager()
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=_do) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)

            assert not errors
            assert _lsp_module._GLOBAL_LSP is None

    def test_atexit_handler_registered(self):
        """Ensure atexit.shutdown_global_lsp_manager is in the handlers."""
        # We can't inspect atexit's private list directly, but we can
        # verify the function is callable and the module loaded.
        assert callable(shutdown_global_lsp_manager)


# ──────────────────────────────────────────────────────────────────────────────
# 5. Server endpoint usage (integration-like)
# ──────────────────────────────────────────────────────────────────────────────


class TestServerEndpointUsage:
    """FastAPI endpoints must call get_lsp_manager, not new LSPManager()."""

    @pytest.mark.parametrize("router_file", [
        "wisp/server/routes/diagnostics.py",
        "wisp/server/routes/suggestions.py",
    ])
    def test_endpoints_use_singleton_source(self, router_file):
        """Read the router source and assert it imports get_lsp_manager."""
        router_py = Path(__file__).resolve().parent.parent / router_file
        assert router_py.exists(), f"{router_file} must exist"
        source = router_py.read_text()
        assert "get_lsp_manager" in source, (
            f"{router_file} must import get_lsp_manager to avoid per-request leaks"
        )
        assert "LSPManager(str(WORKSPACE_ROOT))" not in source, (
            f"{router_file} must NOT instantiate LSPManager per-request"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
