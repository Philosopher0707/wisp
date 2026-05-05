"""Workspace file locking — prevents multiple agents from editing the same file simultaneously.

Uses the AgentRegistry as the source of truth for claims, with filesystem
advisory locks as a secondary guard.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from .registry import AgentRegistry

logger = logging.getLogger(__name__)


class WorkspaceLock:
    """Coordinates file access across agents in a swarm.

    Two layers:
    1. AgentRegistry claim_file/release_file — fast, in-memory
    2. Filesystem .lock files — survives process crashes
    """

    def __init__(self, workspace: str, registry: AgentRegistry):
        self.workspace = Path(workspace).resolve()
        self.registry = registry
        self._local_locks: dict[str, str] = {}  # path -> agent_id
        self._mutex = threading.RLock()

    def _lock_path(self, target: str) -> Path:
        """Return the filesystem lock file path for a target file."""
        target_path = Path(target)
        # If relative, resolve against workspace; if absolute, use as-is
        if not target_path.is_absolute():
            target_path = (self.workspace / target_path).resolve()
        else:
            target_path = target_path.resolve()
        # Ensure the target is inside the workspace
        try:
            target_path.relative_to(self.workspace)
        except ValueError:
            raise ValueError(f"Path {target_path} is outside workspace {self.workspace}")
        return target_path.with_suffix(target_path.suffix + ".wisp_lock")

    def acquire(self, agent_id: str, path: str, timeout: float = 0.0) -> bool:
        """Try to acquire a lock on a file.

        Args:
            agent_id: The agent requesting the lock.
            path: File path to lock (relative or absolute).
            timeout: Seconds to wait (0 = non-blocking).

        Returns:
            True if the lock was acquired.
        """
        # Layer 1: Registry claim
        if not self.registry.claim_file(agent_id, path):
            logger.warning("Agent %s failed to claim %s (already locked)", agent_id, path)
            return False

        # Layer 2: Filesystem lock
        lock_file = self._lock_path(path)
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Use exclusive create as atomic test-and-set
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w") as f:
                f.write(agent_id)
        except FileExistsError:
            # Rollback registry claim
            self.registry.release_file(agent_id, path)
            logger.warning("Agent %s failed to acquire filesystem lock on %s", agent_id, path)
            return False

        with self._mutex:
            self._local_locks[path] = agent_id

        logger.debug("Agent %s acquired lock on %s", agent_id, path)
        return True

    def release(self, agent_id: str, path: str) -> None:
        """Release a lock held by an agent."""
        lock_file = self._lock_path(path)
        try:
            if lock_file.exists():
                with open(lock_file, "r") as f:
                    owner = f.read().strip()
                if owner == agent_id:
                    lock_file.unlink()
                else:
                    logger.warning("Agent %s tried to release lock on %s owned by %s", agent_id, path, owner)
        except OSError as e:
            logger.warning("Failed to remove lock file %s: %s", lock_file, e)

        self.registry.release_file(agent_id, path)
        with self._mutex:
            self._local_locks.pop(path, None)

        logger.debug("Agent %s released lock on %s", agent_id, path)

    def release_all(self, agent_id: str) -> None:
        """Release all locks held by an agent."""
        with self._mutex:
            paths = [p for p, aid in self._local_locks.items() if aid == agent_id]
        for path in paths:
            self.release(agent_id, path)

    def is_locked(self, path: str) -> bool:
        """Check if a file is currently locked by any agent."""
        lock_file = self._lock_path(path)
        return lock_file.exists()

    def owner(self, path: str) -> Optional[str]:
        """Return the agent ID that holds the lock, or None."""
        lock_file = self._lock_path(path)
        if not lock_file.exists():
            return None
        try:
            with open(lock_file, "r") as f:
                return f.read().strip()
        except OSError:
            return None

    def cleanup_stale(self) -> int:
        """Remove lock files for agents that are no longer active."""
        removed = 0
        active_ids = {a.agent_id for a in self.registry.list_agents() if a.status.name not in ("STOPPED", "CRASHED")}

        for lock_file in self.workspace.rglob("*.wisp_lock"):
            try:
                with open(lock_file, "r") as f:
                    owner = f.read().strip()
                if owner not in active_ids:
                    lock_file.unlink()
                    removed += 1
                    self.registry.release_file(owner, str(lock_file.with_suffix("").relative_to(self.workspace)))
            except OSError:
                pass

        return removed
