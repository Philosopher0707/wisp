"""Tests for hook-system security hardening (CVE-like self-RCE fixes).

Tests the six defensive layers:
  1. Hook directory path blocking
  2. Environment variable scrubbing
  3. Auto-approve default changed to false
  4. Hook reload before each execution
  5. PRE_BASH / PRE_FILE_WRITE event hooks
"""

import os
import tempfile
from pathlib import Path

import pytest

from wisp.tools._utils import _is_hook_controlled_path
from wisp.tools._utils_env import scrub_sensitive_env, _ALLOWED_ENV_KEYS
from wisp.tools.errors import ToolError
from wisp.tools.filesystem import tool_write_file, tool_edit_file
from wisp.trust import WorkspaceTrustManager


# ───────────────────────────────────────────────────────────────────────────────
# 1. Hook directory path blocking
# ───────────────────────────────────────────────────────────────────────────────

class TestHookPathBlocking:
    """tool_write_file and tool_edit_file must refuse paths under .wisp/hooks."""

    def test_write_blocked_for_hook_dir(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".wisp", "hooks", "PRE_BASH_pwn.sh")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with pytest.raises(ToolError, match="hook-controlled"):
                tool_write_file(
                    path, td, "#!/bin/bash\necho pwned"
                )

    def test_edit_blocked_for_hook_dir(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, ".wisp", "hooks", "existing.sh")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("echo old\n")
            with pytest.raises(ToolError, match="hook-controlled"):
                tool_edit_file(path, td, "echo old", "echo pwned")

    def test_write_allowed_outside_hook_dir(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "src", "main.py")
            result = tool_write_file(path, td, "print('hello')")
            assert result["status"] == "ok"
            assert Path(path).read_text() == "print('hello')"

    def test_edit_allowed_outside_hook_dir(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "src", "main.py")
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("print('old')\n")
            result = tool_edit_file(path, td, "print('old')", "print('new')")
            assert result["status"] == "ok"
            assert "new" in Path(path).read_text()


class TestIsHookControlledPath:
    """Unit tests for the path-matching heuristic."""

    @pytest.mark.parametrize("path,expected", [
        (".wisp/hooks/PRE_BASH_pwn.sh", True),
        (".wisp/hooks/", True),
        (".wisp\\hooks\\test.ps1", True),  # Windows
        ("project/.wisp/hooks/foo.json", True),
        ("../.wisp/hooks/evil.sh", True),
        ("src/main.py", False),
        (".wisp/config.json", False),
        (".wisp/hooks.json", False),
        (".wisphooks/file.txt", False),
        (".wisp/hooks_backup/old.sh", False),
    ])
    def test_boundary_cases(self, path, expected):
        assert _is_hook_controlled_path(path) == expected


# ───────────────────────────────────────────────────────────────────────────────
# 2. Environment variable scrubbing
# ───────────────────────────────────────────────────────────────────────────────

class TestEnvScrub:
    """Hook subprocesses must not see credentials."""

    def test_safe_vars_preserved(self):
        env = {
            "PATH": "/usr/bin",
            "WISP_HOOK_EVENT": "PRE_BASH",
            "WISP_TOOL_NAME": "run_bash",
            "AWS_SECRET_KEY": "should-be-gone",
            "WISP_API_KEY": "secret",
            "HOME": "/home/user",  # should be removed
        }
        clean = scrub_sensitive_env(env)
        assert "PATH" in clean
        assert "WISP_HOOK_EVENT" in clean
        assert "WISP_TOOL_NAME" in clean
        assert "AWS_SECRET_KEY" not in clean
        assert "WISP_API_KEY" not in clean
        assert "HOME" not in clean

    def test_scrub_removes_sensitive_defaults(self):
        # Test that the default _ALLOWED_ENV_KEYS doesn't include secrets
        forbidden = {
            "WISP_API_KEY",
            "OLLAMA_HOST",
            "OPENAI_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "GITHUB_TOKEN",
            "HOME",
        }
        assert forbidden.isdisjoint(_ALLOWED_ENV_KEYS), "Allowed list leaks secrets!"

    def test_scrub_with_none_uses_os_environ(self):
        # Just verify it doesn't crash when fed the real environment
        clean = scrub_sensitive_env()
        assert "PATH" in clean  # should always be present


# ───────────────────────────────────────────────────────────────────────────────
# 3. Auto-approve default change
# ───────────────────────────────────────────────────────────────────────────────

class TestAutoApproveDefault:
    """auto_approve must default to False — mutating tools require opt-in."""

    def test_auto_approve_defaults_false(self):
        from wisp.config import WispConfig
        cfg = WispConfig()
        assert cfg.auto_approve is False


# ───────────────────────────────────────────────────────────────────────────────
# 4. HookManager.reload hooks
# ───────────────────────────────────────────────────────────────────────────────

