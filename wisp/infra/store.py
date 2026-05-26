"""UnifiedStore — single SQLite persistence layer for all Wisp state.

Replaces: session.py, session_store.py, JSON file sessions, and ad-hoc
persistence scattered across the codebase.

Design:
  - ONE database per workspace (or global ~/.config/wisp/wisp.db)
  - SQLite with WAL mode for concurrent reads
  - All writes go through transaction() context manager
  - Sessions, runs, events, and memory are all in one schema
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class UnifiedStore:
    """Single SQLite store for sessions, runs, events, and memory."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self.name = "store"
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Lazy init: create parent dirs and schema on first connection."""
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError):
                pass
            try:
                self._init_schema()
                self._initialized = True
            except (PermissionError, OSError, sqlite3.OperationalError) as e:
                import tempfile
                fallback = Path(tempfile.gettempdir()) / "wisp_fallback.db"
                logger.warning(
                    "UnifiedStore: cannot open %s (%s) — falling back to %s",
                    self.db_path, e, fallback
                )
                self.db_path = fallback
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._init_schema()
                self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=5.0,
            isolation_level=None,  # autocommit — each statement is its own transaction
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _get_conn(self) -> sqlite3.Connection:
        """Get a connection for the current thread."""
        self._ensure_initialized()
        if not hasattr(self, "_local"):
            self._local = threading.local()
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = self._connect()
        return self._local.conn

    def start(self) -> None:
        """Lifecycle start — connections are lazily opened."""
        logger.debug("UnifiedStore started")

    def stop(self) -> None:
        """Lifecycle stop — close all thread-local connections."""
        if hasattr(self, "_local"):
            if hasattr(self._local, "conn") and self._local.conn is not None:
                self._local.conn.close()
                self._local.conn = None
        logger.debug("UnifiedStore stopped")

    def close(self) -> None:
        """Alias for stop() — backward compatibility."""
        self.stop()

    def healthy(self) -> bool:
        """Health check — ping the database."""
        try:
            conn = self._get_conn()
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    # ── Schema ──────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        # Called from _ensure_initialized which already holds the lock
        conn = self._connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    messages TEXT NOT NULL DEFAULT '[]',
                    compaction_history TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    data TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
                CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
                CREATE INDEX IF NOT EXISTS idx_memory_created ON memory(created_at);

                CREATE TABLE IF NOT EXISTS background_runs (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    model TEXT NOT NULL DEFAULT 'unknown',
                    workspace TEXT NOT NULL DEFAULT '.',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at REAL NOT NULL DEFAULT 0,
                    started_at REAL,
                    finished_at REAL,
                    content TEXT NOT NULL DEFAULT '',
                    tool_calls TEXT NOT NULL DEFAULT '[]',
                    files_changed TEXT NOT NULL DEFAULT '[]',
                    error TEXT,
                    iterations INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_bg_runs_status ON background_runs(status);

                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sequence_num INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_session_events_session
                    ON session_events(session_id, sequence_num);

                CREATE TABLE IF NOT EXISTS idempotency (
                    key TEXT PRIMARY KEY,
                    result TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_idempotency_created
                    ON idempotency(created_at);
                """
            )
            # Schema migration: add title column if missing
            try:
                conn.execute("SELECT title FROM sessions LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT NOT NULL DEFAULT ''")

            # Schema migration: add background_runs table if missing (older dbs)
            try:
                conn.execute("SELECT 1 FROM background_runs LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("""
                    CREATE TABLE background_runs (
                        id TEXT PRIMARY KEY,
                        prompt TEXT NOT NULL,
                        model TEXT NOT NULL DEFAULT 'unknown',
                        workspace TEXT NOT NULL DEFAULT '.',
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at REAL NOT NULL DEFAULT 0,
                        started_at REAL,
                        finished_at REAL,
                        content TEXT NOT NULL DEFAULT '',
                        tool_calls TEXT NOT NULL DEFAULT '[]',
                        files_changed TEXT NOT NULL DEFAULT '[]',
                        error TEXT,
                        iterations INTEGER NOT NULL DEFAULT 0
                    )
                """)
                conn.execute("CREATE INDEX idx_bg_runs_status ON background_runs(status)")
        finally:
            conn.close()

    def _list_tables(self) -> list[str]:
        cursor = self._get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        return [row["name"] for row in cursor.fetchall()]

    # ── Transactions ────────────────────────────────────────────────

    @contextmanager
    def transaction(self):
        """Atomic transaction context."""
        conn = self._get_conn()
        try:
            conn.execute("BEGIN")
            yield self
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    # ── Session CRUD ────────────────────────────────────────────────

    def create_session(self, session_id: str, model: str, workspace: str, title: str = "") -> dict:
        """Backward-compatible session creation."""
        now = datetime.now(timezone.utc).isoformat()
        session = {
            "id": session_id,
            "model": model,
            "workspace": workspace,
            "messages": [],
            "compaction_history": [],
            "created_at": now,
            "updated_at": now,
            "title": title,
        }
        self.save_session(session)
        return session

    def save_session(self, session: dict) -> None:
        self._ensure_initialized()
        with self._lock:
            data = {
                "id": session["id"],
                "model": session.get("model", ""),
                "workspace": session.get("workspace", ""),
                "title": session.get("title", ""),
                "messages": json.dumps(session.get("messages", [])),
                "compaction_history": json.dumps(session.get("compaction_history", [])),
                "created_at": session.get("created_at", ""),
                "updated_at": session.get("updated_at", ""),
            }
            self._get_conn().execute(
                """
                INSERT INTO sessions (id, model, workspace, title, messages, compaction_history, created_at, updated_at)
                VALUES (:id, :model, :workspace, :title, :messages, :compaction_history, :created_at, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                    model=excluded.model,
                    workspace=excluded.workspace,
                    title=excluded.title,
                    messages=excluded.messages,
                    compaction_history=excluded.compaction_history,
                    updated_at=excluded.updated_at
                """,
                data,
            )

    def load_session(self, session_id: str) -> Optional[dict]:
        row = self._get_conn().execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "model": row["model"],
            "workspace": row["workspace"],
            "title": row["title"],
            "messages": json.loads(row["messages"]),
            "compaction_history": json.loads(row["compaction_history"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self._get_conn().execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "model": r["model"],
                "workspace": r["workspace"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "msg_count": len(json.loads(r["messages"])) if r["messages"] else 0,
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> None:
        self._get_conn().execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def get_session_id_from_fragment(self, fragment: str) -> str | None:
        """Find session ID matching a fragment."""
        sessions = self.list_sessions()
        for s in sessions:
            if fragment.lower() in s.get("id", "").lower():
                return s["id"]
        return None

    def update_run_status(self, run_id: str, status: str) -> None:
        """Update a run's status."""
        run = self.load_run(run_id)
        if run is not None:
            run["status"] = status
            self.save_run(run)

    # ── Run CRUD ────────────────────────────────────────────────────

    def create_run(self, session_id: str, prompt: str, model: str = "unknown") -> str:
        """Backward-compatible run creation."""
        import uuid
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        run = {
            "id": run_id,
            "session_id": session_id,
            "prompt": prompt,
            "model": model,
            "status": "pending",
            "events": [],
            "created_at": now,
        }
        self.save_run(run)
        return run_id

    def save_run(self, run: dict) -> None:
        data = {
            "id": run["id"],
            "session_id": run["session_id"],
            "prompt": run.get("prompt", ""),
            "status": run.get("status", "pending"),
            "created_at": run.get("created_at", ""),
        }
        self._get_conn().execute(
            """
            INSERT INTO runs (id, session_id, prompt, status, created_at)
            VALUES (:id, :session_id, :prompt, :status, :created_at)
            ON CONFLICT(id) DO UPDATE SET
                session_id=excluded.session_id,
                prompt=excluded.prompt,
                status=excluded.status
            """,
            data,
        )
        # Inline events: delete old, insert new
        self._get_conn().execute("DELETE FROM events WHERE run_id = ?", (run["id"],))
        for ev in run.get("events", []):
            self._get_conn().execute(
                "INSERT INTO events (run_id, type, data) VALUES (?, ?, ?)",
                (run["id"], ev.get("type", ""), json.dumps(ev)),
            )

    def load_run(self, run_id: str) -> Optional[dict]:
        row = self._get_conn().execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        events = self._get_conn().execute(
            "SELECT data FROM events WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "prompt": row["prompt"],
            "status": row["status"],
            "created_at": row["created_at"],
            "events": [json.loads(e["data"]) for e in events],
        }

    def list_runs(self, session_id: str | None = None) -> list[dict]:
        if session_id:
            rows = self._get_conn().execute(
                "SELECT * FROM runs WHERE session_id = ? ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        else:
            rows = self._get_conn().execute(
                "SELECT * FROM runs ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "prompt": r["prompt"],
                "status": r["status"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ── Memory ──────────────────────────────────────────────────────

    def save_memory(self, content: str, importance: int = 1) -> None:
        from datetime import datetime, timezone
        self._get_conn().execute(
            "INSERT INTO memory (content, importance, created_at) VALUES (?, ?, ?)",
            (content, importance, datetime.now(timezone.utc).isoformat()),
        )

    def recall_memory(self, query: str, limit: int = 5) -> list[dict]:
        # Simple substring search for now; can be upgraded to FTS
        rows = self._get_conn().execute(
            "SELECT * FROM memory WHERE content LIKE ? ORDER BY importance DESC, created_at DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        return [
            {"id": r["id"], "content": r["content"], "importance": r["importance"], "created_at": r["created_at"]}
            for r in rows
        ]

    def list_memory(self) -> list[dict]:
        rows = self._get_conn().execute(
            "SELECT * FROM memory ORDER BY created_at DESC"
        ).fetchall()
        return [
            {"id": r["id"], "content": r["content"], "importance": r["importance"], "created_at": r["created_at"]}
            for r in rows
        ]

    def evict_memory(self, keep: int = 100) -> None:
        self._get_conn().execute(
            """
            DELETE FROM memory WHERE id NOT IN (
                SELECT id FROM memory ORDER BY importance DESC, created_at DESC LIMIT ?
            )
            """,
            (keep,),
        )

    # ── Background runs ─────────────────────────────────────────────

    def bg_create(self, run: dict) -> None:
        """Create a background run record."""
        self._get_conn().execute(
            """
            INSERT INTO background_runs (id, prompt, model, workspace, status, created_at)
            VALUES (:id, :prompt, :model, :workspace, :status, :created_at)
            """,
            {
                "id": run["id"],
                "prompt": run.get("prompt", ""),
                "model": run.get("model", "unknown"),
                "workspace": run.get("workspace", "."),
                "status": run.get("status", "pending"),
                "created_at": run.get("created_at", 0),
            },
        )

    def bg_get(self, run_id: str) -> dict | None:
        """Get a background run by ID."""
        row = self._get_conn().execute(
            "SELECT * FROM background_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["id"],
            "prompt": row["prompt"],
            "model": row["model"],
            "workspace": row["workspace"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "content": row["content"],
            "tool_calls": json.loads(row["tool_calls"]),
            "files_changed": json.loads(row["files_changed"]),
            "error": row["error"],
            "iterations": row["iterations"],
        }

    def bg_update(self, run_id: str, **kwargs) -> None:
        """Update fields of a background run."""
        allowed = {"status", "started_at", "finished_at", "content", "error", "iterations", "tool_calls", "files_changed"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return
        # Serialize JSON fields
        if "tool_calls" in fields:
            fields["tool_calls"] = json.dumps(fields["tool_calls"])
        if "files_changed" in fields:
            fields["files_changed"] = json.dumps(fields["files_changed"])
        sets = ", ".join(f"{k} = :{k}" for k in fields)
        fields["id"] = run_id
        self._get_conn().execute(
            f"UPDATE background_runs SET {sets} WHERE id = :id",
            fields,
        )

    def bg_delete(self, run_id: str) -> None:
        """Delete a background run by ID."""
        self._get_conn().execute(
            "DELETE FROM background_runs WHERE id = ?",
            (run_id,),
        )

    def bg_list(self) -> list[dict]:
        """List all background runs."""
        rows = self._get_conn().execute(
            "SELECT * FROM background_runs ORDER BY created_at DESC"
        ).fetchall()
        return [
            {
                "run_id": r["id"],
                "prompt": r["prompt"],
                "model": r["model"],
                "workspace": r["workspace"],
                "status": r["status"],
                "created_at": r["created_at"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "content": r["content"],
                "tool_calls": json.loads(r["tool_calls"]),
                "files_changed": json.loads(r["files_changed"]),
                "error": r["error"],
                "iterations": r["iterations"],
            }
            for r in rows
        ]


# ── format_session_preview (migrated from adapters.py) ──────────────


def format_session_preview(session) -> str:
    """Format a session for display in a list. Accepts both dict and SessionDTO."""
    if hasattr(session, "to_dict"):
        session = session.to_dict()
    sid = session.get("id", "unknown")
    title = session.get("title", "")
    updated = session.get("updated_at", "")
    model = session.get("model", "")
    msg_count = len(session.get("messages", []))

    parts = [sid]
    if title:
        parts.append(f"'{title}'")
    if updated:
        parts.append(f"updated {updated[:19]}")
    if model:
        parts.append(f"model={model}")
    parts.append(f"{msg_count} messages")

    return " | ".join(parts)


# ── get_store (migrated from adapters.py) ───────────────────────────

_store_cache: dict[str, UnifiedStore] = {}


def get_store(db_path: str | None = None) -> UnifiedStore:
    """Get or create a UnifiedStore instance."""
    if db_path is None:
        db_path = str(Path.home() / ".config" / "wisp" / "wisp.db")

    if db_path not in _store_cache:
        _store_cache[db_path] = UnifiedStore(db_path)
    return _store_cache[db_path]
