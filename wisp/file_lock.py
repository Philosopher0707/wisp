"""Advisory file locking for collaborative editing.

Prevents multiple agents from editing the same file simultaneously.
Locks expire automatically to prevent stale locks from blocking forever.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TIMEOUT = 300  # 5 minutes


class FileLock:
    """Advisory file locking with automatic expiration."""

    def __init__(self, workspace: str, agent_id: Optional[str] = None):
        self.workspace = Path(workspace).resolve()
        self.lock_dir = self.workspace / ".wisp" / "locks"
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.agent_id = agent_id or _generate_agent_id()

    def acquire(self, filepath: str, timeout_sec: int = DEFAULT_LOCK_TIMEOUT) -> bool:
        """Try to acquire a lock on a file.

        Returns True if lock acquired, False if another agent holds it.
        """
        lock_file = self._lock_path(filepath)

        # Check existing lock
        if lock_file.exists():
            try:
                data = json.loads(lock_file.read_text(encoding="utf-8"))
                expires = datetime.fromisoformat(data["expires"])
                if expires > datetime.now(timezone.utc):
                    # Lock still valid
                    if data.get("agent") == self.agent_id:
                        # We already hold it — renew
                        self._write_lock(lock_file, timeout_sec)
                        return True
                    logger.warning(
                        "File %s locked by %s until %s",
                        filepath, data["agent"], data["expires"],
                    )
                    return False
                # Lock expired — steal it
                logger.info("Lock on %s expired, acquiring", filepath)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning("Corrupt lock file for %s: %s", filepath, e)

        self._write_lock(lock_file, timeout_sec)
        logger.debug("Acquired lock on %s", filepath)
        return True

    def release(self, filepath: str) -> None:
        """Release a lock on a file."""
        lock_file = self._lock_path(filepath)
        if lock_file.exists():
            try:
                data = json.loads(lock_file.read_text(encoding="utf-8"))
                if data.get("agent") == self.agent_id:
                    lock_file.unlink()
                    logger.debug("Released lock on %s", filepath)
                else:
                    logger.warning("Cannot release lock on %s — held by %s", filepath, data.get("agent"))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to release lock on %s: %s", filepath, e)

    def is_locked(self, filepath: str) -> bool:
        """Check if a file is currently locked (by any agent)."""
        lock_file = self._lock_path(filepath)
        if not lock_file.exists():
            return False
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            expires = datetime.fromisoformat(data["expires"])
            return expires > datetime.now(timezone.utc)
        except (json.JSONDecodeError, KeyError, ValueError):
            return False

    def lock_info(self, filepath: str) -> Optional[dict]:
        """Return lock metadata if file is locked."""
        lock_file = self._lock_path(filepath)
        if not lock_file.exists():
            return None
        try:
            data = json.loads(lock_file.read_text(encoding="utf-8"))
            expires = datetime.fromisoformat(data["expires"])
            if expires > datetime.now(timezone.utc):
                return data
            return None
        except (json.JSONDecodeError, KeyError, ValueError):
            return None

    def list_active_locks(self) -> list[dict]:
        """List all active locks in the workspace."""
        locks = []
        if not self.lock_dir.exists():
            return locks
        for lock_file in self.lock_dir.glob("*.lock"):
            try:
                data = json.loads(lock_file.read_text(encoding="utf-8"))
                expires = datetime.fromisoformat(data["expires"])
                if expires > datetime.now(timezone.utc):
                    data["_file"] = lock_file.stem.replace(".lock", "")
                    locks.append(data)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return locks

    def release_all(self) -> None:
        """Release all locks held by this agent."""
        if not self.lock_dir.exists():
            return
        for lock_file in self.lock_dir.glob("*.lock"):
            try:
                data = json.loads(lock_file.read_text(encoding="utf-8"))
                if data.get("agent") == self.agent_id:
                    lock_file.unlink()
                    logger.debug("Released lock: %s", lock_file.name)
            except (json.JSONDecodeError, OSError):
                continue

    def _lock_path(self, filepath: str) -> Path:
        """Convert a file path to its lock file path."""
        # Resolve relative to workspace first
        path = Path(filepath)
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        # Use the relative path from workspace to avoid collisions
        try:
            rel = path.relative_to(self.workspace)
        except ValueError:
            # File outside workspace — use absolute path hash
            safe_name = str(path).replace("/", "__").replace("\\", "__")
            return self.lock_dir / f"ext__{safe_name}.lock"
        # Replace path separators with underscores for a flat lock directory
        safe_name = str(rel).replace("/", "__").replace("\\", "__")
        return self.lock_dir / f"{safe_name}.lock"

    def _write_lock(self, lock_file: Path, timeout_sec: int) -> None:
        """Write a new lock file."""
        now = datetime.now(timezone.utc)
        data = {
            "agent": self.agent_id,
            "since": now.isoformat(),
            "expires": (now + timedelta(seconds=timeout_sec)).isoformat(),
        }
        lock_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _generate_agent_id() -> str:
    """Generate a unique agent identifier."""
    return f"wisp-{uuid.uuid4().hex[:8]}"
