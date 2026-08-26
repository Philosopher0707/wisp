"""Async utilities for Wisp — safe sync wrappers for async code.

Provides run_sync() and run_sync_coro() to consume async code from sync
contexts without creating nested event loops.

NEW: ``sync_gen_iter()`` bridges synchronous generators so they can be consumed
without blocking the asyncio event loop.  This is the minimal correct fix
for the _arun blocking bug: the synchronous requests.post(...) chain runs in
a thread and yields events via an asyncio.Queue.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Iterator
from typing import Any, AsyncIterator, TypeVar

T = TypeVar("T")

# Shared thread pool — single pool for all sync-offload work.
# Consolidates the old _GEN_EXECUTOR + default asyncio executor so thread
# count stays bounded under concurrent turns.
_SHARED_EXECUTOR: ThreadPoolExecutor | None = None
_SHARED_EXECUTOR_SIZE: int = 8


def get_shared_executor() -> ThreadPoolExecutor:
    """Return the shared thread pool, creating it lazily if needed.

    This pool is used by ``sync_gen_iter()`` and registered as the default
    asyncio executor so ``asyncio.to_thread()`` also uses it.
    """
    global _SHARED_EXECUTOR
    if _SHARED_EXECUTOR is None:
        _SHARED_EXECUTOR = ThreadPoolExecutor(
            max_workers=_SHARED_EXECUTOR_SIZE,
            thread_name_prefix="wisp-worker",
        )
    return _SHARED_EXECUTOR


def set_shared_executor_size(size: int) -> None:
    """Configure the shared thread pool size. Must be called before first use."""
    global _SHARED_EXECUTOR_SIZE, _SHARED_EXECUTOR
    if _SHARED_EXECUTOR is not None:
        raise RuntimeError("Cannot resize executor after it has been created")
    _SHARED_EXECUTOR_SIZE = max(1, size)


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


class NonOwningExecutor(ThreadPoolExecutor):
    """Executor proxy for asyncio loop default-executor registration.

    Subclasses ThreadPoolExecutor ONLY because asyncio's
    set_default_executor isinstance-checks for it (bpo: base_events).


    asyncio shuts a loop's default executor when the loop closes
    (base_events.close -> _do_shutdown). When several loops outlive each
    other in one process — pytest-asyncio's function-scoped loops, server
    restarts, embedded embedding — that close killed the PROCESS-GLOBAL
    shared pool and every later to_thread raised "cannot schedule new
    futures after shutdown". Loops get this proxy instead: submit/map
    delegate to the shared pool, shutdown() is a no-op because ownership
    of the pool belongs to interpreter exit, not to any one loop.
    """

    def __init__(self, pool: "ThreadPoolExecutor"):
        # Bypass the parent constructor: no threads of our own — every call
        # delegates to `pool`. Parent __init__ would spin an unused pool.
        self._pool = pool
        self._max_workers = pool._max_workers

    def submit(
        self, fn: "Callable[..., Any]", /, *args: Any, **kwargs: Any
    ) -> "Future[Any]":
        return self._pool.submit(fn, *args, **kwargs)

    def map(
        self,
        fn: "Callable[..., Any]",
        *iterables: Any,
        timeout: float | None = None,
        chunksize: int = 1,
    ) -> "Iterator[Any]":
        return self._pool.map(fn, *iterables, timeout=timeout, chunksize=chunksize)

    def shutdown(self, wait=True, *, cancel_futures=False) -> None:
        # Deliberate no-op: see class docstring.
        pass


def non_owning_executor() -> NonOwningExecutor:
    """The shared pool wrapped so loop.close() cannot kill it."""
    return NonOwningExecutor(get_shared_executor())


def get_background_thread() -> threading.Thread | None:
    """Return the background worker thread, if it has been started."""
    return _loop_thread


def shutdown_background_loop(timeout: float = 5.0) -> None:
    """Stop the global background event loop and join its thread.

    Idempotent and thread-safe.  Call at process exit to avoid leaking
    the daemon thread.
    """
    global _loop_future, _loop_thread

    with _loop_lock:
        if _loop_future is None or not _loop_future.done():
            return  # never started or still starting

        loop = _loop_future.result()
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:
            pass  # already closed

        t = _loop_thread
        _loop_future = None
        _loop_thread = None

    if t is not None and t.is_alive():
        t.join(timeout=timeout)


# ── sync generator → async iterator bridge ──────────────────────────

async def sync_gen_iter(
    gen_factory: callable,
    executor: Any | None = None,
) -> AsyncIterator[Any]:
    """Consume a synchronous generator factory in a thread; yield items
    asynchronously without blocking the event loop.

    This is the minimal correct fix for the _arun blocking bug.  The
    synchronous requests.post(...) chain (via _run_turn_streaming_events
    → generate_stream_events → _post_stream) runs in a thread and yields
    events via an asyncio.Queue.

    Args:
        gen_factory: A zero-argument callable that returns a **fresh**
            synchronous iterator / generator.  Must be callable so that the
            bridge can create a new instance in the thread.
        executor: Optional ThreadPoolExecutor / asyncio executor.  If None
            ``asyncio.to_thread`` is used.

    Yields:
        Each item produced by the synchronous generator.

    Raises:
        Exception from the sync generator — propagated faithfully.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[bool, Any]] = asyncio.Queue()
    sentinel = object()
    stop_event = threading.Event()

    def _enqueue(ok: bool, item: Any) -> None:
        """Thread-safe fire-and-forget queue put."""
        try:
            asyncio.run_coroutine_threadsafe(queue.put((ok, item)), loop)
        except RuntimeError:
            pass  # event loop closed — nothing to do

    def _thread_target() -> None:
        """Consumes the sync generator in a thread and enqueues items."""
        # Make the event loop visible to sync code that needs to yield
        # (e.g. retry back-off in _post_stream).
        try:
            from wisp.ollama_client import _loop_local as _ll
            _ll.loop = loop
        except Exception:
            _ll = None  # type: ignore[assignment]
        try:
            gen = gen_factory()
            for item in gen:
                if stop_event.is_set():
                    break
                _enqueue(True, item)
            if not stop_event.is_set():
                _enqueue(False, sentinel)
        except Exception as exc:
            if not stop_event.is_set():
                _enqueue(False, exc)
        finally:
            # Prevent stale loop references from leaking across generator
            # lifetimes (e.g. dead loops after test reloads).
            if _ll is not None:
                try:
                    del _ll.loop
                except AttributeError:
                    pass

    # Start the consumer thread using the shared thread pool
    get_shared_executor().submit(_thread_target)

    try:
        while True:
            # Wait for next item with a check-cancellable timeout
            try:
                ok, item = await queue.get()
            except asyncio.CancelledError:
                stop_event.set()
                raise

            if not ok:
                # Completed or exception
                if isinstance(item, Exception):
                    raise item
                break  # sentinel, done

            yield item
    finally:
        # Ensure the thread terminates even if consumer is cancelled
        stop_event.set()
        # Drain any remaining items so the thread's queue.put() doesn't block
        # (important because run_coroutine_threadsafe().result() can hang if
        # the loop is gone by the time the coroutine runs).
        try:
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        except Exception:
            pass


