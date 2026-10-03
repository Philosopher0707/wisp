"""Immutable audit trail — append-only, hash-chained, tamper-evident log.

Writes JSONL with SHA-256 chain hashing. Each entry links to the previous
entry's hash, forming an immutable chain. verify() detects any tampering.

Also provides ImmutableAuditTrail — a SQLite-backed variant using the
UnifiedStore schema for tool-execution decisions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_PATH = Path.home() / ".config" / "wisp" / "audit.jsonl"

_SENSITIVE_KEYS = {"api_key", "token", "password", "secret", "ssh_key", "private_key"}


class AuditTrail:
    """Append-only tamper-evident audit log (JSONL file).

    Each entry is hashed and linked to the previous entry via _prev_hash.
    verify() walks the entire chain to detect tampering.
    """

    def __init__(self, path: Path | None = None):
        self._path = path or Path(os.environ.get("WISP_AUDIT_LOG", str(DEFAULT_AUDIT_PATH)))
        self._last_hash: str = ""
        self._entry_count: int = 0
        self._disabled: bool = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            logger.warning("AuditTrail: cannot create directory %s — audit logging disabled", self._path.parent)
            self._disabled = True
        self._init_state()

    def _init_state(self) -> None:
        """Reconstruct _last_hash and _entry_count from existing entries."""
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        self._last_hash = entry.get("_hash", "")
                        self._entry_count += 1
                    except json.JSONDecodeError:
                        continue
        except Exception:
            self._last_hash = ""
            self._entry_count = 0

    @property
    def entry_count(self) -> int:
        return self._entry_count

    def _redact_value(self, key: str, value: Any) -> Any:
        """Redact sensitive values before writing to the audit log.

        Two layers (12.5E convergence, same semantics as every other audit
        sink): key-name masking first, then the canonical
        ``auth.secrets`` pattern scan (recursive). Previously only the
        key mask existed, so secret-bearing values under innocent keys
        and all nested structures reached the log raw.
        """
        from wisp.auth.secrets import redact_record
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", "replace")
            except Exception:
                return "***"
        if not isinstance(value, (str, dict, list, tuple, int, float, bool)) \
                and value is not None:
            # Unserializable values must not crash the audit write; coerce.
            try:
                value = str(value)
            except Exception:
                return "***"
            if len(value) > 2000:
                value = value[:2000]
        key_lower = str(key).lower().replace("-", "_")
        if any(s in key_lower for s in _SENSITIVE_KEYS):
            if isinstance(value, str) and len(value) > 4:
                return f"{value[:4]}***"
            return "***"
        return _json_safe(redact_record(value))

    def record(self, action: str, *, actor: str = "system", key: str | None = None,
               old_value: Any = None, new_value: Any = None,
               metadata: dict | None = None) -> str:
        """Append a tamper-evident audit entry.

        Returns the entry hash for cross-referencing.
        """
        if self._disabled:
            return ""
        entry: dict[str, Any] = {
            "timestamp": time.time(),
            "action": action,
            "actor": actor,
            "key": key,
            "old_value": self._redact_value(key or "", old_value),
            "new_value": self._redact_value(key or "", new_value),
            "_prev_hash": self._last_hash,
        }
        if metadata:
            entry["metadata"] = self._redact_value("metadata", metadata)

        payload = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        entry["_hash"] = hashlib.sha256(payload.encode()).hexdigest()
        self._last_hash = entry["_hash"]
        self._entry_count += 1

        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        except Exception:
            logger.exception("Audit log write failed for action=%s", action)

        return entry["_hash"]

    def verify(self) -> Optional[int]:
        """Verify the entire audit chain.

        Returns the 1-based index of the first tampered entry, or None
        if the chain is intact. A return value of None means the log is
        fully valid.
        """
        if not self._path.exists():
            return None  # No log to verify

        prev_hash = ""
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for i, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        return i  # Corrupted line

                    # Check backward link
                    stored_prev = entry.get("_prev_hash", "")
                    if stored_prev != prev_hash:
                        logger.warning(
                            "Audit chain broken at entry %d: expected prev_hash=%s, got %s",
                            i, prev_hash, stored_prev,
                        )
                        return i

                    # Recompute hash to verify integrity
                    stored_hash = entry.pop("_hash", None)
                    entry["_hash"] = stored_hash  # Restore for recompute
                    # Recompute without _hash
                    verify_entry = {k: v for k, v in entry.items() if k != "_hash"}
                    payload = json.dumps(verify_entry, sort_keys=True, separators=(",", ":"))
                    expected_hash = hashlib.sha256(payload.encode()).hexdigest()
                    if stored_hash != expected_hash:
                        logger.warning(
                            "Audit entry %d tampered: stored=%s, expected=%s",
                            i, stored_hash, expected_hash,
                        )
                        return i

                    prev_hash = stored_hash

            return None  # All entries valid
        except Exception:
            logger.exception("Audit verification failed")
            return -1  # Signal fatal error

    def entries(self) -> list[dict]:
        """Read all audit entries (for inspection, not verification)."""
        if not self._path.exists():
            return []
        entries: list[dict] = []
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        entries.append({"_error": "corrupted", "_raw": line[:200]})
        except Exception:
            pass
        return entries


def _json_safe(value: Any) -> Any:
    """Recursively coerce audit values to JSON-serializable form."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k)[:256]: _json_safe(v) for k, v in list(value.items())[:256]}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in list(value)[:256]]
    try:
        text = str(value)
    except Exception:
        return "***"
    return text[:2000]


