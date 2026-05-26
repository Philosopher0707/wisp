"""TDD for UnifiedStore — the single SQLite persistence layer.

Red phase: these tests define the interface and will fail until
wisp/infra/store.py is implemented.
"""

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ── Test fixtures ──────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Return a fresh database path that will be cleaned up."""
    return tmp_path / "wisp.db"


@pytest.fixture
def store(tmp_db):
    """Return a fresh UnifiedStore instance."""
    from wisp.infra.store import UnifiedStore
    return UnifiedStore(tmp_db)


# ── Minimal data classes for testing ───────────────────────────────

@dataclass
class _TestSession:
    id: str
    model: str = "qwen2.5-coder"
    workspace: str = "/tmp/test"
    messages: list[dict] = field(default_factory=list)
    compaction_history: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "model": self.model,
            "workspace": self.workspace,
            "messages": self.messages,
            "compaction_history": self.compaction_history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "_TestSession":
        return cls(
            id=d["id"],
            model=d["model"],
            workspace=d["workspace"],
            messages=d.get("messages", []),
            compaction_history=d.get("compaction_history", []),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
        )


@dataclass
class _TestRun:
    id: str
    session_id: str
    prompt: str = "test"
    status: str = "pending"
    events: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "prompt": self.prompt,
            "status": self.status,
            "events": self.events,
            "created_at": self.created_at,
        }


# ═══════════════════════════════════════════════════════════════════
# 1. Construction and schema
# ═══════════════════════════════════════════════════════════════════

class TestStoreConstruction:
    """UnifiedStore creates its schema on first open."""

    def test_creates_database_file(self, tmp_db):
        from wisp.infra.store import UnifiedStore
        store = UnifiedStore(tmp_db)
        store._ensure_initialized()  # lazy init on first use
        assert tmp_db.exists()

    def test_creates_sessions_table(self, tmp_db):
        from wisp.infra.store import UnifiedStore
        store = UnifiedStore(tmp_db)
        tables = store._list_tables()
        assert "sessions" in tables

    def test_creates_runs_table(self, tmp_db):
        from wisp.infra.store import UnifiedStore
        store = UnifiedStore(tmp_db)
        tables = store._list_tables()
        assert "runs" in tables

    def test_creates_events_table(self, tmp_db):
        from wisp.infra.store import UnifiedStore
        store = UnifiedStore(tmp_db)
        tables = store._list_tables()
        assert "events" in tables


# ═══════════════════════════════════════════════════════════════════
# 2. Session CRUD
# ═══════════════════════════════════════════════════════════════════

class TestSessionCrud:
    """Sessions can be saved, loaded, listed, and deleted."""

    def test_save_and_load_session(self, store):
        session = _TestSession(id="sess-1", messages=[{"role": "user", "content": "hi"}])
        store.save_session(session.to_dict())

        loaded = store.load_session("sess-1")
        assert loaded is not None
        assert loaded["id"] == "sess-1"
        assert loaded["messages"][0]["content"] == "hi"

    def test_load_missing_session_returns_none(self, store):
        assert store.load_session("nonexistent") is None

    def test_list_sessions_newest_first(self, store):
        store.save_session(_TestSession(id="sess-a").to_dict())
        store.save_session(_TestSession(id="sess-b").to_dict())

        sessions = store.list_sessions(limit=10)
        ids = [s["id"] for s in sessions]
        assert ids == ["sess-b", "sess-a"]

    def test_delete_session(self, store):
        store.save_session(_TestSession(id="sess-del").to_dict())
        assert store.load_session("sess-del") is not None

        store.delete_session("sess-del")
        assert store.load_session("sess-del") is None

    def test_update_session_overwrites(self, store):
        store.save_session(_TestSession(id="sess-up", messages=[{"role": "user", "content": "old"}]).to_dict())
        store.save_session(_TestSession(id="sess-up", messages=[{"role": "user", "content": "new"}]).to_dict())

        loaded = store.load_session("sess-up")
        assert loaded["messages"][0]["content"] == "new"


# ═══════════════════════════════════════════════════════════════════
# 3. Run + Event persistence
# ═══════════════════════════════════════════════════════════════════

class TestRunPersistence:
    """Runs and their events are persisted atomically."""

    def test_save_and_load_run(self, store):
        store.save_session(_TestSession(id="sess-1").to_dict())
        run = _TestRun(id="run-1", session_id="sess-1", prompt="hello")
        store.save_run(run.to_dict())

        loaded = store.load_run("run-1")
        assert loaded is not None
        assert loaded["prompt"] == "hello"
        assert loaded["session_id"] == "sess-1"

    def test_run_events_are_loaded_inline(self, store):
        store.save_session(_TestSession(id="sess-1").to_dict())
        run = _TestRun(
            id="run-2",
            session_id="sess-1",
            events=[
                {"type": "content", "text": "hello"},
                {"type": "tool_call", "name": "read_file"},
            ],
        )
        store.save_run(run.to_dict())

        loaded = store.load_run("run-2")
        assert len(loaded["events"]) == 2
        assert loaded["events"][1]["name"] == "read_file"

    def test_list_runs_by_session(self, store):
        store.save_session(_TestSession(id="sess-x").to_dict())
        store.save_session(_TestSession(id="sess-y").to_dict())
        store.save_run(_TestRun(id="run-a", session_id="sess-x").to_dict())
        store.save_run(_TestRun(id="run-b", session_id="sess-x").to_dict())
        store.save_run(_TestRun(id="run-c", session_id="sess-y").to_dict())

        runs = store.list_runs(session_id="sess-x")
        assert len(runs) == 2
        assert {r["id"] for r in runs} == {"run-a", "run-b"}


# ═══════════════════════════════════════════════════════════════════
# 4. Atomic transactions
# ═══════════════════════════════════════════════════════════════════

class TestTransactions:
    """Writes are atomic across tables."""

    def test_session_and_run_committed_together(self, store):
        session = _TestSession(id="sess-tx").to_dict()
        run = _TestRun(id="run-tx", session_id="sess-tx").to_dict()

        with store.transaction():
            store.save_session(session)
            store.save_run(run)

        assert store.load_session("sess-tx") is not None
        assert store.load_run("run-tx") is not None

    def test_transaction_rollback_on_error(self, store):
        session = _TestSession(id="sess-rollback").to_dict()

        with pytest.raises(RuntimeError):
            with store.transaction():
                store.save_session(session)
                raise RuntimeError("boom")

        assert store.load_session("sess-rollback") is None


# ═══════════════════════════════════════════════════════════════════
# 5. Memory persistence (for remember/recall)
# ═══════════════════════════════════════════════════════════════════

class TestMemoryPersistence:
    """Memory facts survive across sessions."""

    def test_save_and_recall_memory(self, store):
        store.save_memory("convention: use snake_case", importance=2)
        store.save_memory("preference: dark mode", importance=1)

        facts = store.recall_memory("convention", limit=5)
        assert len(facts) == 1
        assert "snake_case" in facts[0]["content"]

    def test_memory_eviction_by_age(self, store):
        import time
        store.save_memory("old fact", importance=1)
        time.sleep(0.01)
        store.save_memory("new fact", importance=1)

        # Evict oldest 1 fact
        store.evict_memory(keep=1)
        facts = store.list_memory()
        assert len(facts) == 1
        assert facts[0]["content"] == "new fact"
