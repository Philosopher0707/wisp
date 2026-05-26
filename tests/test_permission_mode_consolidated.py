"""TDD: Issue 3 — PermissionMode must be a single source of truth.

Before fix: PermissionMode was defined in BOTH wisp.config and wisp.infra.security.
After fix:  Only wisp.infra.security defines it; wisp.config re-exports for compat.
"""

import inspect


class TestPermissionModeSingleSourceOfTruth:
    """PermissionMode must exist in exactly one module."""

    def test_security_module_defines_permission_mode(self):
        from wisp.infra.security import PermissionMode
        assert inspect.isclass(PermissionMode)
        assert hasattr(PermissionMode, "FULL")
        assert hasattr(PermissionMode, "ASK_ALL")
        assert hasattr(PermissionMode, "AUTO_EDIT")
        assert hasattr(PermissionMode, "READ_ONLY")

    def test_config_module_re_exports_permission_mode(self):
        """wisp.config must re-export PermissionMode from security."""
        from wisp.config import PermissionMode as ConfigPM
        from wisp.infra.security import PermissionMode as SecurityPM
        assert ConfigPM is SecurityPM, (
            "wisp.config.PermissionMode must be the SAME object as "
            "wisp.infra.security.PermissionMode, not a duplicate definition"
        )

    def test_config_module_does_not_redefine_permission_mode(self):
        """The class object in wisp.config must NOT be defined there."""
        import wisp.config as cfg_mod
        import wisp.infra.security as sec_mod

        # If config defines its own class, __module__ will be wisp.config
        # After fix, the re-exported symbol's __module__ should be wisp.infra.security
        pm = getattr(cfg_mod, "PermissionMode", None)
        assert pm is not None
        assert pm.__module__ == "wisp.infra.security", (
            f"PermissionMode.__module__ is {pm.__module__!r}; "
            "expected 'wisp.infra.security' — config must re-export, not redefine"
        )

    def test_tool_executor_imports_from_security(self):
        """ToolExecutor must import PermissionMode from security, not config."""
        import wisp.tool_executor as te_mod
        # Check the module's source for the import line
        import inspect
        source = inspect.getsource(te_mod)
        assert "from wisp.infra.security import" in source or "from wisp.config import" not in source, (
            "tool_executor.py should import PermissionMode from wisp.infra.security"
        )
