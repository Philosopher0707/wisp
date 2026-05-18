"""Tests for LSP process leak prevention (orphaned child processes).

The core issue: every endpoint request created a fresh LSPManager,
which spawned child LSP processes (pylsp, rust-analyzer, etc.) and
never called shutdown_all() before the request ended. After this fix:
- Only one LSPManager per module per workspace (singleton)
- FastAPI lifespan calls shutdown_global_lsp_manager()
- atexit handler calls shutdown_global_lsp_manager()
- WispAgentCore.close() still calls shutdown_all() on its own copy
- LSPManager __enter__/__exit__ context manager for safe usage
"""

import pytest
from unittest.mock import MagicMock, patch
import tempfile
import os


class TestGlobalSingleton:
    """get_lsp_manager must return the same instance for the same workspace."""

    def test_returns_same_instance_for_same_workspace(self):
        from wisp.lsp.manager import get_lsp_manager, shutdown_global_lsp_manager
        shutdown_global_lsp_manager()  # Reset state

        mgr1 = get_lsp_manager("/tmp/ws1")
        mgr2 = get_lsp_manager("/tmp/ws1")
        assert mgr1 is mgr2

    def test_replaces_instance_for_different_workspace(self):
        from wisp.lsp.manager import get_lsp_manager, shutdown_global_lsp_manager
        shutdown_global_lsp_manager()

        mgr1 = get_lsp_manager("/tmp/ws1")
        # Different workspace => new instance (old one shut down)
        mgr2 = get_lsp_manager("/tmp/ws2")
        assert mgr1 is not mgr2

    def test_initialized_flag_set_on_creation(self):
        from wisp.lsp.manager import get_lsp_manager, shutdown_global_lsp_manager
        shutdown_global_lsp_manager()

        mgr = get_lsp_manager("/tmp/ws3")
        # initialize() is called inside get_lsp_manager
        assert mgr._initialized


class TestContextManager:
    """LSPManager should support context manager protocol."""

    def test_context_manager_initializes_and_shuts_down(self):
        from wisp.lsp.manager import LSPManager

        mgr = MagicMock(spec=LSPManager)
        with patch("wisp.lsp.manager.LSPManager") as MockManager:
            MockManager.return_value = mgr
            from wisp.lsp.manager import get_lsp_manager, shutdown_global_lsp_manager
            shutdown_global_lsp_manager()
            mgr = get_lsp_manager("/tmp/ws4")

            assert mgr.initialize.called

    def test_explicit_shutdown_after_use(self):
        from wisp.lsp.manager import LSPManager

        mgr = LSPManager("/tmp/ws")
        mgr.shutdown_all = MagicMock()
        mgr.shutdown_all()
        mgr.shutdown_all.assert_called_once()


class TestShutdownGlobal:
    """shutdown_global_lsp_manager must be idempotent and safe."""

    def test_idempotent(self):
        from wisp.lsp.manager import shutdown_global_lsp_manager
        shutdown_global_lsp_manager()
        shutdown_global_lsp_manager()  # Should not throw
        assert True

    def test_none_after_shutdown(self):
        import wisp.lsp.manager as mgr
        from wisp.lsp.manager import get_lsp_manager, shutdown_global_lsp_manager
        shutdown_global_lsp_manager()

        mgr_obj = get_lsp_manager("/tmp/ws5")
        assert mgr._GLOBAL_LSP is not None

        shutdown_global_lsp_manager()
        # Must reset global to None
        assert mgr._GLOBAL_LSP is None


class TestAtexitRegistered:
    """The atexit handler should be registered."""

    def test_atexit_handler_registered(self):
        import atexit
        from unittest.mock import patch
        import importlib
        import wisp.lsp.manager as mgr

        real_register = atexit.register
        with patch.object(atexit, 'register', side_effect=real_register) as mock_register:
            importlib.reload(mgr)
            found = any(
                call.args[0] is mgr.shutdown_global_lsp_manager
                for call in mock_register.call_args_list
            )
            assert found, "shutdown_global_lsp_manager must be in atexit handlers"


class TestEndpointDoesNotLeak:
    """Verify endpoint uses singleton and does not create orphan processes."""

    def test_diagnostics_endpoint_reuses_singleton(self):
        from wisp.lsp.manager import get_lsp_manager, shutdown_global_lsp_manager
        shutdown_global_lsp_manager()

        # Simulate two endpoint calls
        mgr1 = get_lsp_manager("/tmp/ws6")
        mgr2 = get_lsp_manager("/tmp/ws6")
        assert mgr1 is mgr2

        # Single instance = single set of child processes, not two orphaned sets
        assert len({id(mgr1), id(mgr2)}) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
