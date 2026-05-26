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

from wisp.infra.audit import audit

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Auth
# ═══════════════════════════════════════════════════════════════════

_AUTH_KEY_GRACE_SECONDS = 86_400  # 24h default key rotation grace period


class _AuthConfig:
    """Runtime-mutable auth key with rotation support.

    Supports up to one previous key during rotation with a grace period.
    Keys are persisted to disk (~/.config/wisp/auth_keys.json) so
    rotation grace windows survive server restarts.
    """

    _persist_path: Path = Path.home() / ".config" / "wisp" / "auth_keys.json"

    def __init__(self):
        self._key: str = os.environ.get("WISP_API_KEY", "")
        self._no_auth: bool = False
        self._valid_keys: dict[str, float | None] = {}

        # Load persisted keys first; env overrides
        self._load_from_disk()

        if self._key:
            self._valid_keys.setdefault(self._key, None)
            logger.info("WISP_API_KEY set — server requires authentication")
        else:
            self._no_auth = True
            logger.info("WISP_API_KEY not set — authentication disabled (dev mode)")
        self._save_to_disk()

    # ── Persistence ─────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        if not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self._valid_keys = {k: v for k, v in data.items()}
            # Filter expired keys
            now = time.time()
            expired = [
                k for k, expiry in self._valid_keys.items()
                if expiry is not None and now > expiry
            ]
            for k in expired:
                del self._valid_keys[k]
            if expired:
                logger.info("Removed %d expired key(s) on load", len(expired))
        except Exception:
            logger.warning("Failed to load persisted keys, starting fresh")

    def _save_to_disk(self) -> None:
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._persist_path.write_text(
                json.dumps(self._valid_keys, indent=2), encoding="utf-8"
            )
            self._persist_path.chmod(0o600)  # Owner read/write only
        except Exception:
            logger.warning("Failed to persist auth keys to disk")

    # ── Mutation ──────────────────────────────────────────────────

    def set_key(self, key: str) -> None:
        """Replace the current key immediately (no rotation grace)."""
        self._key = key
        self._no_auth = not bool(key)
        self._valid_keys = {key: None} if key else {}
        self._save_to_disk()
        audit.record("key_set", key="api_key")

    def rotate_key(self, new_key: str, grace_seconds: int = _AUTH_KEY_GRACE_SECONDS) -> None:
        """Rotate to a new key while keeping the current one valid for a grace period."""
        old_key = self._key
        now = time.time()
        # Update new key as primary (no expiry)
        self._key = new_key
        self._no_auth = not bool(new_key)
        # Set expiry for old key if it exists and differs
        if old_key and old_key != new_key:
            self._valid_keys[old_key] = now + grace_seconds
            logger.info("Key rotation: old key expires in %d seconds", grace_seconds)
        # Add new key with no expiry, or clear if empty
        if new_key:
            self._valid_keys[new_key] = None
        if not new_key:
            self._valid_keys.clear()
        self._save_to_disk()
        audit.record("key_rotation", key="api_key",
                      new_value=new_key[:4] + "..." if new_key else None)

    def disable(self) -> None:
        self._key = ""
        self._no_auth = True
        self._valid_keys.clear()
        logger.info("Auth disabled (no-auth mode)")
        audit.record("auth_disabled", key="api_key")
        self._save_to_disk()

    def _is_valid(self, candidate: str) -> bool:
        """Check if a candidate key is valid (current or within grace period)."""
        if self._no_auth:
            return True
        now = time.time()
        # Clean expired keys lazily
        expired = [
            k for k, expiry in self._valid_keys.items()
            if expiry is not None and now > expiry
        ]
        for k in expired:
            del self._valid_keys[k]
            logger.info("Rotated key expired and removed")
        if expired:
            self._save_to_disk()
        return candidate in self._valid_keys or candidate == self._key

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
        if _auth._is_valid(auth_key):
            return auth_key
    if x_api_key_header and _auth._is_valid(x_api_key_header):
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
        self._disabled: bool = False
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            logger.warning("SQLiteRateLimiter: cannot create directory %s — rate limiting disabled", self.db_path.parent)
            self._disabled = True
            return
        try:
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
        except (sqlite3.OperationalError, PermissionError, OSError):
            logger.warning("SQLiteRateLimiter: cannot initialize DB — rate limiting disabled")
            self._disabled = True

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
        if getattr(self, "_disabled", False):
            return True
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


# ═══════════════════════════════════════════════════════════════════
# Lazy singletons (avoid import-time side effects in sandboxed/tests)
# ═══════════════════════════════════════════════════════════════════

_auth_instance: _AuthConfig | None = None
_rate_limiter_instance: SQLiteRateLimiter | None = None


def get_auth() -> _AuthConfig:
    """Return the singleton _AuthConfig, creating it on first use."""
    global _auth_instance
    if _auth_instance is None:
        _auth_instance = _AuthConfig()
    return _auth_instance


def get_rate_limiter() -> SQLiteRateLimiter:
    """Return the singleton SQLiteRateLimiter, creating it on first use."""
    global _rate_limiter_instance
    if _rate_limiter_instance is None:
        _rate_limiter_instance = SQLiteRateLimiter(
            db_path=Path.home() / ".config" / "wisp" / "rate_limits.db",
            max_requests=30,
            window_seconds=60,
        )
    return _rate_limiter_instance


class _LazyAuthProxy:
    """Proxy that delays _AuthConfig instantiation until first attribute access."""

    def __getattr__(self, name: str):
        return getattr(get_auth(), name)

    def __setattr__(self, name: str, value) -> None:
        if name.startswith("__"):
            super().__setattr__(name, value)
        else:
            setattr(get_auth(), name, value)

    def __delattr__(self, name: str) -> None:
        if name.startswith("__"):
            super().__delattr__(name)
        else:
            delattr(get_auth(), name)


class _LazyRateLimiterProxy:
    """Proxy that delays SQLiteRateLimiter instantiation until first call."""

    async def __call__(self, request: Request) -> None:
        return await get_rate_limiter()(request)


# Public module-level names (backward compatible)
_auth = _LazyAuthProxy()
RATE_LIMITER = _LazyRateLimiterProxy()

# Lazy exports for API_KEY compatibility
API_KEY = _auth


class _APIKeyStr:
    def __str__(self):
        return get_auth().key

    def __repr__(self):
        return repr(get_auth().key)


API_KEY_STR = _APIKeyStr()
