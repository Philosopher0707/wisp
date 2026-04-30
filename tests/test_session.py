"""Tests for session.py — session CRUD, fragment resolution, formatting."""

import pytest
import json
from pathlib import Path
from wisp.session import Session, SessionManager, format_session_preview, SESSIONS_DIR


class TestSession:

    def test_create(self):
        s = Session.create("deepseek-v4", "/workspace", "hello world")
        assert s.model == "deepseek-v4"
        assert s.workspace == "/workspace"
        assert s.title == "hello world"
        assert s.id.endswith("-hello-world")
        assert s.messages == []

    def test_to_dict_roundtrip(self):
        s = Session.create("m", ".", "test")
        s.messages = [{"role": "user", "content": "hi"}]
        d = s.to_dict()
        s2 = Session.from_dict(d)
        assert s2.id == s.id
        assert s2.model == s.model
        assert s2.messages == s.messages

    def test_slugify_special_chars(self):
        from wisp.session import _slugify
        assert _slugify("Hello World!") == "hello-world"
        assert _slugify("  spaces  ") == "spaces"
        assert _slugify("a" * 100) == "a" * 40

    def test_touch(self):
        s = Session.create("m", ".", "t")
        before = s.updated_at
        s.touch()
        assert s.updated_at >= before


class TestSessionManager:

    @pytest.fixture(autouse=True)
    def isolate_sessions(self, tmp_path, monkeypatch):
        """Point sessions dir to a temp dir so tests don't touch ~/.config/wisp."""
        import wisp.session as sess_mod
        test_dir = tmp_path / "sessions"
        sess_mod.SESSIONS_DIR = test_dir
        yield

    @pytest.fixture
    def mgr(self):
        return SessionManager()

    def test_save_and_load(self, mgr):
        s = Session.create("model", ".", "test prompt")
        mgr.save(s)
        loaded = mgr.load(s.id)
        assert loaded is not None
        assert loaded.id == s.id
        assert loaded.model == "model"

    def test_load_nonexistent(self, mgr):
        assert mgr.load("nonexistent") is None

    def test_delete(self, mgr):
        s = Session.create("m", ".", "del")
        mgr.save(s)
        assert mgr.delete(s.id) is True
        assert mgr.load(s.id) is None

    def test_delete_nonexistent(self, mgr):
        assert mgr.delete("nope") is False

    def test_list_sessions(self, mgr):
        s1 = Session.create("m1", ".", "first")
        s2 = Session.create("m2", ".", "second")
        mgr.save(s1)
        mgr.save(s2)
        sessions = mgr.list_sessions()
        assert len(sessions) == 2

    def test_fragment_resolution_exact(self, mgr):
        s = Session.create("m", ".", "exact")
        mgr.save(s)
        resolved = mgr.get_session_id_from_fragment(s.id)
        assert resolved == s.id

    def test_fragment_resolution_partial(self, mgr):
        s = Session.create("m", ".", "partial test")
        mgr.save(s)
        # The ID format is YYYYMMDD-HHMMSS-partial-test
        # Fragment match on the date prefix
        prefix = s.id[:8]
        resolved = mgr.get_session_id_from_fragment(prefix)
        assert resolved == s.id

    def test_fragment_ambiguous(self, mgr):
        """Two sessions with same prefix should return None."""
        s1 = Session(id="20260430-120000-aaa", created_at="2026-04-30T12:00:00",
                      updated_at="2026-04-30T12:00:00", model="m", workspace=".")
        s2 = Session(id="20260430-120001-bbb", created_at="2026-04-30T12:00:01",
                      updated_at="2026-04-30T12:00:01", model="m", workspace=".")
        mgr.save(s1)
        mgr.save(s2)
        resolved = mgr.get_session_id_from_fragment("20260430")
        assert resolved is None  # ambiguous

    def test_list_sessions_limit(self, mgr):
        for i in range(5):
            s = Session.create("m", ".", f"session {i}")
            mgr.save(s)
        sessions = mgr.list_sessions(limit=3)
        assert len(sessions) <= 3

    def test_list_sessions_empty(self, mgr):
        assert mgr.list_sessions() == []


class TestFormatSessionPreview:

    def test_format_preview(self):
        s = Session.create("m", ".", "my session")
        s.messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        preview = format_session_preview(s)
        assert "my session" in preview
        assert "hello" in preview
        assert "world" in preview
        assert s.id in preview
