"""Bounding process exit: a safety net against a thread that never lets Python finish.

Python waits for every non-daemon thread at interpreter shutdown (``threading._shutdown``, ``lock.acquire()``). One that never
finishes (a stalled network call run in an executor, a worker blocked on a pipe) leaves a finished wisp session hanging in the
terminal until the user mashes Ctrl-C into a traceback. By the time this is armed the work is done (the session and history are
saved before ``main`` returns), so after a short grace period the process is ended with its real exit code.

``arm_exit_watchdog`` starts a **daemon** thread (it must never itself keep the process alive). The process entry point
(``wisp.__main__.entry``) arms it; ``main()`` does not, so in-process callers such as the test suite are never killed by it.

Standard library only (core primitive).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable

__all__ = ["DEFAULT_GRACE_S", "arm_exit_watchdog", "default_grace_s", "exit_code_from"]

DEFAULT_GRACE_S = 5.0


def default_grace_s() -> float:
    """The grace period: ``WISP_EXIT_GRACE_S`` seconds (0 disables), else 5. A malformed value never disables the net."""
    raw = os.environ.get("WISP_EXIT_GRACE_S")
    if raw is None:
        return DEFAULT_GRACE_S
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_GRACE_S


def exit_code_from(code: object) -> int:
    """The process exit status for a ``SystemExit.code``, as Python itself maps it: None -> 0, an int as is, anything else -> 1."""
    if code is None:
        return 0
    if isinstance(code, bool):
        return int(code)
    if isinstance(code, int):
        return code
    return 1


def arm_exit_watchdog(
    grace_s: float | None = None,
    code: int = 0,
    *,
    exit_fn: Callable[[int], object] = os._exit,
) -> threading.Thread | None:
    """After ``grace_s`` seconds, end the process with ``code``. Returns the (daemon) thread, or None when disabled (grace <= 0).

    ``os._exit`` skips interpreter shutdown, so the standard streams are flushed first. If the process finishes on its own sooner,
    the daemon thread simply dies with it.
    """
    grace = default_grace_s() if grace_s is None else grace_s
    if grace <= 0:
        return None

    def _watch() -> None:
        time.sleep(grace)
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except Exception:
                pass
        exit_fn(code)

    thread = threading.Thread(target=_watch, name="wisp-exit-watchdog", daemon=True)
    thread.start()
    return thread
