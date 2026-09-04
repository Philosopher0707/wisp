"""Pool tests — semaphore bound, timeouts, cancellation cascade."""

from __future__ import annotations

import asyncio

from wisp.core.subagent.protocol import ExecutionPolicy, TaskFrame, TaskStatus


def _frame(i: int, **kw) -> TaskFrame:
    base = {"task_id": f"t{i}", "task": f"do {i}", "role": "explorer"}
    base.update(kw)
    return TaskFrame(**base)


def test_semaphore_enforced_under_fanout():
    from wisp.core.subagent.pool import BoundedWorkerPool

    in_flight = 0
    peak = 0

    async def _worker(frame: TaskFrame, emit) -> dict:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return {"task_id": frame.task_id, "status": "SUCCESS",
                "findings": [], "token_usage": {"prompt": 1, "completion": 1}}

    async def _go():
        pool = BoundedWorkerPool(worker_fn=_worker,
                                 policy=ExecutionPolicy(max_concurrent=4, timeout_s=60.0))
        return await pool.run([_frame(i) for i in range(12)])

    results = asyncio.run(_go())
    assert peak <= 4
    assert peak > 1  # genuinely concurrent, not serialized
    assert all(r.status is TaskStatus.SUCCESS for r in results)
    assert [r.task_id for r in results] == [f"t{i}" for i in range(12)]


def test_wall_clock_timeout_reclaims_slot():
    from wisp.core.subagent.pool import BoundedWorkerPool

    async def _slow(frame: TaskFrame, emit) -> dict:
        await asyncio.sleep(30)
        return {"task_id": frame.task_id, "status": "SUCCESS",
                "findings": [], "token_usage": {"prompt": 1, "completion": 1}}

    async def _go():
        import time
        t0 = time.monotonic()
        pool = BoundedWorkerPool(worker_fn=_slow,
                                 policy=ExecutionPolicy(max_concurrent=2, timeout_s=0.05))
        results = await pool.run([_frame(0)])
        return results, time.monotonic() - t0

    results, elapsed = asyncio.run(_go())
    assert results[0].status is TaskStatus.TIMEOUT
    assert elapsed < 5.0


def test_parent_cancel_cascades_to_workers():
    from wisp.core.subagent.pool import BoundedWorkerPool

    reached: list[str] = []

    async def _worker(frame: TaskFrame, emit) -> dict:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            reached.append(frame.task_id)
            raise
        return {"task_id": frame.task_id, "status": "SUCCESS",
                "findings": [], "token_usage": {"prompt": 1, "completion": 1}}

    async def _go():
        pool = BoundedWorkerPool(worker_fn=_worker,
                                 policy=ExecutionPolicy(max_concurrent=4, timeout_s=60.0))
        run_task = asyncio.ensure_future(pool.run([_frame(i) for i in range(4)]))
        await asyncio.sleep(0.05)
        run_task.cancel()
        try:
            await run_task
        except asyncio.CancelledError:
            pass
        # Let cancellations deliver inside the still-running loop.
        await asyncio.sleep(0.05)
        return pool

    pool = asyncio.run(_go())
    assert sorted(reached) == ["t0", "t1", "t2", "t3"]
    assert pool.cancelled_count == 4


def test_crash_becomes_failed_result_not_exception():
    from wisp.core.subagent.pool import BoundedWorkerPool

    async def _boom(frame: TaskFrame, emit) -> dict:
        raise RuntimeError("worker exploded")

    async def _go():
        pool = BoundedWorkerPool(worker_fn=_boom,
                                 policy=ExecutionPolicy(max_concurrent=2, timeout_s=60.0))
        return await pool.run([_frame(0), _frame(1)])

    results = asyncio.run(_go())
    assert all(r.status is TaskStatus.FAILED for r in results)
    assert "exploded" in results[0].error


def test_telemetry_started_settled_events():
    from wisp.core.subagent.pool import BoundedWorkerPool

    seen: list[tuple[str, str]] = []

    async def _sink(event) -> None:
        seen.append((event.worker_id, event.event))

    async def _worker(frame: TaskFrame, emit) -> dict:
        await emit("tool", "read_file a.py")
        return {"task_id": frame.task_id, "status": "SUCCESS",
                "findings": [], "token_usage": {"prompt": 1, "completion": 1}}

    async def _go():
        pool = BoundedWorkerPool(worker_fn=_worker,
                                 policy=ExecutionPolicy(max_concurrent=2, timeout_s=60.0),
                                 telemetry=_sink)
        return await pool.run([_frame(0)])

    results = asyncio.run(_go())
    assert results[0].status is TaskStatus.SUCCESS
    kinds = [e for _, e in seen]
    assert "started" in kinds and "settled" in kinds and "tool" in kinds
