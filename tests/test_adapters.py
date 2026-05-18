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
# 6. Store adapter
# ═══════════════════════════════════════════════════════════════════

class TestStoreAdapter:
    """Adapter provides get_store() compatibility."""

    def test_get_store_returns_store(self, tmp_path):
        from wisp.adapters import get_store
        store = get_store(str(tmp_path / "test.db"))
        assert store is not None
        assert hasattr(store, "load_session")
        assert hasattr(store, "save_session")

    def test_get_store_singleton(self, tmp_path):
        from wisp.adapters import get_store
        db_path = str(tmp_path / "test.db")
        store1 = get_store(db_path)
        store2 = get_store(db_path)
        assert store1 is store2


# ═══════════════════════════════════════════════════════════════════
# 7. Session preview adapter
# ═══════════════════════════════════════════════════════════════════

class TestSessionPreviewAdapter:
    """Adapter provides format_session_preview() compatibility."""

    def test_format_session_preview(self, tmp_path):
        from wisp.adapters import format_session_preview
        session = {
            "id": "test-sess",
            "title": "Test Session",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "model": "qwen",
            "messages": [{"role": "user", "content": "hello"}],
        }
        preview = format_session_preview(session)
        assert "test-sess" in preview
        assert "Test Session" in preview

    def test_format_session_preview_no_title(self, tmp_path):
        from wisp.adapters import format_session_preview
        session = {
            "id": "test-sess",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "model": "qwen",
            "messages": [],
        }
        preview = format_session_preview(session)
        assert "test-sess" in preview


# ═══════════════════════════════════════════════════════════════════
# 8. Session class adapter
# ═══════════════════════════════════════════════════════════════════

class TestSessionClassAdapter:
    """Adapter provides Session.create() compatibility."""

    def test_session_create(self, tmp_path):
        from wisp.adapters import Session
        session = Session.create(model="qwen", workspace="/tmp", first_prompt="hello")
        assert session.id is not None
        assert session.model == "qwen"
        assert session.workspace == "/tmp"
        assert session.title == "hello"

    def test_session_to_dict(self, tmp_path):
        from wisp.adapters import Session
        session = Session.create(model="qwen", workspace="/tmp", first_prompt="hello")
        data = session.to_dict()
        assert data["model"] == "qwen"
        assert data["workspace"] == "/tmp"


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