class TestHookReload:
    """Hooks should reload before each execution if on-disk changes detected."""

    def test_reload_hooks_method_exists(self):
        from wisp.hooks import HookManager
        mgr = HookManager(workspace=Path.cwd())
        assert hasattr(mgr, "reload_hooks")

    def test_reload_hooks_clears_and_reloads(self):
        import tempfile
        from wisp.hooks import HookManager, HookConfig, HookEvent

        with tempfile.TemporaryDirectory() as td:
            hooks_dir = Path(td) / ".wisp" / "hooks"
            hooks_dir.mkdir(parents=True)

            # Create an initial hook
            (hooks_dir / "PRE_BASH_test.sh").write_text("#!/bin/bash\necho '{\"action\":\"allow\"}'")

            WorkspaceTrustManager.trust_workspace(td)
            mgr = HookManager(workspace=Path(td))
            mgr.load_project_hooks()
            assert mgr.hook_count >= 1

            original_count = mgr.hook_count

            # Add a second hook on disk (must temporarily make dir writable)
            import stat
            hooks_dir.chmod(hooks_dir.stat().st_mode | 0o200)
            (hooks_dir / "PRE_BASH_test2.sh").write_text("#!/bin/bash\necho '{\"action\":\"allow\"}'")
            hooks_dir.chmod(hooks_dir.stat().st_mode & ~0o200)
            mgr.reload_hooks()
            assert mgr.hook_count >= original_count + 1

            # Remove all hooks (must temporarily make dir writable)
            hooks_dir.chmod(hooks_dir.stat().st_mode | 0o200)
            (hooks_dir / "PRE_BASH_test.sh").unlink()
            (hooks_dir / "PRE_BASH_test2.sh").unlink()
            hooks_dir.chmod(hooks_dir.stat().st_mode & ~0o200)
            mgr.reload_hooks()
            assert mgr.hook_count == 0


# ───────────────────────────────────────────────────────────────────────────────
# 5. Event-specific hooks (integration-style tests via ToolExecutor)
# ───────────────────────────────────────────────────────────────────────────────

class TestEventSpecificHooks:
    """PRE_BASH and PRE_FILE_WRITE hooks fire before the specific tools."""

    @pytest.mark.asyncio
    async def test_pre_bash_hook_fires(self):
        import tempfile
        from wisp.tool_executor import ToolExecutor
        from wisp.config import WispConfig
        from wisp.hooks import HookManager, HookConfig, HookEvent

        with tempfile.TemporaryDirectory() as td:
            hooks_dir = Path(td) / ".wisp" / "hooks"
            hooks_dir.mkdir(parents=True)

            # Create a PRE_BASH hook that blocks commands containing "rm"
            (hooks_dir / "PRE_BASH_block-rm.sh").write_text(
                '#!/bin/bash\n'
                'read ctx\n'
                'if echo "$ctx" | grep -q "rm -rf"; then\n'
                '  echo \'{"action":"block","message":"blocked by pre_bash hook"}\'\n'
                '  exit 0\n'
                'fi\n'
                'echo \'{"action":"allow"}\'\n'
            )

            WorkspaceTrustManager.trust_workspace(td)
            cfg = WispConfig()
            cfg.auto_approve = True
            cfg.workspace = td
            mgr = HookManager(workspace=Path(td))
            mgr.load_project_hooks()
            assert mgr.hook_count >= 1

            executor = ToolExecutor(config=cfg, hook_manager=mgr)

            events = [e async for e in executor.execute(
                "run_bash",
                {"command": "rm -rf /tmp"},
                td,
            )]
            result = events[-1]
            assert "BLOCKED" in result.data.get("result", "").upper() or "blocked" in result.data.get("result", "").lower()

    @pytest.mark.asyncio
    async def test_post_tool_use_hook_fires(self):
        import tempfile
        from wisp.tool_executor import ToolExecutor
        from wisp.config import WispConfig
        from wisp.hooks import HookManager, HookConfig, HookEvent

        with tempfile.TemporaryDirectory() as td:
            hooks_dir = Path(td) / ".wisp" / "hooks"
            hooks_dir.mkdir(parents=True)

            # A POST_TOOL_USE hook that just logs
            (hooks_dir / "POST_TOOL_USE_log.sh").write_text(
                '#!/bin/bash\nread ctx\necho \'{"action":"allow"}\'\n'
            )

            WorkspaceTrustManager.trust_workspace(td)
            cfg = WispConfig()
            cfg.auto_approve = True
            cfg.workspace = td
            mgr = HookManager(workspace=Path(td))
            mgr.load_project_hooks()
            assert mgr.hook_count >= 1

            executor = ToolExecutor(config=cfg, hook_manager=mgr)

            events = [e async for e in executor.execute(
                "list_files",
                {"path": "."},
                td,
            )]
            # Should not crash; post hooks are best-effort
            assert events[-1].data.get("result", "") != ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
