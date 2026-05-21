"""Audit trail for security-relevant config and state changes.

Writes append-only, hash-chained log entries to a JSONL file.
Each entry includes: timestamp, action, actor, old_value (redacted), new_value (redacted), and the previous entry's hash for tamper evidence.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default audit log path — overridden by WISP_AUDIT_LOG env var
DEFAULT_AUDIT_PATH = Path.home() / ".config" / "wisp" / "audit.jsonl"

# Fields whose values are redacted in the audit log
_SENSITIVE_KEYS = {"api_key", "token", "password", "secret", "ssh_key", "private_key"}


class AuditTrail:
    """Append-only tamper-evident audit log."""

    def __init__(self, path: Path | None = None):
        self._path = path or Path(os.environ.get("WISP_AUDIT_LOG", str(DEFAULT_AUDIT_PATH)))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash: str = ""
        self._init_last_hash()

    def _init_last_hash(self) -> None:
        """Initialise _last_hash from the final existing entry."""
        if not self._path.exists():
            self._last_hash = ""
            return
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                lines = [line.strip() for line in fh if line.strip()]
                if lines:
                    last_entry = json.loads(lines[-1])
                    self._last_hash = last_entry.get("_hash", "")
        except Exception:
            self._last_hash = ""

    def _redact_value(self, key: str, value: Any) -> Any:
        """Redact sensitive values before writing to the audit log."""
        key_lower = str(key).lower().replace("-", "_")
        if any(s in key_lower for s in _SENSITIVE_KEYS):
            if isinstance(value, str) and len(value) > 4:
                return f"{value[:4]}***"
            return "***"
        if isinstance(value, (dict, list)):
            return value  # Non-primitive values are not recursively redacted for simplicity
        return value

    def record(self, action: str, *, actor: str = "system", key: str | None = None,
                old_value: Any = None, new_value: Any = None) -> None:
        """Append a tamper-evident audit entry.

        Args:
            action: Short code, e.g. 'config_change', 'key_rotation'.
            actor: Who triggered the action (e.g. 'user:admin', 'system').
            key: The config key or field that changed.
            old_value: Previous value (redacted automatically).
            new_value: New value (redacted automatically).
        """
        entry = {
            "timestamp": time.time(),
            "action": action,
            "actor": actor,
            "key": key,
            "old_value": self._redact_value(key or "", old_value),
            "new_value": self._redact_value(key or "", new_value),
            "_prev_hash": self._last_hash,
        }
        # Hash the entry for tamper evidence
        payload = json.dumps(entry, sort_keys=True, separators=(",", ":"))
        entry["_hash"] = hashlib.sha256(payload.encode()).hexdigest()
        self._last_hash = entry["_hash"]

        try:
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, separators=(",", ":")) + "\n")
        except Exception:
            logger.exception("Audit log write failed for action=%s", action)


# Global singleton — imported by config, deps, and orchestrator
audit = AuditTrail()
