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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

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

    # ── Schema ──────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with self._lock:
            conn = self._get_conn()
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
                """
            )
            # Schema migration: add title column if missing
            try:
                conn.execute("SELECT title FROM sessions LIMIT 1")
            except sqlite3.OperationalError:
                conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT NOT NULL DEFAULT ''")

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
            }
            for r in rows
        ]

    def delete_session(self, session_id: str) -> None:
        self._get_conn().execute("DELETE FROM sessions WHERE id = ?", (session_id,))

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
