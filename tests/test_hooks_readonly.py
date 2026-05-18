"""Tests for OS-level read-only hooks directory enforcement.

Regression: _is_hook_controlled_path() only blocked writes at the application
level.  A malicious tool could bypass the check and modify hooks.  After the
fix, HookManager.load_project_hooks() sets the hooks directory to mode 0o555
so the OS itself denies writes.
"""

import os
import stat
import pytest
from pathlib import Path

from wisp.hooks import HookManager
from wisp.trust import WorkspaceTrustManager


class TestHooksDirReadonly:
    """Hooks directory must be read-only at the OS level."""

    def test_enforces_readonly_on_project_hooks(self, tmp_path):
        """load_project_hooks() makes .wisp/hooks/ read-only."""
        hooks_dir = tmp_path / ".wisp" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "PRE_BASH_test.sh").write_text("#!/bin/bash\necho '{}'")

        # Initially writable
        assert os.access(hooks_dir, os.W_OK)

        WorkspaceTrustManager.trust_workspace(str(tmp_path))
        mgr = HookManager(workspace=tmp_path)
        mgr.load_project_hooks()

        # After loading, directory should be read-only
        assert not os.access(hooks_dir, os.W_OK)
        # And existing files too
        assert not os.access(hooks_dir / "PRE_BASH_test.sh", os.W_OK)

    def test_enforces_readonly_on_user_hooks(self, tmp_path, monkeypatch):
        """load_project_hooks() makes ~/.config/wisp/hooks/ read-only."""
        user_hooks = tmp_path / "user_hooks"
        user_hooks.mkdir(parents=True)
        (user_hooks / "PRE_BASH_user.sh").write_text("#!/bin/bash\necho '{}'")

        # Patch HOME so we don't touch the real ~/.config
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        home = tmp_path / "home"
        home.mkdir()
        real_user_hooks = home / ".config" / "wisp" / "hooks"
        real_user_hooks.mkdir(parents=True)
        (real_user_hooks / "PRE_BASH_real.sh").write_text("#!/bin/bash\necho '{}'")

        ws = tmp_path / "workspace"
        (ws / ".wisp" / "hooks").mkdir(parents=True)
        (ws / ".wisp" / "hooks" / "PRE_BASH_ws.sh").write_text("#!/bin/bash\necho '{}'")

        WorkspaceTrustManager.trust_workspace(str(ws))
        mgr = HookManager(workspace=ws)
        mgr.load_project_hooks()

        # Both project and user hooks should be read-only
        assert not os.access(ws / ".wisp" / "hooks", os.W_OK)
        assert not os.access(real_user_hooks, os.W_OK)

    def test_os_denies_write_after_enforcement(self, tmp_path):
        """After enforcement, actual write attempts raise PermissionError."""
        hooks_dir = tmp_path / ".wisp" / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "PRE_BASH_test.sh").write_text("#!/bin/bash\necho '{}'")

        WorkspaceTrustManager.trust_workspace(str(tmp_path))
        mgr = HookManager(workspace=tmp_path)
        mgr.load_project_hooks()

        # OS-level denial
        with pytest.raises(PermissionError):
            (hooks_dir / "new_hook.sh").write_text("evil")
        with pytest.raises(PermissionError):
            (hooks_dir / "PRE_BASH_test.sh").write_text("evil")
