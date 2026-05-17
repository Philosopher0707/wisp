"""Shared in-memory server state refactored for multi-process deployments.

Replaces:
  _swarm_store:  dict[str, dict]  → SwarmStateStore (SQLite-backed)
  _swarm_lock:   asyncio.Lock     → SQLite WAL (no explicit lock needed)

The SwarmStateStore is a drop-in replacement for the dict-based store.
It looks like a dict but persists to SQLite for cross-process safety.

Orchestrator objects (non-serializable) are tracked separately via
a lightweight snapshot mechanism: the worker that starts a swarm
periodically writes the orchestrator's registry back to the store.
Other workers can read this snapshot.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SwarmStateStore(dict):
    """SQLite-backed persistent swarm run store.

    Looks like a dict to existing code:
        store["run-id"] = {...}
        entry = store.get("run-id")
        del store["run-id"]

    But persists under the hood to SQLite. The orchestrator object is NOT
    stored in SQLite (it's not serializable). Instead, a "live_orch" key
    is set in a process-local dict and the orchestrator's registry is
    periodically snapshotted to SQLite for cross-process reads.
    """

    # Global counter for how many writes have been done (for optimistic locking)
    _write_counter: int = 0

    def __init__(self, workspace: str):
        """Initialize with a workspace path (used to locate the SQLite DB)."""
        self._db_path = Path(workspace).resolve() / ".wisp" / "server_state.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    # ── Table init (runs once per worker process) ──

    def _init_tables(self) -> None:
        with sqlite3.connect(str(self._db_path), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS swarm_runs (
                    run_id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL DEFAULT '',
                    roles TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'running',
                    start_time REAL NOT NULL,
                    end_time REAL,
                    finished INTEGER NOT NULL DEFAULT 0,
                    events TEXT NOT NULL DEFAULT '[]',
                    agents_snapshot TEXT,
                    updated_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS background_runs (
                    run_id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    workspace TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    content TEXT NOT NULL DEFAULT '',
                    error TEXT,
                    iterations INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Dict-like API ──────────────────────────────────────────────────

    def __getitem__(self, run_id: str) -> dict[str, Any]:
        entry = self.get(run_id)
        if entry is None:
            raise KeyError(run_id)
        return entry

    def __setitem__(self, run_id: str, value: dict[str, Any]) -> None:
        self._upsert(run_id, value)

    def __contains__(self, run_id: str) -> bool:
        return self.get(run_id) is not None

    def __delitem__(self, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM swarm_runs WHERE run_id = ?", (run_id,))

    def get(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM swarm_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def pop(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM swarm_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row:
                conn.execute("DELETE FROM swarm_runs WHERE run_id = ?", (run_id,))
                return self._row_to_dict(row)
        return None

    def items(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM swarm_runs").fetchall()
        return [(r["run_id"], self._row_to_dict(r)) for r in rows]

    def keys(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT run_id FROM swarm_runs").fetchall()
        return [r[0] for r in rows]

    def values(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM swarm_runs").fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Swarm-specific helpers ───────────────────────────────────────────

    def _upsert(self, run_id: str, value: dict[str, Any]) -> None:
        """Insert or replace a swarm run record."""
        events = json.dumps(value.get("event_log", []))
        agents_snapshot = json.dumps(value.get("agents_snapshot", {})) if value.get("agents_snapshot") else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO swarm_runs (run_id, goal, roles, status, start_time, end_time,
                                        finished, events, agents_snapshot, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    goal = excluded.goal,
                    roles = excluded.roles,
                    status = excluded.status,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    finished = excluded.finished,
                    events = excluded.events,
                    agents_snapshot = excluded.agents_snapshot,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    str(value.get("goal", "")),
                    json.dumps(value.get("roles", [])),
                    "done" if value.get("finished") else "running",
                    value.get("start_time", time.monotonic()),
                    value.get("end_time", None),
                    1 if value.get("finished") else 0,
                    events,
                    agents_snapshot,
                    time.monotonic(),
                ),
            )

    def _evict_stale(self, ttl_seconds: int = 600) -> list[str]:
        """Remove finished runs older than TTL. Returns list of evicted run_ids."""
        cutoff = time.monotonic() - ttl_seconds
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM swarm_runs WHERE finished = 1 AND end_time < ? RETURNING run_id",
                (cutoff,)
            )
            return [r[0] for r in cur.fetchall()]

    # ── Background run helpers (dict-like but simpler) ──────────────────

    def _bg_create(self, run: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO background_runs
                (run_id, prompt, model, workspace, status, content, error, iterations,
                 created_at, started_at, finished_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["id"],
                    run.get("prompt", ""),
                    run.get("model", ""),
                    run.get("workspace", ""),
                    run.get("status", "pending"),
                    run.get("content", ""),
                    run.get("error", None),
                    run.get("iterations", 0),
                    run.get("created_at", time.time()),
                    run.get("started_at", None),
                    run.get("finished_at", None),
                    time.time(),
                ),
            )

    def _bg_get(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM background_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return dict(row)

    def _bg_update(self, run_id: str, **kwargs) -> None:
        if not kwargs:
            return
        columns = []
        params = []
        for k, v in kwargs.items():
            if k == "error" and not v:
                continue
            columns.append(f"{k} = ?")
            params.append(v)
        columns.append("updated_at = ?")
        params.append(time.time())
        params.append(run_id)
        sql = f"UPDATE background_runs SET {', '.join(columns)} WHERE run_id = ?"
        with self._connect() as conn:
            conn.execute(sql, params)

    def _bg_list(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM background_runs ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Internal helpers ───────────────────────────────────────────────

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        """Convert a sqlite Row to the dict format expected by existing code."""
        entry: dict[str, Any] = {
            "goal": row["goal"],
            "roles": json.loads(row["roles"]),
            "start_time": row["start_time"],
            "finished": bool(row["finished"]),
            "event_log": json.loads(row["events"]),
            "status": row["status"],
        }
        if row["end_time"] is not None:
            entry["end_time"] = row["end_time"]
        if row["agents_snapshot"] is not None:
            entry["agents_snapshot"] = json.loads(row["agents_snapshot"])
        # live_orch is injected at runtime by the worker process, not in DB
        return entry

    # ── Snapshot helpers ───────────────────────────────────────────────

    def snapshot_agents(self, run_id: str, registry_data: dict[str, Any]) -> None:
        """Write a snapshot of the orchestrator's registry to DB."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE swarm_runs SET agents_snapshot = ?, updated_at = ? WHERE run_id = ?",
                (json.dumps(registry_data), time.monotonic(), run_id),
            )

