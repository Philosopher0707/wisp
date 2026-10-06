"""Cancelling a running turn from a signal handler or another thread.

A turn runs as an asyncio task on the main thread's event loop, and while the provider has not answered that loop is asleep in
``select()``. The REPL's SIGINT handlers used to call ``task.cancel()`` directly: that schedules the cancellation but writes nothing
to the loop's wake-up pipe, so with a stalled provider the loop never ran it. Ctrl-C printed "Interrupted — cancelling turn…" and the
REPL stayed unresponsive until the provider answered (up to its 120 s read timeout), which sent users on to pressing Ctrl-C again and
again. ``loop.call_soon_threadsafe`` is the call that wakes a sleeping loop; this is the one place that does it, so every handler
cancels the same way.

Standard library only (core primitive).
"""

from __future__ import annotations

import asyncio

__all__ = ["request_cancel"]


def request_cancel(task: asyncio.Future | None) -> bool:
    """Ask ``task`` to cancel, waking its event loop. Safe from a signal handler or any thread.

    Returns True when a cancellation was requested, False when there was nothing to cancel (no task, already done, or its loop is
    closed). It only requests: the task ends when its coroutine handles the ``CancelledError``.
    """
    if task is None or task.done():
        return False
    try:
        task.get_loop().call_soon_threadsafe(task.cancel)
    except RuntimeError:  # the loop is closed: nothing is left to wake
        return False
    return True
