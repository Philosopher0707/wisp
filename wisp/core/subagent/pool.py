"""Bounded worker pool with structured cancellation (TaskGroup).

Guarantees:
  - At most ``max_concurrent`` workers run user code at once
    (``asyncio.Semaphore``; overflow queues gracefully in arrival order).
  - Each worker is bounded by ``timeout_s`` (``asyncio.wait_for``); expiry
    yields a TIMEOUT result and the semaphore slot is always reclaimed
    (``try/finally`` — no leak on crash, timeout, or cancel).
  - Cancellation cascades: aborting the awaiting parent cancels every live
    child (structured concurrency — no orphaned network/subprocess work).
    Cancellation is never swallowed or converted (cancellation-first).
  - Every lifecycle moment emits a :class:`WorkerEvent` to the sink
    (started/tool/settled); a missing sink is a no-op, never an error.

The pool validates raw worker dicts into :class:`SubagentResult`.
Unparseable output becomes FAILED (validation retry policy belongs to
the coordinator, which owns parent-state decisions).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from pydantic import ValidationError

from wisp.core.subagent.protocol import ExecutionPolicy, SubagentResult, TaskFrame, WorkerEvent

logger = logging.getLogger(__name__)

# WorkerFn: (frame, emit) -> raw result dict. emit(kind, detail) lets the
# worker report tool/progress moments that the pool forwards as events.
EmitFn = Callable[[str, str], Awaitable[None]]
WorkerFn = Callable[[TaskFrame, EmitFn], Awaitable[dict[str, Any]]]


class BoundedWorkerPool:
    """Semaphore-bounded fanout executor with cascade cancellation."""

    def __init__(
        self,
        worker_fn: WorkerFn,
        policy: ExecutionPolicy | None = None,
        telemetry: Callable[[WorkerEvent], Awaitable[None]] | None = None,
    ) -> None:
        self._worker = worker_fn
        self._policy = policy or ExecutionPolicy()
        self._telemetry = telemetry
        self._semaphore = asyncio.Semaphore(self._policy.max_concurrent)
        self.cancelled_count = 0
        self.timed_out_count = 0

    @property
    def max_concurrent(self) -> int:
        return self._policy.max_concurrent

    @property
    def timeout_s(self) -> float:
        return self._policy.timeout_s

    async def _emit(self, event: WorkerEvent) -> None:
        if self._telemetry is None:
            return
        try:
            await self._telemetry(event)
        except Exception:
            logger.debug("telemetry sink failed", exc_info=True)

    async def _run_one(self, frame: TaskFrame, role: str) -> SubagentResult:
        started = time.monotonic()
        worker_id = f"subagent:{frame.role}-{frame.task_id}"
        await self._emit(WorkerEvent(worker_id=worker_id, role=frame.role, event="started"))

        async def _emit_kind(kind: str, detail: str) -> None:
            await self._emit(WorkerEvent(
                worker_id=worker_id, role=frame.role, event="tool" if kind == "tool" else "progress",
                detail=detail, elapsed_s=time.monotonic() - started,
            ))

        await self._semaphore.acquire()
        try:
            try:
                raw = await asyncio.wait_for(
                    self._worker(frame, _emit_kind), timeout=self._policy.timeout_s
                )
            except asyncio.TimeoutError:
                elapsed = time.monotonic() - started
                self.timed_out_count += 1
                result = SubagentResult.timeout(frame.task_id, elapsed)
                await self._emit(WorkerEvent(
                    worker_id=worker_id, role=frame.role, event="settled",
                    detail="TIMEOUT", elapsed_s=elapsed,
                ))
                return result
            elapsed = time.monotonic() - started
            try:
                result = SubagentResult.model_validate(raw)
            except ValidationError as exc:
                result = SubagentResult.failure(
                    frame.task_id, f"output failed SubagentResult validation: {exc.errors()}", elapsed)
            if not result.task_id:
                result = SubagentResult(
                    task_id=frame.task_id, status=result.status, findings=result.findings,
                    patches=result.patches, token_usage=result.token_usage,
                    error=result.error, elapsed_s=elapsed,
                )
            await self._emit(WorkerEvent(
                worker_id=worker_id, role=frame.role, event="settled",
                detail=result.status.value, elapsed_s=elapsed, tokens=result.token_usage.total,
            ))
            return result
        except (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit):
            # Cascade path: parent abort. Count, emit, re-raise — the
            # TaskGroup (or awaiting parent) owns the final propagation.
            self.cancelled_count += 1
            try:
                await self._emit(WorkerEvent(
                    worker_id=worker_id, role=frame.role, event="settled",
                    detail="CANCELLED", elapsed_s=time.monotonic() - started,
                ))
            except (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit):
                pass
            raise
        except Exception as exc:
            elapsed = time.monotonic() - started
            logger.exception("worker %s crashed", worker_id)
            result = SubagentResult.failure(frame.task_id, f"worker crashed: {exc}", elapsed)
            await self._emit(WorkerEvent(
                worker_id=worker_id, role=frame.role, event="settled",
                detail="FAILED", elapsed_s=elapsed,
            ))
            return result
        finally:
            self._semaphore.release()

    async def run(self, frames: list[TaskFrame]) -> list[SubagentResult]:
        """Fan out over frames with structured concurrency, order-preserving.

        Raises CancelledError (after cascading to live children) when the
        awaiting parent is aborted. Never returns partial + exception mixes:
        either every frame has a result, or cancellation propagates.
        """
        if not frames:
            return []
        ordered: dict[str, SubagentResult] = {}
        # Semaphore must be re-created if the loop changed (pools can
        # outlive a test's event loop). Semaphores bind to no loop until
        # awaited in 3.10+, but a closed loop still poisons waiters.
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and getattr(self._semaphore, "_loop", None) not in (None, running):
            self._semaphore = asyncio.Semaphore(self._policy.max_concurrent)

        async def _collect(frame: TaskFrame) -> None:
            ordered[frame.task_id] = await self._run_one(frame, frame.role)

        try:
            async with asyncio.TaskGroup() as tg:
                for frame in frames:
                    tg.create_task(_collect(frame))
        except BaseException:
            # TaskGroup already cancelled+joined every child (cascade
            # complete). Propagate untouched — including CancelledError.
            raise
        return [ordered[frame.task_id] for frame in frames]
