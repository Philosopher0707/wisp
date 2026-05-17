"""Async utilities for Wisp — safe sync wrappers for async code.

Provides run_sync() and run_sync_coro() to consume async code from sync
contexts without creating nested event loops.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, AsyncIterator, TypeVar

T = TypeVar("T")

# ── Persistent background thread + loop ──────────────────────────────────

_loop_lock = threading.Lock()
_loop_future: Future | None = None
_loop_thread: threading.Thread | None = None


def _ensure_background_loop() -> asyncio.AbstractEventLoop:
    """Return the global persistent event loop, starting it if needed.

    Thread-safe.  Idempotent.
    """
    global _loop_future, _loop_thread

    if _loop_future is not None:
        # Fast-path: already initialised
        if _loop_future.done():
            return _loop_future.result()
        # Still being created by another thread — block until ready
        # (this returns immediately if done, otherwise blocks)
        return _loop_future.result()

    with _loop_lock:
        # Double-check after acquiring the lock
        if _loop_future is not None and _loop_future.done():
            return _loop_future.result()

        fut = Future()

        def _worker() -> None:
            loop = asyncio.new_event_loop()
            fut.set_result(loop)
            try:
                loop.run_forever()
            finally:
                loop.close()

        t = threading.Thread(target=_worker, daemon=True, name="wisp-async-bg")
        t.start()

        _loop_future = fut
        _loop_thread = t

        return fut.result()


def get_background_thread() -> threading.Thread | None:
    """Return the background worker thread, if it has been started."""
    return _loop_thread


def run_sync_coro(coro) -> Any:
    """Run a coroutine from a sync context, reusing a global background thread.

    Handles both standalone and nested event-loop contexts:
    - If no loop is running: uses asyncio.run() (lightweight — no thread needed)
    - If a loop is running: offloads to the shared persistent background loop
      via asyncio.run_coroutine_threadsafe() (thread-safe, thread-cached)

    Args:
        coro: A coroutine to run.

    Returns:
        The coroutine's return value.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running — safe to use asyncio.run() directly
        return asyncio.run(coro)

    # Reuse the persistent worker instead of spawning a one-off thread
    loop = _ensure_background_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def run_sync(agen: AsyncIterator[T]) -> list[T]:
    """Consume an async generator from a sync context.

    Handles both standalone and nested event-loop contexts:
    - If no loop is running: uses asyncio.run()
    - If a loop is running: offloads to the shared persistent background loop

    Args:
        agen: An async generator to consume.

    Returns:
        A list of all yielded values.

    Example:
        async def my_gen():
            yield 1
            yield 2

        results = run_sync(my_gen())
        assert results == [1, 2]
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running — safe to use asyncio.run()
        return asyncio.run(_consume(agen))

    # Reuse the persistent worker
    loop = _ensure_background_loop()
    future = asyncio.run_coroutine_threadsafe(_consume(agen), loop)
    return future.result()


async def _consume(agen: AsyncIterator[T]) -> list[T]:
    """Consume an async generator into a list."""
    result: list[T] = []
    async for item in agen:
        result.append(item)
    return result
