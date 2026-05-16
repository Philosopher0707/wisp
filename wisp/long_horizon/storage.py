"""Persistent storage for long-horizon task checkpoints.

Provides atomic writes, an index registry, and CRUD operations
for TaskState objects.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from wisp.config import WISP_CONFIG_DIR
from wisp.long_horizon.state import TaskState

logger = logging.getLogger(__name__)

TASKS_DIR = WISP_CONFIG_DIR / "tasks"
INDEX_PATH = TASKS_DIR / "index.json"


# ── Public API ───────────────────────────────────────────────────────

class TaskStorage:
    """CRUD + atomic checkpointing for TaskState objects.

    Usage:
        storage = TaskStorage()
        storage.save(state)           # Atomic write
        loaded = storage.load(tid)    # Deserialize
        tasks = storage.list_all()    # List all tasks
        storage.delete(tid)           # Remove checkpoint
    """

    def __init__(self, tasks_dir: Path | None = None) -> None:
        self.tasks_dir = tasks_dir or TASKS_DIR
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.tasks_dir / "index.json"

    # ── Core CRUD ───────────────────────────────────────────────────

    def save(self, state: TaskState) -> Path:
        """Atomically write a TaskState checkpoint to disk.

        Uses write-to-temp + rename for crash safety.
        Also updates the index registry.
        """
        path = self._checkpoint_path(state.task_id)
        data = state.to_json(indent=2)
        _atomic_write(path, data)
        state.last_checkpoint = _now_iso()
        self._update_index(state)
        logger.debug("Checkpoint saved: %s", path)
        return path

    def load(self, task_id: str) -> TaskState | None:
        """Load a TaskState from disk. Returns None if not found."""
        path = self._checkpoint_path(task_id)
        if not path.exists():
            logger.warning("Checkpoint not found: %s", path)
            return None
        try:
            text = path.read_text(encoding="utf-8")
            return TaskState.from_json(text)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error("Failed to load checkpoint %s: %s", task_id, e)
            return None

    def delete(self, task_id: str) -> bool:
        """Remove a checkpoint and update the index. Returns True if existed."""
        path = self._checkpoint_path(task_id)
        existed = path.exists()
        if existed:
            path.unlink()
            logger.info("Deleted checkpoint: %s", path)
        self._remove_from_index(task_id)
        return existed

    def exists(self, task_id: str) -> bool:
        """Check if a checkpoint exists on disk."""
        return self._checkpoint_path(task_id).exists()

    # ── Listing ─────────────────────────────────────────────────────

    def list_all(self) -> list[dict[str, Any]]:
        """Return lightweight metadata for all tasks from the index."""
        index = self._read_index()
        return index.get("tasks", [])

    def list_by_status(self, status: str) -> list[dict[str, Any]]:
        """Filter tasks by status (pending, running, paused, completed, failed)."""
        return [t for t in self.list_all() if t.get("status") == status]

    def list_running(self) -> list[dict[str, Any]]:
        """Convenience: list only running tasks."""
        return self.list_by_status("running")

    # ── Index registry ──────────────────────────────────────────────

    def _read_index(self) -> dict[str, Any]:
        """Read the index registry, returning empty if missing/corrupt."""
        if not self.index_path.exists():
            return {"tasks": []}
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Index corrupt, rebuilding from checkpoints")
            return self._rebuild_index()

    def _update_index(self, state: TaskState) -> None:
        """Add or update a task entry in the index."""
        index = self._read_index()
        tasks = index.get("tasks", [])

        # Remove old entry for this task_id
        tasks = [t for t in tasks if t.get("task_id") != state.task_id]

        # Add new entry
        tasks.append({
            "task_id": state.task_id,
            "goal": state.goal[:200],  # Truncate for index
            "status": state.status.value,
            "current_step": state.current_step_index,
            "total_steps": state.total_steps,
            "updated_at": state.updated_at,
        })

        index["tasks"] = tasks
        _atomic_write(self.index_path, json.dumps(index, indent=2))

    def _remove_from_index(self, task_id: str) -> None:
        """Remove a task entry from the index."""
        index = self._read_index()
        tasks = [t for t in index.get("tasks", []) if t.get("task_id") != task_id]
        index["tasks"] = tasks
        _atomic_write(self.index_path, json.dumps(index, indent=2))

    def _rebuild_index(self) -> dict[str, Any]:
        """Rebuild index by scanning checkpoint files."""
        tasks: list[dict[str, Any]] = []
        for path in self.tasks_dir.glob("task-*.json"):
            try:
                state = TaskState.from_json(path.read_text(encoding="utf-8"))
                tasks.append({
                    "task_id": state.task_id,
                    "goal": state.goal[:200],
                    "status": state.status.value,
                    "current_step": state.current_step_index,
                    "total_steps": state.total_steps,
                    "updated_at": state.updated_at,
                })
            except Exception as e:
                logger.warning("Skipping corrupt checkpoint %s: %s", path, e)
        return {"tasks": tasks}

    # ── Helpers ─────────────────────────────────────────────────────

    def _checkpoint_path(self, task_id: str) -> Path:
        return self.tasks_dir / f"{task_id}.json"


# ── Module-level helpers ─────────────────────────────────────────────

def _atomic_write(path: Path, data: str) -> None:
    """Write data atomically using temp file + rename.

    On POSIX, os.replace() is atomic. On Windows, it may not be
    truly atomic but is the best-effort approach.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        # Clean up temp on failure
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    """Current UTC time as ISO-8601 string."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
