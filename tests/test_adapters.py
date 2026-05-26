"""Tests for migrated adapter functionality.

After Phase 7.1, these test the new canonical module locations:
  - get_store → wisp.infra.store
  - format_session_preview → wisp.infra.store
  - SessionDTO → wisp.infra.session_dto
"""



# ═══════════════════════════════════════════════════════════════════
# Store adapter — get_store() migrated to wisp.infra.store
# ═══════════════════════════════════════════════════════════════════

class TestStoreAdapter:
    """get_store() now lives in wisp.infra.store."""

    def test_get_store_returns_store(self, tmp_path):
        from wisp.infra.store import get_store
        store = get_store(str(tmp_path / "test.db"))
        assert store is not None
        assert hasattr(store, "load_session")
        assert hasattr(store, "save_session")

    def test_get_store_singleton(self, tmp_path):
        from wisp.infra.store import get_store
        db_path = str(tmp_path / "test.db")
        store1 = get_store(db_path)
        store2 = get_store(db_path)
        assert store1 is store2


# ═══════════════════════════════════════════════════════════════════
# Session preview adapter — format_session_preview migrated
# ═══════════════════════════════════════════════════════════════════

class TestSessionPreviewAdapter:
    """format_session_preview() now lives in wisp.infra.store."""

    def test_format_session_preview(self, tmp_path):
        from wisp.infra.store import format_session_preview
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
        from wisp.infra.store import format_session_preview
        session = {
            "id": "test-sess",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "model": "qwen",
            "messages": [],
        }
        preview = format_session_preview(session)
        assert "test-sess" in preview


# ═══════════════════════════════════════════════════════════════════
# Session class adapter — Session → SessionDTO migrated
# ═══════════════════════════════════════════════════════════════════

class TestSessionClassAdapter:
    """SessionDTO.create() now lives in wisp.infra.session_dto."""

    def test_session_create(self, tmp_path):
        from wisp.infra.session_dto import SessionDTO
        session = SessionDTO.create(model="qwen", workspace="/tmp", first_prompt="hello")
        assert session.id is not None
        assert session.model == "qwen"
        assert session.workspace == "/tmp"
        assert session.title == "hello"

    def test_session_to_dict(self, tmp_path):
        from wisp.infra.session_dto import SessionDTO
        session = SessionDTO.create(model="qwen", workspace="/tmp", first_prompt="hello")
        data = session.to_dict()
        assert data["model"] == "qwen"
        assert data["workspace"] == "/tmp"
