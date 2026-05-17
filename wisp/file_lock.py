"""Advisory file locking for collaborative editing.

Uses OS-level advisory file locking (fcntl on Unix, Win32 on Windows)
via the filelock library.  A process-level ``threading.Lock`` guards
acquire/release within the same interpreter to prevent TOCTOU races
among threads / subagents.

JSON metadata files (``*.lock``) are kept alongside for introspection
(who holds the lock, expiry), but the actual mutual exclusion is
enforced by the OS, not by Path.exists() checks.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from filelock import FileLock as _OSFileLock

logger = logging.getLogger(__name__)

DEFAULT_LOCK_TIMEOUT = 300  # 5 minutes

# Module-level cache of OS-level FileLock instances per path.
# Critical: within a single process, all agents / FileLock instances
# must share the same underlying OS FileLock object so that acquire /
# release / is_locked operate on the same file descriptor.
_flock_cache: dict[Path, _OSFileLock] = {}
_flock_cache_lock = threading.Lock()

# One process-level mutex per metadata-lock-file so that multiple
# threads / FileLock instances racing on the same target file don't
# interleave reads/writes.
_process_locks: dict[Path, threading.Lock] = {}
_process_locks_lock = threading.Lock()


def _get_process_lock(lock_path: Path) -> threading.Lock:
    with _process_locks_lock:
        if lock_path not in _process_locks:
            _process_locks[lock_path] = threading.Lock()
        return _process_locks[lock_path]


def _get_os_lock(lock_path: Path) -> _OSFileLock:
    """Return the cached (or new) OS FileLock for *lock_path*."""
    flock_path = lock_path.with_suffix(".flock")
    with _flock_cache_lock:
        if flock_path not in _flock_cache:
            _flock_cache[flock_path] = _OSFileLock(str(flock_path))
        return _flock_cache[flock_path]


class FileLock:
    """Advisory file locking with automatic expiration.

    Internally uses ``filelock.FileLock`` for OS-level mutual exclusion
    and a ``threading.Lock`` for intra-process ordering.
    JSON metadata files (``*.lock``) are still written for visibility.
    """

    def __init__(self, workspace: str, agent_id: Optional[str] = None):
        self.workspace = Path(workspace).resolve()
        self.lock_dir = self.workspace / ".wisp" / "locks"
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.agent_id = agent_id or _generate_agent_id()

    # ── Public API ─────────────────────────────────────────────────────

    def acquire(self, filepath: str, timeout_sec: int = DEFAULT_LOCK_TIMEOUT) -> bool:
        """Try to acquire a lock on *filepath*.

        Returns ``True`` if the lock was acquired (or already held by this
        agent and renewed), ``False`` if another active agent holds it.
        """
        lock_path = self._lock_path(filepath)
        px_lock = _get_process_lock(lock_path)
        os_lock = _get_os_lock(lock_path)

        # ── Step 1: intra-process ordering (eliminates TOCTOU) ──
        px_lock.acquire()
        try:
            data = _read_meta(lock_path)

            # If WE own it (same agent), just renew metadata.
            if data and data.get("agent") == self.agent_id:
                _write_meta(lock_path, self.agent_id, timeout_sec)
                return True

            # Try OS-level lock (non-blocking).
            try:
                os_lock.acquire(timeout=0)
                lock_held = True
            except Exception:
                lock_held = False

            if not lock_held:
                # Another PROCESS holds the advisory lock — can't steal.
                if data and data.get("expires"):
                    try:
                        expires = datetime.fromisoformat(data["expires"])
                        if expires > datetime.now(timezone.utc):
                            logger.warning(
                                "File %s locked by %s until %s",
                                filepath, data.get("agent"), data["expires"],
                            )
                            return False
                        logger.info("Lock on %s expired, acquiring", filepath)
                    except (ValueError, TypeError):
                        pass
                logger.warning("File %s locked (another process)", filepath)
                return False

            # ── Step 2: we hold the OS lock, but on macOS/POSIX another
            # FileLock instance in the SAME process may also succeed.
            # Re-read metadata (under px_lock) to check ownership. ──
            data = _read_meta(lock_path)
            if data and data.get("agent") != self.agent_id and data.get("expires"):
                try:
                    expires = datetime.fromisoformat(data["expires"])
                    if expires > datetime.now(timezone.utc):
                        # Another agent in this process still owns it.
                        os_lock.release()
                        logger.warning(
                            "File %s locked by %s until %s",
                            filepath, data["agent"], data["expires"],
                        )
                        return False
                except (ValueError, TypeError):
                    pass

            _write_meta(lock_path, self.agent_id, timeout_sec)
            logger.debug("Acquired lock on %s", filepath)
            return True
        finally:
            px_lock.release()

    def release(self, filepath: str) -> None:
        """Release a lock on *filepath* held by this agent."""
        lock_path = self._lock_path(filepath)
        px_lock = _get_process_lock(lock_path)
        os_lock = _get_os_lock(lock_path)

        px_lock.acquire()
        try:
            # Remove metadata first while still holding the OS lock.
            data = _read_meta(lock_path)
            if data and data.get("agent") == self.agent_id:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                logger.debug("Released lock on %s", filepath)
            elif data:
                logger.warning(
                    "Cannot release lock on %s — held by %s", filepath, data.get("agent")
                )
                # Do NOT release the OS-level lock — we don't own it.
                return
            if os_lock.is_locked:
                try:
                    os_lock.release()
                except Exception:
                    pass
        finally:
            px_lock.release()

    def is_locked(self, filepath: str) -> bool:
        """Check if *filepath* is currently locked by any agent."""
        lock_path = self._lock_path(filepath)
        px_lock = _get_process_lock(lock_path)
        os_lock = _get_os_lock(lock_path)

        px_lock.acquire()
        try:
            # If metadata says another agent holds it, return True immediately.
            data = _read_meta(lock_path)
            if data and data.get("agent") != self.agent_id and data.get("expires"):
                try:
                    expires = datetime.fromisoformat(data["expires"])
                    if expires > datetime.now(timezone.utc):
                        return True
                except (ValueError, TypeError):
                    return True

            # Check OS advisory lock definitively.
            if os_lock.is_locked:
                # We (or someone in this process) currently hold it.
                if data:
                    try:
                        expires = datetime.fromisoformat(data["expires"])
                        return expires > datetime.now(timezone.utc)
                    except (ValueError, TypeError):
                        return True
                return True

            # Try to grab the OS lock non-blocking.
            try:
                os_lock.acquire(timeout=0)
            except Exception:
                # Another process holds it — definitively locked.
                return True

            # Nobody holds it — we grabbed it but don't need it.
            try:
                os_lock.release()
            except Exception:
                pass
            return False
        finally:
            px_lock.release()

    def lock_info(self, filepath: str) -> Optional[dict]:
        """Return lock metadata if *filepath* is locked."""
        if not self.is_locked(filepath):
            return None
        return _read_meta(self._lock_path(filepath))

    def list_active_locks(self) -> list[dict]:
        """List all active locks in the workspace."""
        locks: list[dict] = []
        if not self.lock_dir.exists():
            return locks
        for lock_file in self.lock_dir.glob("*.lock"):
            data = _read_meta(lock_file)
            if data and data.get("expires"):
                try:
                    expires = datetime.fromisoformat(data["expires"])
                    if expires > datetime.now(timezone.utc):
                        data["_file"] = lock_file.stem.replace(".lock", "")
                        locks.append(data)
                except (ValueError, TypeError):
                    continue
        return locks

    def release_all(self) -> None:
        """Release all locks held by this agent."""
        if not self.lock_dir.exists():
            return
        for lock_file in self.lock_dir.glob("*.lock"):
            data = _read_meta(lock_file)
            if data and data.get("agent") == self.agent_id:
                flock_path = lock_file.with_suffix(".flock")
                os_lock = _flock_cache.get(flock_path)
                px_lock = _get_process_lock(lock_file)
                px_lock.acquire()
                try:
                    try:
                        lock_file.unlink()
                    except OSError:
                        pass
                    if os_lock and os_lock.is_locked:
                        try:
                            os_lock.release()
                        except Exception:
                            pass
                    logger.debug("Released lock: %s", lock_file.name)
                finally:
                    px_lock.release()

    # ── Internals ──────────────────────────────────────────────────────

    def _lock_path(self, filepath: str) -> Path:
        """Map a target file path to its JSON metadata lock file."""
        path = Path(filepath)
        if not path.is_absolute():
            path = self.workspace / path
        path = path.resolve()
        try:
            rel = path.relative_to(self.workspace)
        except ValueError:
            safe_name = str(path).replace("/", "__").replace("\\", "__")
            return self.lock_dir / f"ext__{safe_name}.lock"
        safe_name = str(rel).replace("/", "__").replace("\\", "__")
        return self.lock_dir / f"{safe_name}.lock"


# ── Standalone helpers (don't depend on ``self.agent_id``) ───────────


def _write_meta(lock_path: Path, agent_id: str, timeout_sec: int) -> None:
    """Write JSON lock metadata (must be called while holding the process lock)."""
    now = datetime.now(timezone.utc)
    data = {
        "agent": agent_id,
        "since": now.isoformat(),
        "expires": (now + timedelta(seconds=timeout_sec)).isoformat(),
    }
    lock_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _read_meta(lock_path: Path) -> Optional[dict]:
    """Read JSON lock metadata if present."""
    if not lock_path.exists():
        return None
    try:
        return json.loads(lock_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, KeyError):
        return None


def _generate_agent_id() -> str:
    """Generate a unique agent identifier."""
    return f"wisp-{uuid.uuid4().hex[:8]}"
