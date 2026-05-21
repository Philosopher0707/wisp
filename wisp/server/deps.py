"""FastAPI dependencies for Wisp server.

Extracted from monolithic server.py:
  - verify_api_key: API key authentication
  - rate_limiter: SQLite-backed rate limiting
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════════

class _AuthConfig:
    """Runtime-mutable auth key."""

    def __init__(self):
        self._key: str = os.environ.get("WISP_API_KEY", "")
        self._no_auth: bool = False
        if self._key:
            logger.info("WISP_API_KEY set — server requires authentication")
        else:
            self._no_auth = True
            logger.info("WISP_API_KEY not set — authentication disabled (dev mode)")

    def set_key(self, key: str) -> None:
        self._key = key
        self._no_auth = not bool(key)

    def disable(self) -> None:
        self._key = ""
        self._no_auth = True
        logger.info("Auth disabled (no-auth mode)")

    @property
    def key(self) -> str:
        return self._key

    @property
    def required(self) -> bool:
        return not self._no_auth and bool(self._key)

    # String back-compat
    def __str__(self) -> str:
        return self._key

    def __eq__(self, other):
        if isinstance(other, str):
            return self._key == other
        if isinstance(other, _AuthConfig):
            return self._key == other._key
        return NotImplemented

    def __bool__(self):
        return bool(self._key)


_auth = _AuthConfig()
API_KEY = _auth
API_KEY_STR = _auth.key


async def verify_api_key(
    x_api_key_header: str | None = Header(None, alias="X-API-Key"),
    authorization: str | None = Header(None),
):
    """API key verification via header only (query param removed — leaks to logs)."""
    if not _auth.required:
        return x_api_key_header or authorization or ""
    if authorization and authorization.lower().startswith("bearer "):
        auth_key = authorization[7:]
        if auth_key == _auth.key:
            return auth_key
    if x_api_key_header and x_api_key_header == _auth.key:
        return x_api_key_header
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ═══════════════════════════════════════════════════════════════════
# Rate Limiting
# ═══════════════════════════════════════════════════════════════════

class SQLiteRateLimiter:
    """Cross-process rate limiter backed by SQLite."""

    def __init__(self, *, db_path: Path, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rate_limits (
                    client_ip TEXT PRIMARY KEY,
                    timestamps TEXT NOT NULL DEFAULT '[]',
                    updated_at REAL NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_rate_updated ON rate_limits(updated_at)"
            )

    def _get_timestamps(self, client_ip: str) -> list[float]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT timestamps FROM rate_limits WHERE client_ip = ?",
                (client_ip,),
            ).fetchone()
        if row is None:
            return []
        try:
            return json.loads(row["timestamps"])
        except (json.JSONDecodeError, TypeError):
            return []

    def _set_timestamps(self, client_ip: str, timestamps: list[float]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO rate_limits (client_ip, timestamps, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(client_ip) DO UPDATE SET
                    timestamps = excluded.timestamps,
                    updated_at = excluded.updated_at
                """,
                (client_ip, json.dumps(timestamps), time.time()),
            )

    def _prune_old(self, timestamps: list[float]) -> list[float]:
        cutoff = time.time() - self.window_seconds
        return [t for t in timestamps if t > cutoff]

    def is_allowed(self, client_ip: str) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT timestamps FROM rate_limits WHERE client_ip = ?",
                    (client_ip,),
                ).fetchone()
                timestamps = []
                if row is not None:
                    try:
                        timestamps = json.loads(row["timestamps"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                timestamps = self._prune_old(timestamps)
                if len(timestamps) >= self.max_requests:
                    conn.execute("ROLLBACK")
                    return False
                timestamps.append(time.time())
                conn.execute(
                    """
                    INSERT INTO rate_limits (client_ip, timestamps, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(client_ip) DO UPDATE SET
                        timestamps = excluded.timestamps,
                        updated_at = excluded.updated_at
                    """,
                    (client_ip, json.dumps(timestamps), time.time()),
                )
                conn.execute("COMMIT")
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise

    async def __call__(self, request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        if not self.is_allowed(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")


# Default rate limiter: 30 requests per 60 seconds
RATE_LIMITER = SQLiteRateLimiter(
    db_path=Path.home() / ".config" / "wisp" / "rate_limits.db",
    max_requests=30,
    window_seconds=60,
)
