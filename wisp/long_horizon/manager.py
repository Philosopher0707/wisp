"""Task manager for long-horizon execution.

Provides a high-level interface for starting, monitoring, and controlling
long-horizon tasks. Integrates with the agent core for actual execution.

Usage:
    manager = TaskManager(agent=agent)
    task_id = await manager.start("Migrate Flask to FastAPI")
    status = manager.status(task_id)
    await manager.pause(task_id)
    await manager.resume(task_id)
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from wisp.core.agent import WispAgentCore
from wisp.core.events import AgentEvent
from wisp.long_horizon.runner import LongHorizonRunner
from wisp.long_horizon.state import TaskState, TaskStatus
from wisp.long_horizon.storage import TaskStorage

logger = logging.getLogger(__name__)


class TaskManager:
    """Manage long-horizon task lifecycle.

    Attributes:
        agent: WispAgentCore instance for step execution.
        storage: TaskStorage for checkpoint persistence.
        max_parallel: Max parallel steps (passed to runner).
    """

    def __init__(
        self,
        agent: WispAgentCore,
        storage: TaskStorage | None = None,
        max_parallel: int = 4,
    ):
        self.agent = agent
        self.storage = storage or TaskStorage()
        self.max_parallel = max_parallel
        self._running: dict[str, asyncio.Task] = {}

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self, goal: str, workspace: str = ".") -> str:
        """Start a new long-horizon task.

        Creates initial plan, saves checkpoint, and begins background execution.
        Returns the task_id.
        """
        runner = LongHorizonRunner(
            agent=self.agent,
            storage=self.storage,
        )
        state = await runner._create_initial_state(goal, workspace)
        self.storage.save(state)

        # Start background execution
        bg_task = asyncio.create_task(
            self._execute(runner, state, workspace),
            name=f"long-task-{state.task_id}",
        )
        self._running[state.task_id] = bg_task
        logger.info("Started long-horizon task: %s", state.task_id)
        return state.task_id

    async def resume(self, task_id: str, workspace: str = ".") -> str:
        """Resume a paused or crashed task.

        Returns the task_id if successful, raises if not found.
        """
        state = self.storage.load(task_id)
        if state is None:
            raise ValueError(f"Task not found: {task_id}")

        runner = LongHorizonRunner(
            agent=self.agent,
            storage=self.storage,
        )
        bg_task = asyncio.create_task(
            self._execute(runner, state, workspace),
            name=f"long-task-{task_id}",
        )
        self._running[task_id] = bg_task
        logger.info("Resumed long-horizon task: %s", task_id)
        return task_id

    async def pause(self, task_id: str) -> bool:
        """Pause a running task.

        Cancels the background task and updates checkpoint.
        Returns True if the task was running.
        """
        bg_task = self._running.pop(task_id, None)
        if bg_task and not bg_task.done():
            bg_task.cancel()
            try:
                await bg_task
            except asyncio.CancelledError:
                pass

        state = self.storage.load(task_id)
        if state is None:
            return False
        state.set_status(TaskStatus.PAUSED)
        self.storage.save(state)
        logger.info("Paused long-horizon task: %s", task_id)
        return True

    async def cancel(self, task_id: str) -> bool:
        """Cancel a running or paused task.

        Returns True if the task was found and cancelled.
        """
        bg_task = self._running.pop(task_id, None)
        if bg_task and not bg_task.done():
            bg_task.cancel()
            try:
                await bg_task
            except asyncio.CancelledError:
                pass

        state = self.storage.load(task_id)
        if state is None:
            return False
        state.set_status(TaskStatus.FAILED)
        self.storage.save(state)
        logger.info("Cancelled long-horizon task: %s", task_id)
        return True

    # ── Queries ───────────────────────────────────────────────────────

    def status(self, task_id: str) -> dict:
        """Get the current status of a task.

        Returns a dict with task metadata and progress.
        """
        state = self.storage.load(task_id)
        if state is None:
            return {"error": f"Task not found: {task_id}"}

        current = state.current_step
        return {
            "task_id": state.task_id,
            "goal": state.goal,
            "status": state.status.value,
            "current_step_index": state.current_step_index,
            "total_steps": state.total_steps,
            "completed_steps": state.completed_count,
            "failed_steps": state.failed_count,
            "plan_version": state.plan_version,
            "current_step": current.description if current else None,
            "progress_pct": round(state.progress_pct, 1),
            "last_checkpoint": state.last_checkpoint,
            "is_running": task_id in self._running,
        }

    def list_tasks(self, status_filter: str = "all") -> list[dict]:
        """List all tasks, optionally filtered by status.

        Returns lightweight metadata for each task.
        """
        if status_filter == "all":
            tasks = self.storage.list_all()
        else:
            tasks = self.storage.list_by_status(status_filter)
        return tasks

    def is_running(self, task_id: str) -> bool:
        """Check if a task has an active background execution."""
        task = self._running.get(task_id)
        return task is not None and not task.done()

    # ── Background execution ──────────────────────────────────────────

    async def _execute(
        self,
        runner: LongHorizonRunner,
        state: TaskState,
        workspace: str,
    ) -> None:
        """Run the task loop and clean up when done."""
        try:
            async for _event in runner._run_loop(state, workspace):
                pass  # Events are logged by the runner
        except asyncio.CancelledError:
            logger.info("Task %s cancelled", state.task_id)
            raise
        finally:
            self._running.pop(state.task_id, None)
