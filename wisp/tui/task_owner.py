"""Owned background tasks for TUI screens.

Bare asyncio.create_task() calls leak in three ways: exceptions vanish
silently (an approval-forward that dies means the server times out and
DENIES while the user believes they approved), nothing cancels them on
teardown, and GC-time "exception was never retrieved" noise. This owner
gives every spawn a name, exception logging, and a single cancel point.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class OwnedTasks:
    """Track, log, and cancel a screen's background tasks."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro, *, name: str) -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._reap)
        return task

    def _reap(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "TUI task %s failed", task.get_name(), exc_info=exc,
            )

    def cancel_all(self) -> int:
        """Cancel everything still running; returns the count."""
        live = [t for t in self._tasks if not t.done()]
        for task in live:
            task.cancel()
        return len(live)

    def __len__(self) -> int:
        return len([t for t in self._tasks if not t.done()])
