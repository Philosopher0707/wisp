"""Tests for UnifiedSessionStore — session/run/event unification."""

import json
import pytest
from pathlib import Path

from wisp.session_store import UnifiedSessionStore, Run, get_store
from wisp.session import Session


class TestUnifiedSessionStore:

    @pytest.fixture
    def store(self, tmp_path):
        return UnifiedSessionStore(sessions_dir=tmp_path / "sessions")

    def test_create_session(self, store):
        session = store.create_session(model="llama3", workspace="/tmp", title="hello world")
        assert session.model == "llama3"
        assert session.workspace == "/tmp"
        assert session.title == "hello world"
        assert session.messages == []
        assert session.id.startswith("20")  # timestamp prefix

    def test_load_session(self, store):
        created = store.create_session(model="m", workspace=".", title="t")
        loaded = store.load_session(created.id)
        assert loaded is not None
        assert loaded.id == created.id
        assert loaded.model == "m"

    def test_load_nonexistent(self, store):
        assert store.load_session("nonexistent") is None

    def test_delete_session(self, store):
        session = store.create_session(model="m", workspace=".", title="del")
        assert store.delete_session(session.id) is True
        assert store.load_session(session.id) is None

    def test_delete_nonexistent(self, store):
        assert store.delete_session("nope") is False

    def test_list_sessions(self, store):
        s1 = store.create_session(model="m1", workspace=".", title="first")
        s2 = store.create_session(model="m2", workspace=".", title="second")
        sessions = store.list_sessions()
        ids = [s["id"] for s in sessions]
        assert s1.id in ids
        assert s2.id in ids

    def test_resolve_session_id(self, store):
        session = store.create_session(model="m", workspace=".", title="res")
        resolved = store.resolve_session_id(session.id[:20])
        assert resolved == session.id

    def test_create_run(self, store):
        session = store.create_session(model="m", workspace=".", title="run")
        run = store.create_run(session.id, prompt="do something")
        assert run.session_id == session.id
        assert run.prompt == "do something"
        assert run.status == "queued"
        assert run.id.startswith("run-")

    def test_get_run(self, store):
        session = store.create_session(model="m", workspace=".", title="r")
        run = store.create_run(session.id, prompt="p")
        loaded = store.get_run(run.id)
        assert loaded is not None
        assert loaded.id == run.id
        assert loaded.prompt == "p"

    def test_get_run_nonexistent(self, store):
        assert store.get_run("run-nonexistent") is None

    def test_update_run_status(self, store):
        session = store.create_session(model="m", workspace=".", title="u")
        run = store.create_run(session.id, prompt="p")
        ok = store.update_run_status(run.id, "running")
        assert ok is True
        loaded = store.get_run(run.id)
        assert loaded.status == "running"

    def test_list_runs(self, store):
        session = store.create_session(model="m", workspace=".", title="lr")
        r1 = store.create_run(session.id, prompt="first")
        r2 = store.create_run(session.id, prompt="second")
        runs = store.list_runs(session.id)
        assert len(runs) == 2
        assert runs[0].prompt == "first"
        assert runs[1].prompt == "second"

    def test_append_and_read_events(self, store):
        session = store.create_session(model="m", workspace=".", title="e")
        run = store.create_run(session.id, prompt="p")
        ok = store.append_event(run.id, {"event": "tool_call", "name": "read_file"})
        assert ok is True
        events = store.read_events(run.id)
        assert len(events) == 1
        assert events[0]["event"] == "tool_call"
        assert events[0]["name"] == "read_file"
        assert "_logged_at" in events[0]

    def test_append_event_nonexistent_run(self, store):
        assert store.append_event("run-fake", {"event": "x"}) is False

    def test_delete_session_cleans_runs(self, store):
        session = store.create_session(model="m", workspace=".", title="clean")
        run = store.create_run(session.id, prompt="p")
        assert store.get_run(run.id) is not None
        store.delete_session(session.id)
        assert store.get_run(run.id) is None

    def test_run_roundtrip(self, store):
        session = store.create_session(model="m", workspace=".", title="rt")
        run = store.create_run(session.id, prompt="p")
        run.events = [{"event": "start"}, {"event": "done"}]
        store._save_run(run)
        loaded = store.get_run(run.id)
        assert loaded.events == [{"event": "start"}, {"event": "done"}]

    def test_singleton(self, tmp_path, monkeypatch):
        import wisp.session_store as ss
        ss._store = None
        store = get_store()
        assert store is not None
        assert ss._store is store


class TestMigration:

    def test_migrate_from_sqlite(self, tmp_path):
        """Test migrating SQLite threads/runs to JSON sessions."""
        import sqlite3
        db_path = tmp_path / "app.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, workspace TEXT,
                status TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE runs (id TEXT PRIMARY KEY, thread_id TEXT, prompt TEXT,
                status TEXT, created_at TEXT, updated_at TEXT);
        """)
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
            ("thread-abc", "Test Thread", "/tmp", "idle", "2025-01-01T00:00:00", "2025-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
            ("run-xyz", "thread-abc", "hello", "completed", "2025-01-01T00:00:00", "2025-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        store = UnifiedSessionStore(sessions_dir=tmp_path / "sessions")
        migrated = store.migrate_from_sqlite(db_path)
        assert migrated == 1

        session = store.load_session("thread-abc")
        assert session is not None
        assert session.title == "Test Thread"
        assert session.workspace == "/tmp"

        runs = store.list_runs("thread-abc")
        assert len(runs) == 1
        assert runs[0].id == "run-xyz"
        assert runs[0].prompt == "hello"

    def test_migrate_idempotent(self, tmp_path):
        """Migration should skip already-existing sessions."""
        import sqlite3
        db_path = tmp_path / "app.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, workspace TEXT,
                status TEXT, created_at TEXT, updated_at TEXT);
        """)
        conn.execute(
            "INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)",
            ("thread-dup", "Dup", "/tmp", "idle", "2025-01-01T00:00:00", "2025-01-01T00:00:00"),
        )
        conn.commit()
        conn.close()

        store = UnifiedSessionStore(sessions_dir=tmp_path / "sessions")
        store.create_session(model="m", workspace=".", title="t", session_id="thread-dup")
        migrated = store.migrate_from_sqlite(db_path)
        assert migrated == 0  # skipped because already exists

    def test_migrate_no_db(self, tmp_path):
        store = UnifiedSessionStore(sessions_dir=tmp_path / "sessions")
        migrated = store.migrate_from_sqlite(tmp_path / "nonexistent.db")
        assert migrated == 0
