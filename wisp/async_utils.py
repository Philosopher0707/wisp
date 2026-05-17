"""Async utilities for Wisp — safe sync wrappers for async code.

Provides run_sync() to consume async generators from sync contexts
without creating nested event loops.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, AsyncIterator, TypeVar

T = TypeVar("T")


def run_sync_coro(coro) -> Any:
    """Run a coroutine from a sync context.

    Handles both standalone and nested event-loop contexts:
    - If no loop is running: uses asyncio.run()
    - If a loop is running: offloads to a dedicated thread

    Args:
        coro: A coroutine to run.

    Returns:
        The coroutine's return value.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: Any = None
    error: Exception | None = None

    def _target():
        nonlocal result, error
        nloop = asyncio.new_event_loop()
        try:
            result = nloop.run_until_complete(coro)
        except Exception as exc:
            error = exc
        finally:
            nloop.close()

    t = threading.Thread(target=_target)
    t.start()
    t.join()

    if error is not None:
        raise error
    return result


def run_sync(agen: AsyncIterator[T]) -> list[T]:
    """Consume an async generator from a sync context.

    Handles both standalone and nested event-loop contexts:
    - If no loop is running: uses asyncio.run()
    - If a loop is running: offloads to a dedicated thread

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
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop running — safe to use asyncio.run()
        return asyncio.run(_consume(agen))

    # Already inside a running loop — need a dedicated thread
    result: list[T] = []
    error: Exception | None = None

    def _target():
        nonlocal result, error
        nloop = asyncio.new_event_loop()
        try:
            result = nloop.run_until_complete(_consume(agen))
        except Exception as exc:
            error = exc
        finally:
            nloop.close()

    t = threading.Thread(target=_target)
    t.start()
    t.join()

    if error is not None:
        raise error
    return result


async def _consume(agen: AsyncIterator[T]) -> list[T]:
    """Consume an async generator into a list."""
    result: list[T] = []
    async for item in agen:
        result.append(item)
    return result
