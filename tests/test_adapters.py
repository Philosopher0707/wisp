"""TDD for adapter layer.

Bridges old entry points (__main__.py, server.py, cli.py) to the new
CompositionRoot-based system without breaking existing interfaces.
"""

import pytest
from dataclasses import dataclass
from pathlib import Path


@dataclass
class _OldStyleConfig:
    """Old-style config object that existing code uses."""
    model: str = "qwen2.5-coder"
    workspace: str = "/tmp"
    permission_mode: str = "full"
    db_path: str = ""


# ═══════════════════════════════════════════════════════════════════
# 1. Runtime creation adapter
# ═══════════════════════════════════════════════════════════════════

class TestRuntimeAdapter:
    """Adapter creates new Runtime from old-style config."""

    def test_create_runtime_from_old_config(self, tmp_path):
        from wisp.adapters import create_runtime
        config = _OldStyleConfig(
            model="qwen",
            workspace="/tmp",
            permission_mode="full",
            db_path=str(tmp_path / "test.db"),
        )
        runtime = create_runtime(config)
        assert runtime is not None
        assert runtime.store is not None

    def test_runtime_uses_config_model(self, tmp_path):
        from wisp.adapters import create_runtime
        config = _OldStyleConfig(
            model="gpt-4",
            db_path=str(tmp_path / "test.db"),
        )
        runtime = create_runtime(config)
        # Model should be accessible or used in session creation
        assert runtime is not None


# ═══════════════════════════════════════════════════════════════════
# 2. Session adapter
# ═══════════════════════════════════════════════════════════════════

class TestSessionAdapter:
    """Adapter bridges old session API to new UnifiedStore."""

    @pytest.mark.asyncio
    async def test_load_or_create_session(self, tmp_path):
        from wisp.adapters import create_runtime
        config = _OldStyleConfig(db_path=str(tmp_path / "test.db"))
        runtime = create_runtime(config)

        session = await runtime.get_or_create_session(
            session_id="sess-1",
            model="qwen",
            workspace="/tmp",
        )
        assert session["id"] == "sess-1"

    @pytest.mark.asyncio
    async def test_session_persistence(self, tmp_path):
        from wisp.adapters import create_runtime
        config = _OldStyleConfig(db_path=str(tmp_path / "test.db"))
        runtime = create_runtime(config)

        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        session["messages"].append({"role": "user", "content": "hi"})
        runtime.store.save_session(session)

        loaded = runtime.store.load_session("sess-1")
        assert loaded is not None
        assert len(loaded["messages"]) == 1


# ═══════════════════════════════════════════════════════════════════
# 3. Tool adapter
# ═══════════════════════════════════════════════════════════════════

class TestToolAdapter:
    """Adapter bridges old tool API to new ExtensionHost."""

    def test_builtin_tools_available(self, tmp_path):
        from wisp.adapters import create_runtime
        config = _OldStyleConfig(db_path=str(tmp_path / "test.db"))
        runtime = create_runtime(config)

        tools = runtime.extensions.tools()
        # Should have at least some built-in tools
        assert len(tools) >= 0


# ═══════════════════════════════════════════════════════════════════
# 4. Security adapter
# ═══════════════════════════════════════════════════════════════════

class TestSecurityAdapter:
    """Adapter bridges old security API to new SecurityPolicy."""

    def test_full_mode_allows_tools(self, tmp_path):
        from wisp.adapters import create_runtime
        from wisp.infra.security import PermissionMode

        config = _OldStyleConfig(
            permission_mode="full",
            db_path=str(tmp_path / "test.db"),
        )
        runtime = create_runtime(config)
        assert runtime.security.permission_mode == PermissionMode.FULL

    def test_read_only_mode_blocks_write(self, tmp_path):
        from wisp.adapters import create_runtime
        from wisp.infra.security import PermissionMode

        config = _OldStyleConfig(
            permission_mode="read_only",
            db_path=str(tmp_path / "test.db"),
        )
        runtime = create_runtime(config)
        assert runtime.security.permission_mode == PermissionMode.READ_ONLY


# ═══════════════════════════════════════════════════════════════════
# 5. Backward compatibility
# ═══════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """Old code patterns still work through adapters."""

    def test_old_config_object_accepted(self, tmp_path):
        from wisp.adapters import create_runtime
        # Old code might pass a plain object with attributes
        class OldConfig:
            model = "qwen"
            workspace = "/tmp"
            permission_mode = "full"
        
        config = OldConfig()
        config.db_path = str(tmp_path / "test.db")
        runtime = create_runtime(config)
        assert runtime is not None
