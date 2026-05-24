"""Background agent execution — spawn and track long-running agent tasks.

Refactored for multi-process safety:
  - BackgroundRun state is persisted to SQLite (cross-process reads)
  - asyncio.Tasks stay in-process (cannot cross process boundaries)
  - get()/list_runs() read from SQLite to find runs started by other workers
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from wisp.config import WispConfig
from wisp.core.events import (
    AgentEvent, TYPE_CONTENT, TYPE_THINKING, TYPE_TOOL_CALL,
    TYPE_TOOL_RESULT, TYPE_ERROR, TYPE_DONE,
)
from wisp.transport.headless import HeadlessTransport

logger = logging.getLogger(__name__)


@dataclass
class BackgroundRun:
    id: str
    prompt: str
    model: str
    workspace: str
    status: str  # pending | running | done | failed | cancelled
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    error: Optional[str] = None
    iterations: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt[:200],
            "model": self.model,
            "workspace": self.workspace,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "content": self.content[:2000],
            "tool_calls": self.tool_calls[-10:],
            "files_changed": self.files_changed,
            "error": self.error,
            "iterations": self.iterations,
            "duration_ms": round(((self.finished_at or time.time()) - (self.started_at or self.created_at)) * 1000) if self.started_at else 0,
        }

    @classmethod
    def from_db_row(cls, row: dict) -> "BackgroundRun":
        """Reconstruct from a dict (SQLite result converted to dict)."""
        return cls(
            id=row["run_id"],
            prompt=row.get("prompt", ""),
            model=row.get("model", ""),
            workspace=row.get("workspace", ""),
            status=row.get("status", "pending"),
            created_at=row.get("created_at", time.time()),
            started_at=row.get("started_at"),
            finished_at=row.get("finished_at"),
            content=row.get("content", ""),
            tool_calls=row.get("tool_calls", []),
            files_changed=row.get("files_changed", []),
            iterations=row.get("iterations", 0) or 0,
            error=row.get("error"),
        )


class BackgroundRunner:
    """Manages background agent execution with cross-process state tracking."""

    def __init__(self, workspace: str = "."):
        from wisp.infra.store import UnifiedStore
        db_path = Path(workspace) / ".wisp" / "wisp.db"
        self._store = UnifiedStore(db_path)
        self._tasks: dict[str, asyncio.Task] = {}   # process-local only
        self._callbacks: dict[str, asyncio.Task] = {} # process-local only

    def create(self, prompt: str, model: str, workspace: str,
               permission_mode: str = "auto_edit") -> BackgroundRun:
        """Create a new background run. Returns the run ID."""
        run_id = f"bg-{uuid.uuid4().hex[:12]}"
        run = BackgroundRun(
            id=run_id,
            prompt=prompt,
            model=model,
            workspace=workspace,
            status="pending",
        )
        self._store.bg_create(run.to_dict())
        return run

    def start(self, run_id: str):
        """Begin execution of a pending run on THIS worker."""
        row = self._store.bg_get(run_id)
        if not row:
            raise ValueError(f"Unknown run: {run_id}")
        if row.get("status") != "pending":
            raise ValueError(f"Run {run_id} already started (status: {row['status']})")

        self._store.bg_update(run_id, status="running", started_at=time.time())
        task = asyncio.create_task(self._execute(run_id))
        self._tasks[run_id] = task

    async def _execute(self, run_id: str):
        """Execute the agent in background using HeadlessTransport."""
        row = self._store.bg_get(run_id)
        if not row:
            logger.error("Run %s disappeared from store", run_id)
            return

        run = BackgroundRun.from_db_row(row)

        try:
            from wisp.entry import run_headless

            result = await run_headless(
                prompt=run.prompt,
                model=run.model,
                workspace=run.workspace,
                permission_mode="auto_edit",
            )

            run.content = result.get("content", "")
            run.tool_calls = result.get("tool_calls", [])
            run.iterations = result.get("iterations", 0)
            if not result.get("ok", False):
                run.error = result["errors"][0]["message"] if result.get("errors") else "Unknown error"

            # Collect changed files from tool calls
            files_changed: list[str] = []
            for tc in run.tool_calls:
                if tc.get("name") in ("write_file", "edit_file"):
                    args = tc.get("args", {})
                    if isinstance(args, dict) and "path" in args:
                        files_changed.append(args["path"])
            run.files_changed = files_changed

            run.status = "done" if result.get("ok", False) else "failed"

        except Exception as e:
            logger.error("Background run %s failed: %s", run_id, e)
            run.error = str(e)
            run.status = "failed"

        finally:
            run.finished_at = time.time()
            self._store.bg_update(
                run_id,
                status=run.status,
                content=run.content,
                error=run.error,
                iterations=run.iterations,
                finished_at=run.finished_at,
                tool_calls=run.tool_calls,
                files_changed=run.files_changed,
            )

    def get(self, run_id: str) -> Optional[BackgroundRun]:
        """Get a run by ID (reads from SQLite — works across processes)."""
        row = self._store.bg_get(run_id)
        if not row:
            return None
        return BackgroundRun.from_db_row(row)

    def list_runs(self) -> list[BackgroundRun]:
        """List all runs (reads from SQLite — works across processes)."""
        rows = self._store.bg_list()
        return [BackgroundRun.from_db_row(r) for r in rows]

    # Alias for API compatibility
    list = list_runs

    def delete(self, run_id: str) -> bool:
        """Delete a run from the store."""
        self._store.bg_delete(run_id)
        return True

    def cancel(self, run_id: str) -> bool:
        """Cancel a running background task (must be called on the worker
        that owns the asyncio Task). Returns False if run is on a
different worker (caller should poll status instead)."""
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            self._store.bg_update(
                run_id,
                status="cancelled",
                error="Cancelled by user",
            )
            return True
        return False


# Module-level singleton (per-process, but store is SQLite-backed)
_runner: Optional[BackgroundRunner] = None


def get_runner(workspace: str = ".") -> BackgroundRunner:
    global _runner
    if _runner is None:
        _runner = BackgroundRunner(workspace)
    return _runner