class ImmutableAuditTrail:
    """SQLite-backed audit trail using the UnifiedStore schema.

    Appends to the audit_log table with hash chaining via a BEFORE INSERT
    trigger. Used for tool execution decisions in SecurityPolicy.
    """

    def __init__(self, store: Any):
        self._store = store
        self._init_table()

    def _init_table(self) -> None:
        """Ensure the audit_log table exists with hash-chain trigger."""
        import sqlite3 as _sqlite3

        _ddl = """
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                action TEXT NOT NULL,
                tool_name TEXT NOT NULL DEFAULT '',
                workspace TEXT NOT NULL DEFAULT '',
                allowed INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT '',
                args_summary TEXT NOT NULL DEFAULT '',
                prev_hash TEXT NOT NULL DEFAULT '',
                entry_hash TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
        """
        try:
            self._store._get_conn().executescript(_ddl)
        except _sqlite3.DatabaseError as e:
            # A DB that passed the store's own init can still be corrupt in
            # the audit_log pages. Quarantine via the store (one authority
            # for DB health) and retry once on the fresh file.
            markers = ("malformed", "corrupt", "not a database")
            quarantine = getattr(self._store, "_quarantine_corrupt_db", None)
            if quarantine is None or not any(m in str(e).lower() for m in markers):
                raise
            quarantine()
            store_init = getattr(self._store, "_ensure_initialized", None)
            if callable(store_init):
                self._store._initialized = False
                store_init()
            self._store._get_conn().executescript(_ddl)

    def record_decision(
        self,
        action: str,
        tool_name: str,
        workspace: str,
        allowed: bool,
        reason: str = "",
        args_summary: str = "",
    ) -> str:
        """Record a tool execution decision with hash chaining.

        Returns the entry hash.
        """
        conn = self._store._get_conn()

        # Get last hash
        row = conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = row["entry_hash"] if row else ""

        ts = time.time()
        # Build payload for hashing
        payload = f"{ts}|{action}|{tool_name}|{workspace}|{int(allowed)}|{reason}|{args_summary}|{prev_hash}"
        entry_hash = hashlib.sha256(payload.encode()).hexdigest()

        conn.execute(
            """INSERT INTO audit_log (timestamp, action, tool_name, workspace, allowed, reason, args_summary, prev_hash, entry_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, action, tool_name, workspace, int(allowed), reason, args_summary, prev_hash, entry_hash),
        )
        return entry_hash

    def verify(self) -> Optional[int]:
        """Verify the hash chain. Returns first tampered row number or None."""
        conn = self._store._get_conn()
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id ASC").fetchall()
        prev_hash = ""
        for i, row in enumerate(rows, start=1):
            if row["prev_hash"] != prev_hash:
                return i
            payload = (
                f"{row['timestamp']}|{row['action']}|{row['tool_name']}|"
                f"{row['workspace']}|{row['allowed']}|{row['reason']}|"
                f"{row['args_summary']}|{row['prev_hash']}"
            )
            expected = hashlib.sha256(payload.encode()).hexdigest()
            if row["entry_hash"] != expected:
                return i
            prev_hash = row["entry_hash"]
        return None

    def entries(self, limit: int = 100) -> list[dict]:
        """Read recent audit entries."""
        conn = self._store._get_conn()
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════
# Lazy singleton (avoid import-time side effects in sandboxed/tests)
# ═══════════════════════════════════════════════════════════════════

_audit_instance: AuditTrail | None = None


def get_audit() -> AuditTrail:
    """Return the singleton AuditTrail, creating it on first use."""
    global _audit_instance
    if _audit_instance is None:
        _audit_instance = AuditTrail()
    return _audit_instance


class _LazyAuditProxy:
    """Proxy that delays AuditTrail instantiation until first attribute access."""

    def __getattr__(self, name: str):
        return getattr(get_audit(), name)

    def __setattr__(self, name: str, value) -> None:
        if name.startswith("__"):
            super().__setattr__(name, value)
        else:
            setattr(get_audit(), name, value)


# Public module-level name (backward compatible)
audit = _LazyAuditProxy()