# ── run_sync variants ─────────────────────────────────────────────────


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
    try:
        return future.result()
    except KeyboardInterrupt:
        # Cancel the background task so it can clean up (e.g. terminate
        # bash subprocesses) before the daemon thread dies.
        future.cancel()
        raise


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


async def drain_pending_tasks(loop: asyncio.AbstractEventLoop, timeout: float = 3.0) -> None:
    """Cancel every task still parked on *loop* and wait, bounded, for exit.

    Detached work (subagent children, background agents, provider bridges)
    must never outlive the scope that owns its loop — stragglers either
    corrupt state by writing during teardown or die as "Task was destroyed
    but it is pending" noise. Cancel-then-await gives cleanup code a real
    chance to run without letting a stuck task stall shutdown forever.
    """
    current = asyncio.current_task()
    pending = [
        t for t in asyncio.all_tasks(loop)
        if t is not current and not t.done()
    ]
    if not pending:
        return
    for task in pending:
        task.cancel()
    done, _still = await asyncio.wait(pending, timeout=timeout)
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            import logging
            logging.getLogger(__name__).debug(
                "Teardown reaped task error: %s: %s",
                type(task.exception()).__name__, task.exception(),
            )
    # Unclosed async generators (provider bridges!) are not tasks — their
    # finally-blocks only run when the loop finalizes them. Without this,
    # a turn killed mid-stream leaves the bridge thread's shutdown to
    # garbage-collection timing and emits destroyed-pending warnings.
    import contextlib
    with contextlib.suppress(RuntimeError):
        await loop.shutdown_asyncgens()
