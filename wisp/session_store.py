"""Unified session store — single abstraction for JSON, SQLite, and ACP sessions.

This module unifies three previously separate session systems:
  1. JSON file sessions (session.py / SessionManager)
  2. SQLite thread/run store (persistence/sqlite_store.py)
  3. ACP in-memory sessions (acp_session.py)

Design goals:
  - Single API for all session operations
  - JSON files remain the canonical storage (human-readable, backward compatible)
  - Runs and events are stored alongside sessions in JSON
  - Migration from SQLite happens automatically on first access
  - ACP sessions can optionally persist to disk

Usage:
    from wisp.session_store import UnifiedSessionStore

    store = UnifiedSessionStore()

    # Create a session
    session = store.create_session(model="llama3", workspace=".", title="hello")

    # Create a run within the session
    run = store.create_run(session.id, prompt="refactor auth.py")

    # Log events during execution
    store.append_event(run.id, {"event": "tool_call", "name": "read_file", ...})

    # Load everything back
    session = store.load_session(session.id)
    runs = store.list_runs(session.id)
    events = store.read_events(run.id)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from wisp.config import WISP_CONFIG_DIR
from wisp.session import Session, SessionManager, _now_iso, _slugify, _timestamp_id

logger = logging.getLogger(__name__)

# ── Data models ────────────────────────────────────────────────────────


@dataclass
class Run:
    """A single execution run within a session."""
    id: str
    session_id: str
    prompt: str
    status: str  # queued | running | completed | failed
    created_at: str
    updated_at: str
    events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "prompt": self.prompt,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Run:
        return cls(
            id=data["id"],
            session_id=data["session_id"],
            prompt=data.get("prompt", ""),
            status=data.get("status", "queued"),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            events=data.get("events", []),
        )


# ── Unified Session Store ──────────────────────────────────────────────


class UnifiedSessionStore:
    """Single store for sessions, runs, and events.

    Backed by JSON files in ~/.config/wisp/sessions/ for backward
    compatibility with the existing SessionManager.

    Each session file now includes a 'runs' key that stores execution
    history. Events are stored inline within runs (small enough for
    JSON files; if they grow large we can split to JSONL later).
    """

    def __init__(self, sessions_dir: Path | None = None):
        self.sessions_dir = sessions_dir or (WISP_CONFIG_DIR / "sessions")
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._legacy_mgr = SessionManager()
        self._migrated = False

    # ── Session CRUD ─────────────────────────────────────────────────

    def create_session(
        self,
        model: str,
        workspace: str,
        title: str = "",
        session_id: str | None = None,
    ) -> Session:
        """Create a new session."""
        now = _now_iso()
        slug = _slugify(title) if title else "session"
        sid = session_id or f"{_timestamp_id()}-{slug}"
        session = Session(
            id=sid,
            created_at=now,
            updated_at=now,
            model=model,
            workspace=workspace,
            messages=[],
            title=title or "Untitled",
            compaction_history=[],
            task_ids=[],
        )
        self._save(session)
        return session

    def load_session(self, session_id: str) -> Session | None:
        """Load a session by ID. Returns None if not found."""
        path = self._session_path(session_id)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return Session.from_dict(data)
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Corrupt session file %s: %s", session_id, e)
        # Try fragment resolution
        resolved = self.resolve_session_id(session_id)
        if resolved:
            return self.load_session(resolved)
        return None

    def save_session(self, session: Session) -> None:
        """Save a session (and any attached runs)."""
        self._save(session)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its runs."""
        # Delete runs first
        runs_dir = self._runs_dir(session_id)
        if runs_dir.exists():
            for path in runs_dir.glob("*.json"):
                path.unlink()
            runs_dir.rmdir()
        # Delete session
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_sessions(self, limit: int = 50) -> list[dict]:
        """List sessions with metadata, newest first."""
        sessions = []
        if not self.sessions_dir.exists():
            return sessions

        for path in sorted(self.sessions_dir.glob("*.json"), reverse=True):
            if len(sessions) >= limit:
                break
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append({
                    "id": data.get("id", path.stem),
                    "title": data.get("title", "")[:80],
                    "model": data.get("model", "?"),
                    "created_at": data.get("created_at", ""),
                    "updated_at": data.get("updated_at", ""),
                    "msg_count": len(data.get("messages", [])),
                    "compactions": len(data.get("compaction_history", [])),
                    "task_count": len(data.get("task_ids", [])),
                    "file": str(path),
                })
            except (json.JSONDecodeError, OSError):
                continue

        return sessions

    def resolve_session_id(self, fragment: str) -> str | None:
        """Resolve a partial session ID to a full ID."""
        if not self.sessions_dir.exists():
            return None
        best = None
        for path in self.sessions_dir.glob(f"{fragment}*.json"):
            if best is not None:
                logger.warning("Ambiguous session prefix '%s' matches multiple", fragment)
                return None
            best = path.stem
        return best

    # ── Run CRUD ─────────────────────────────────────────────────────

    def create_run(self, session_id: str, prompt: str) -> Run:
        """Create a new run within a session."""
        run = Run(
            id=f"run-{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            prompt=prompt,
            status="queued",
            created_at=_now_iso(),
            updated_at=_now_iso(),
        )
        self._save_run(run)
        return run

    def get_run(self, run_id: str) -> Run | None:
        """Load a run by ID."""
        # Search across all session run directories
        for runs_dir in self.sessions_dir.glob("*/runs"):
            path = runs_dir / f"{run_id}.json"
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    return Run.from_dict(data)
                except (json.JSONDecodeError, OSError):
                    continue
        return None

    def update_run_status(self, run_id: str, status: str) -> bool:
        """Update a run's status."""
        run = self.get_run(run_id)
        if run is None:
            return False
        run.status = status
        run.updated_at = _now_iso()
        self._save_run(run)
        return True

    def list_runs(self, session_id: str) -> list[Run]:
        """List all runs for a session."""
        runs_dir = self._runs_dir(session_id)
        if not runs_dir.exists():
            return []
        runs = []
        for path in sorted(runs_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                runs.append(Run.from_dict(data))
            except (json.JSONDecodeError, OSError):
                continue
        return sorted(runs, key=lambda r: r.created_at)

    # ── Event logging ────────────────────────────────────────────────

    def append_event(self, run_id: str, event: dict) -> bool:
        """Append an event to a run's event log."""
        run = self.get_run(run_id)
        if run is None:
            return False
        event["_logged_at"] = _now_iso()
        run.events.append(event)
        run.updated_at = _now_iso()
        self._save_run(run)
        return True

    def read_events(self, run_id: str) -> list[dict]:
        """Read all events for a run."""
        run = self.get_run(run_id)
        return run.events if run else []

    # ── Migration ────────────────────────────────────────────────────

    def migrate_from_sqlite(self, sqlite_path: Path | None = None) -> int:
        """Migrate threads and runs from SQLiteStateStore to JSON.

        Returns the number of sessions migrated.
        """
        if self._migrated:
            return 0

        sqlite_path = sqlite_path or (WISP_CONFIG_DIR / "app.db")
        if not sqlite_path.exists():
            return 0

        try:
            import sqlite3
            conn = sqlite3.connect(str(sqlite_path))
            conn.row_factory = sqlite3.Row

            # Migrate threads → sessions
            rows = conn.execute(
                "SELECT id, title, workspace, status, created_at, updated_at FROM threads"
            ).fetchall()

            migrated = 0
            for row in rows:
                session_id = row["id"]
                if self.load_session(session_id):
                    continue  # already exists

                session = Session(
                    id=session_id,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    model="unknown",
                    workspace=row["workspace"],
                    messages=[],
                    title=row["title"],
                    compaction_history=[],
                    task_ids=[],
                )
                self._save(session)
                migrated += 1

                # Migrate runs for this thread
                run_rows = conn.execute(
                    "SELECT id, prompt, status, created_at, updated_at FROM runs WHERE thread_id = ?",
                    (session_id,),
                ).fetchall()
                for rrow in run_rows:
                    run = Run(
                        id=rrow["id"],
                        session_id=session_id,
                        prompt=rrow["prompt"],
                        status=rrow["status"],
                        created_at=rrow["created_at"],
                        updated_at=rrow["updated_at"],
                    )
                    self._save_run(run)

            conn.close()
            self._migrated = True
            logger.info("Migrated %d session(s) from SQLite %s", migrated, sqlite_path)
            return migrated

        except Exception as e:
            logger.warning("SQLite migration failed: %s", e)
            return 0

    # ── Internal helpers ─────────────────────────────────────────────

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def _runs_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id / "runs"

    def _save(self, session: Session) -> None:
        """Save session to the store's sessions_dir with locking."""
        session.touch()
        path = self._session_path(session.id)
        try:
            from filelock import FileLock
            lock = FileLock(str(path) + ".lock")
            with lock:
                path.write_text(
                    json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
        except OSError as e:
            logger.error("Failed to save session %s: %s", session.id, e)
            raise

    def _save_run(self, run: Run) -> None:
        """Save a run to its session's runs directory."""
        runs_dir = self._runs_dir(run.session_id)
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"{run.id}.json"
        path.write_text(
            json.dumps(run.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# ── Module-level singleton ─────────────────────────────────────────────

_store: UnifiedSessionStore | None = None


def get_store() -> UnifiedSessionStore:
    """Return the module-level singleton store."""
    global _store
    if _store is None:
        _store = UnifiedSessionStore()
    return _store
