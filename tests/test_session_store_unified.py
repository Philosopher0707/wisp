"""Tests for UnifiedSessionStore — session persistence unification.

Exposes and fixes the mismatch between UnifiedSessionStore and WispAgentCore.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestUnifiedSessionStoreBasics:

    def test_can_create_store(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert store.sessions_dir == tmp_path
        assert tmp_path.exists()

    def test_create_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        assert session.id
        assert session.model == "llama3"
        assert session.workspace == "."
        assert session.title == "hello"

    def test_load_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        created = store.create_session(model="llama3", workspace=".", title="hello")
        loaded = store.load_session(created.id)
        assert loaded is not None
        assert loaded.id == created.id
        assert loaded.title == "hello"

    def test_load_nonexistent_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert store.load_session("nonexistent") is None

    def test_save_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        from wisp.session import Session
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        session.messages.append({"role": "user", "content": "hi"})
        store.save_session(session)
        loaded = store.load_session(session.id)
        assert loaded.messages[0]["content"] == "hi"

    def test_save_alias(self, tmp_path):
        """save() is a backward-compatible alias for save_session()."""
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        # save() should work as alias
        store.save(session)
        loaded = store.load_session(session.id)
        assert loaded is not None

    def test_delete_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        assert store.delete_session(session.id) is True
        assert store.load_session(session.id) is None

    def test_delete_nonexistent_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert store.delete_session("nonexistent") is False

    def test_list_sessions(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        store.create_session(model="llama3", workspace=".", title="first")
        store.create_session(model="llama3", workspace=".", title="second")
        sessions = store.list_sessions()
        assert len(sessions) == 2
        # Newest first
        assert sessions[0]["title"] == "second"
        assert sessions[1]["title"] == "first"

    def test_list_sessions_limit(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        for i in range(5):
            store.create_session(model="llama3", workspace=".", title=f"session-{i}")
        sessions = store.list_sessions(limit=3)
        assert len(sessions) == 3


class TestUnifiedSessionStoreFragmentResolution:

    def test_resolve_session_id(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        # Resolve by prefix
        resolved = store.resolve_session_id(session.id[:8])
        assert resolved == session.id

    def test_resolve_ambiguous_prefix(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        store.create_session(model="llama3", workspace=".", title="first")
        store.create_session(model="llama3", workspace=".", title="second")
        # Empty prefix matches everything — ambiguous
        assert store.resolve_session_id("") is None

    def test_resolve_nonexistent(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert store.resolve_session_id("nonexistent") is None


class TestUnifiedSessionStoreBackwardCompat:
    """Ensure UnifiedSessionStore is a drop-in replacement for SessionManager."""

    def test_has_load_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert hasattr(store, "load_session")

    def test_has_save_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert hasattr(store, "save_session")

    def test_has_delete_session(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert hasattr(store, "delete_session")

    def test_has_list_sessions(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert hasattr(store, "list_sessions")

    def test_has_resolve_session_id(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        assert hasattr(store, "resolve_session_id")

    def test_get_session_id_from_fragment_alias(self, tmp_path):
        """WispAgentCore calls get_session_id_from_fragment — this MUST work."""
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        # This is what WispAgentCore calls
        resolved = store.get_session_id_from_fragment(session.id[:8])
        assert resolved == session.id


class TestUnifiedSessionStoreRuns:

    def test_create_run(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        run = store.create_run(session.id, prompt="refactor auth.py")
        assert run.id.startswith("run-")
        assert run.session_id == session.id
        assert run.prompt == "refactor auth.py"
        assert run.status == "queued"

    def test_get_run(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        run = store.create_run(session.id, prompt="refactor auth.py")
        loaded = store.get_run(run.id)
        assert loaded is not None
        assert loaded.id == run.id

    def test_list_runs(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        store.create_run(session.id, prompt="first")
        store.create_run(session.id, prompt="second")
        runs = store.list_runs(session.id)
        assert len(runs) == 2

    def test_update_run_status(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        run = store.create_run(session.id, prompt="refactor auth.py")
        assert store.update_run_status(run.id, "running") is True
        loaded = store.get_run(run.id)
        assert loaded.status == "running"

    def test_append_event(self, tmp_path):
        from wisp.session_store import UnifiedSessionStore
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="hello")
        run = store.create_run(session.id, prompt="refactor auth.py")
        assert store.append_event(run.id, {"event": "tool_call", "name": "read_file"}) is True
        events = store.read_events(run.id)
        assert len(events) == 1
        assert events[0]["name"] == "read_file"


class TestAcpSessionManager:

    def test_create_acp_session(self, tmp_path):
        from wisp.acp_session import AcpSessionManager
        from wisp.config import WispConfig
        mgr = AcpSessionManager()
        config = WispConfig()
        config.model = "llama3"
        session = mgr.create(workspace=".", config=config, title="test")
        assert session.session_id.startswith("wisp-")
        assert session.title == "test"

    def test_get_active_session(self, tmp_path):
        from wisp.acp_session import AcpSessionManager
        from wisp.config import WispConfig
        mgr = AcpSessionManager()
        config = WispConfig()
        config.model = "llama3"
        created = mgr.create(workspace=".", config=config, title="test")
        retrieved = mgr.get(created.session_id)
        assert retrieved is not None
        assert retrieved.session_id == created.session_id

    def test_load_from_disk(self, tmp_path):
        """AcpSessionManager can load sessions from disk."""
        from wisp.acp_session import AcpSessionManager
        from wisp.config import WispConfig
        from wisp.session_store import UnifiedSessionStore

        # Create a session directly in the store
        store = UnifiedSessionStore(sessions_dir=tmp_path)
        session = store.create_session(model="llama3", workspace=".", title="persisted")
        session.messages.append({"role": "user", "content": "hello"})
        store.save_session(session)

        # Now load via AcpSessionManager
        mgr = AcpSessionManager(store=store)
        loaded = mgr.load(session.id)
        assert loaded is not None
        assert loaded.session_id == session.id
        assert len(loaded.messages) == 1
        assert loaded.messages[0]["content"] == "hello"

    def test_save_to_disk(self, tmp_path):
        from wisp.acp_session import AcpSessionManager
        from wisp.config import WispConfig
        from wisp.session_store import UnifiedSessionStore

        store = UnifiedSessionStore(sessions_dir=tmp_path)
        mgr = AcpSessionManager(store=store)
        config = WispConfig()
        config.model = "llama3"
        session = mgr.create(workspace=".", config=config, title="test")
        session.messages.append({"role": "user", "content": "hello"})

        # Save to disk
        assert mgr.save(session.session_id) is True

        # Verify by loading fresh
        loaded = store.load_session(session.session_id)
        assert loaded is not None
        assert len(loaded.messages) == 1

    def test_delete_session(self, tmp_path):
        from wisp.acp_session import AcpSessionManager
        from wisp.config import WispConfig
        from wisp.session_store import UnifiedSessionStore

        store = UnifiedSessionStore(sessions_dir=tmp_path)
        mgr = AcpSessionManager(store=store)
        config = WispConfig()
        config.model = "llama3"
        session = mgr.create(workspace=".", config=config, title="test")

        assert mgr.delete(session.session_id) is True
        assert mgr.get(session.session_id) is None
