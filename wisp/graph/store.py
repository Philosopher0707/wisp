"""Durable graph state — extends the UnifiedStore DB, no parallel system.

Tables (``graph_*``) live in the SAME SQLite file resolved like
wisp.infra.store.UnifiedStore (WISP_DB / <ws>/.wisp/wisp.db / fallback),
created with CREATE TABLE IF NOT EXISTS so existing installs migrate
without touching current tables. Persisted per meaningful transition:
graph_run, node_run, edge decision, verification, retry, budget, approval,
checkpoint ref, event.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_runs (
  run_id TEXT PRIMARY KEY,
  graph_id TEXT NOT NULL,
  graph_version TEXT NOT NULL DEFAULT '1',
  graph_hash TEXT NOT NULL DEFAULT '',
  graph_def TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued',
  inputs TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS graph_node_runs (
  node_run_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt INTEGER NOT NULL DEFAULT 0,
  input_hash TEXT NOT NULL DEFAULT '',
  result TEXT NOT NULL DEFAULT '{}',
  idempotency_key TEXT NOT NULL DEFAULT '',
  started_at REAL NOT NULL DEFAULT 0,
  finished_at REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_graph_node_runs_run ON graph_node_runs(run_id);
CREATE TABLE IF NOT EXISTS graph_artifacts (
  artifact_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  node_run_id TEXT NOT NULL DEFAULT '',
  type TEXT NOT NULL DEFAULT 'blob',
  schema_version TEXT NOT NULL DEFAULT '1',
  content_hash TEXT NOT NULL DEFAULT '',
  uri TEXT NOT NULL DEFAULT '',
  producer TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_graph_artifacts_run ON graph_artifacts(run_id);
CREATE TABLE IF NOT EXISTS graph_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  type TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_graph_events_run ON graph_events(run_id);
CREATE TABLE IF NOT EXISTS graph_checkpoints (
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  state TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (run_id, seq)
);
"""


def _db_path(workspace: str = "") -> str:
    override = os.environ.get("WISP_DB", "")
    if override:
        return override
    if workspace:
        return os.path.join(os.path.abspath(workspace), ".wisp", "wisp.db")
    home = os.path.expanduser("~/.config/wisp/wisp.db")
    return home


def new_run_id() -> str:
    return f"graph-{uuid.uuid4().hex[:12]}"


class GraphStore:
    """Thread-safe SQLite store for graph runs. Mirrors UnifiedStore idioms."""

    def __init__(self, workspace: str = "", path: str = "") -> None:
        self._path = path or _db_path(workspace)
        self._local = threading.local()
        self._lock = threading.RLock()
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    # ── runs ──
    def create_run(self, run_id: str, graph_id: str, version: str,
                   graph_hash: str, graph_def: dict, inputs: dict,
                   workspace: str = "") -> None:
        now = time.time()
        graph_def = {**graph_def, "workspace": os.path.abspath(workspace or ".")}
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO graph_runs(run_id,graph_id,graph_version,graph_hash,"
                "graph_def,status,inputs,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, graph_id, version, graph_hash, json.dumps(graph_def),
                 "queued", json.dumps(inputs), now, now))

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM graph_runs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def set_run_status(self, run_id: str, status: str, error: str = "") -> None:
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE graph_runs SET status=?, error=?, updated_at=? WHERE run_id=?",
                         (status, error, time.time(), run_id))

    def list_runs(self, graph_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            limit = _clamp_limit(limit)
            if graph_id:
                rows = conn.execute("SELECT * FROM graph_runs WHERE graph_id=? "
                                    "ORDER BY created_at DESC LIMIT ?", (graph_id, limit)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM graph_runs ORDER BY created_at DESC LIMIT ?",
                                    (limit,)).fetchall()
            return [dict(r) for r in rows]

    # ── node runs (upsert by idempotency key) ──
    def put_node_run(self, node_run_id: str, run_id: str, node_id: str, status: str,
                     attempt: int, input_hash: str, result: dict,
                     idempotency_key: str = "", started: float = 0.0,
                     finished: float = 0.0) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO graph_node_runs(node_run_id,run_id,node_id,status,attempt,"
                "input_hash,result,idempotency_key,started_at,finished_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(node_run_id) DO UPDATE SET status=excluded.status,"
                " attempt=excluded.attempt, result=excluded.result, finished_at=excluded.finished_at",
                (node_run_id, run_id, node_id, status, attempt, input_hash,
                 json.dumps(result), idempotency_key, started, finished))

    def node_runs(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM graph_node_runs WHERE run_id=? ORDER BY started_at",
                                (run_id,)).fetchall()
            return [dict(r) for r in rows]

    def find_completed(self, idempotency_key: str) -> dict[str, Any] | None:
        """Idempotent resume: same key + terminal success -> reuse, never re-run."""
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM graph_node_runs WHERE idempotency_key=? "
                               "AND status='success' LIMIT 1", (idempotency_key,)).fetchone()
            return dict(row) if row else None

    # ── artifacts ──
    def put_artifact(self, artifact_id: str, run_id: str, node_run_id: str, type: str,
                     content_hash: str, uri: str, producer: str, metadata: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO graph_artifacts(artifact_id,run_id,node_run_id,type,"
                "schema_version,content_hash,uri,producer,created_at,metadata)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, run_id, node_run_id, type, "1", content_hash, uri,
                 producer, time.time(), json.dumps(metadata)))

    def artifacts(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM graph_artifacts WHERE run_id=? ORDER BY created_at",
                                (run_id,)).fetchall()
            return [dict(r) for r in rows]

    def list_artifacts(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM graph_artifacts ORDER BY created_at DESC LIMIT ?",
                                (_clamp_limit(limit),)).fetchall()
            return [dict(r) for r in rows]

    # ── events / checkpoints ──
    def append_event(self, run_id: str, type: str, data: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("INSERT INTO graph_events(run_id,type,data,created_at) VALUES(?,?,?,?)",
                         (run_id, type, json.dumps(data), time.time()))

    def events(self, run_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT * FROM graph_events WHERE run_id=? ORDER BY id LIMIT ?",
                                (run_id, _clamp_limit(limit))).fetchall()
            return [dict(r) for r in rows]

    def put_checkpoint(self, run_id: str, seq: int, state: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO graph_checkpoints(run_id,seq,state,created_at)"
                         " VALUES(?,?,?,?)", (run_id, seq, json.dumps(state), time.time()))

    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM graph_checkpoints WHERE run_id=? "
                               "ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
            return dict(row) if row else None


def _clamp_limit(limit: Any) -> int:
    """Negative SQLite LIMIT means unbounded — never allow that."""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return 500
    return max(1, min(n, 5000))
