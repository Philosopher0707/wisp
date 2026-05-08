"""SQLite-backed storage for thread and run metadata."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ThreadRecord:
    id: str
    title: str
    workspace: str
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RunRecord:
    id: str
    thread_id: str
    prompt: str
    status: str
    created_at: str
    updated_at: str


class SQLiteStateStore:
    """Small SQLite store for terminal app state."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES threads(id)
                );
                """
            )

    def create_thread(self, title: str, workspace: str, status: str = "idle") -> ThreadRecord:
        thread = ThreadRecord(
            id=f"thread-{uuid.uuid4().hex[:12]}",
            title=title,
            workspace=workspace,
            status=status,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO threads (id, title, workspace, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    thread.id,
                    thread.title,
                    thread.workspace,
                    thread.status,
                    thread.created_at,
                    thread.updated_at,
                ),
            )
        return thread

    def list_threads(self) -> list[ThreadRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, title, workspace, status, created_at, updated_at
                FROM threads
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [ThreadRecord(**dict(row)) for row in rows]

    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, title, workspace, status, created_at, updated_at
                FROM threads
                WHERE id = ?
                """,
                (thread_id,),
            ).fetchone()
        return ThreadRecord(**dict(row)) if row else None

    def create_run(self, thread_id: str, prompt: str, status: str = "queued") -> RunRecord:
        run = RunRecord(
            id=f"run-{uuid.uuid4().hex[:12]}",
            thread_id=thread_id,
            prompt=prompt,
            status=status,
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (id, thread_id, prompt, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.thread_id,
                    run.prompt,
                    run.status,
                    run.created_at,
                    run.updated_at,
                ),
            )
        return run

    def update_run_status(self, run_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, _now_iso(), run_id),
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, thread_id, prompt, status, created_at, updated_at
                FROM runs
                WHERE id = ?
                """,
                (run_id,),
            ).fetchone()
        return RunRecord(**dict(row)) if row else None
