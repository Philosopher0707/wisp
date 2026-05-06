"""Background task runner — long-lived subprocess execution.

Lets the agent start dev servers, watch-mode tests, or any long-running
command without blocking the main agent loop. Tasks run in subprocesses
with daemon reader threads that buffer output into ring buffers.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_OUTPUT_LINES = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BackgroundTask:
    """A single background command, with its subprocess and output buffer."""

    id: str
    command: str
    workspace: str
    start_time: str
    status: str = "running"
    output_lines: deque = field(default_factory=lambda: deque(maxlen=_MAX_OUTPUT_LINES))
    exit_code: int = -1
    _process: subprocess.Popen | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "workspace": self.workspace,
            "start_time": self.start_time,
            "status": self.status,
            "exit_code": self.exit_code,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BackgroundTask":
        return cls(
            id=data["id"],
            command=data["command"],
            workspace=data["workspace"],
            start_time=data["start_time"],
            status=data.get("status", "unknown"),
            exit_code=data.get("exit_code", -1),
        )


class BackgroundTaskManager:
    """Manage background subprocesses — start, watch, kill, list, cleanup."""

    def __init__(self):
        self._tasks: dict[str, BackgroundTask] = {}

    def start(self, command: str, workspace: str) -> str:
        """Start a background command, return task_id immediately."""
        task_id = f"bg-{uuid.uuid4().hex[:8]}"
        cwd = Path(workspace).resolve()
        task = BackgroundTask(
            id=task_id,
            command=command,
            workspace=str(cwd),
            start_time=_now_iso(),
        )
        task._process = subprocess.Popen(
            command,
            shell=True,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        task._thread = threading.Thread(
            target=self._read_output, args=(task,), daemon=True
        )
        task._thread.start()
        self._tasks[task_id] = task
        logger.info("Started background task %s: %.100s", task_id, command)
        return task_id

    def _read_output(self, task: BackgroundTask):
        """Daemon thread: read stdout lines into ring buffer until EOF."""
        try:
            for line in task._process.stdout:
                task.output_lines.append(line.rstrip("\n"))
            task._process.wait()
            task.exit_code = task._process.returncode
            task.status = f"exited ({task.exit_code})"
        except Exception as e:
            task.status = f"error: {e}"
            logger.warning("Background task %s read error: %s", task.id, e)

    def watch(self, task_id: str) -> str:
        """Return latest buffered output for a task."""
        task = self._tasks.get(task_id)
        if not task:
            return f"Task {task_id} not found."
        lines = list(task.output_lines)
        if not lines:
            return f"[{task.status}] (no output yet)"
        return f"[{task.status}]\n" + "\n".join(lines)

    def kill(self, task_id: str) -> str:
        """Kill a running task. Returns status message."""
        task = self._tasks.get(task_id)
        if not task:
            return f"Task {task_id} not found."
        if task.status != "running":
            return f"Task {task_id} already {task.status}"
        try:
            task._process.kill()
            task._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            task._process.terminate()
        except Exception as e:
            return f"Error killing {task_id}: {e}"
        task.status = "killed"
        return f"Killed task {task_id}"

    def list_tasks(self) -> str:
        """Return a summary of all known tasks."""
        if not self._tasks:
            return "No background tasks."
        lines = ["Background tasks:"]
        for t in self._tasks.values():
            lines.append(f"  {t.id} [{t.status}] {t.command[:80]}")
        return "\n".join(lines)

    def cleanup_all(self):
        """Kill all running tasks. Called on session exit."""
        for task_id in list(self._tasks.keys()):
            task = self._tasks[task_id]
            if task.status == "running":
                try:
                    task._process.kill()
                except Exception:
                    pass
        self._tasks.clear()
        logger.info("Cleaned up all background tasks")
