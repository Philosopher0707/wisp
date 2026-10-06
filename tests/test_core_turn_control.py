"""Cancelling a turn that is waiting on a stalled provider, and bounding process exit.

Observed live: with the provider stalled, Ctrl-C printed "Interrupted — cancelling turn…" and the "waiting" clock kept running, so the
REPL stayed unresponsive until the provider answered (up to its 120 s read timeout), and a user mashing Ctrl-C ended in a shutdown
traceback. Cause: the SIGINT handlers called `task.cancel()` straight from the signal handler. That schedules the cancellation but
does not wake the event loop, which is asleep in `select()` waiting for the provider, so nothing ran it. `loop.call_soon_threadsafe`
writes to the loop's wake-up pipe; `core.turn_control.request_cancel` does that.

The second half: a non-daemon thread left running at interpreter shutdown makes Python wait for it forever
(`threading._shutdown` ... `lock.acquire()`). `core.shutdown.arm_exit_watchdog` bounds that, and only the real process entry arms it.
"""
from __future__ import annotations

import ast
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from wisp.core import shutdown
from wisp.core.shutdown import arm_exit_watchdog, exit_code_from
from wisp.core.turn_control import request_cancel

REPO = Path(__file__).resolve().parent.parent


def _py(script: str, *args: str, timeout: float = 30, env: dict | None = None) -> subprocess.CompletedProcess:
    e = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    e["PYTHONPATH"] = str(REPO)
    e.update(env or {})
    return subprocess.run([sys.executable, "-c", textwrap.dedent(script), *args], capture_output=True, text=True,
                          timeout=timeout, env=e, cwd=str(REPO))


# ── cancel: a REAL SIGINT while the loop sleeps in select() waiting for a provider that never answers ─────────────

_SIGINT_SCRIPT = """
    import asyncio, os, signal, sys, threading, time
    from wisp.core.turn_control import request_cancel
    mode = sys.argv[1]
    async def stalled_provider():
        await asyncio.get_running_loop().create_future()      # never answers: the loop sleeps in select()
    loop = asyncio.new_event_loop()
    task = loop.create_task(stalled_provider())
    def on_sigint(signum, frame):
        if mode == "naive":
            task.cancel()                                     # what the handlers did
        else:
            request_cancel(task)
    signal.signal(signal.SIGINT, on_sigint)
    threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
    def rescue():                                             # so the demonstration of the OLD behaviour terminates
        time.sleep(2.5)
        print("STILL BLOCKED", flush=True)
        loop.call_soon_threadsafe(lambda: None)
    threading.Thread(target=rescue, daemon=True).start()
    t0 = time.time()
    try:
        loop.run_until_complete(task)
    except asyncio.CancelledError:
        print(f"CANCELLED {time.time() - t0:.1f}", flush=True)
"""


def test_request_cancel_wakes_a_loop_asleep_waiting_on_a_stalled_provider():
    out = _py(_SIGINT_SCRIPT, "wake").stdout
    assert "CANCELLED" in out and "STILL BLOCKED" not in out
    assert float(out.split("CANCELLED")[1].split()[0]) < 1.5, "cancel is processed at once, not after the provider answers"


def test_the_old_direct_task_cancel_from_a_signal_handler_leaves_the_loop_asleep():
    """Documents the defect: the cancel was scheduled but nothing woke the loop until something else did (here, at 2.5 s)."""
    out = _py(_SIGINT_SCRIPT, "naive").stdout
    assert "STILL BLOCKED" in out, "if CPython ever starts waking the loop here, this test can go and the helper can stay"


def test_request_cancel_is_a_no_op_for_a_finished_or_missing_task():
    import asyncio

    assert request_cancel(None) is False

    async def quick():
        return 1

    loop = asyncio.new_event_loop()
    try:
        t = loop.create_task(quick())
        loop.run_until_complete(t)
        assert request_cancel(t) is False
    finally:
        loop.close()


def test_request_cancel_survives_a_closed_loop():
    import asyncio

    loop = asyncio.new_event_loop()
    t = loop.create_task(asyncio.sleep(10))
    loop.close()
    assert request_cancel(t) is False


def test_both_sigint_handlers_use_the_shared_helper_not_a_bare_task_cancel():
    for rel, func in (("wisp/cli/repl.py", "_on_sigint"), ("wisp/entry.py", "_repl_sigint_handler")):
        tree = ast.parse((REPO / rel).read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
        assert any(getattr(c.func, "id", "") == "request_cancel" or getattr(c.func, "attr", "") == "request_cancel"
                   for c in calls), f"{rel}:{func} must cancel through core.turn_control.request_cancel"
        assert not any(getattr(c.func, "attr", "") == "cancel" and getattr(c.func.value, "id", "") == "task" for c in calls), (
            f"{rel}:{func} still calls task.cancel() directly from the signal handler")


# ── bounded exit ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_watchdog_forces_the_exit_after_the_grace_period():
    fired: list[int] = []
    t = arm_exit_watchdog(grace_s=0.2, code=7, exit_fn=fired.append)
    assert t is not None and t.daemon, "a daemon thread: it must never itself keep the process alive"
    deadline = time.time() + 3
    while not fired and time.time() < deadline:
        time.sleep(0.05)
    assert fired == [7]


def test_a_non_positive_grace_disables_it():
    fired: list[int] = []
    assert arm_exit_watchdog(grace_s=0, code=0, exit_fn=fired.append) is None
    assert arm_exit_watchdog(grace_s=-1, code=0, exit_fn=fired.append) is None
    time.sleep(0.2)
    assert fired == []


def test_the_grace_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("WISP_EXIT_GRACE_S", raising=False)
    assert shutdown.default_grace_s() == 5.0
    monkeypatch.setenv("WISP_EXIT_GRACE_S", "2.5")
    assert shutdown.default_grace_s() == 2.5
    monkeypatch.setenv("WISP_EXIT_GRACE_S", "0")
    assert shutdown.default_grace_s() == 0.0
    monkeypatch.setenv("WISP_EXIT_GRACE_S", "not-a-number")
    assert shutdown.default_grace_s() == 5.0, "a bad value never disables the safety net"


@pytest.mark.parametrize("raw,expected", [(None, 0), (0, 0), (3, 3), ("boom", 1), (True, 1)])
def test_system_exit_codes_map_like_python_does(raw, expected):
    assert exit_code_from(raw) == expected


_HANG = """
    import sys, threading, time
    from wisp.core.shutdown import arm_exit_watchdog
    threading.Thread(target=time.sleep, args=(60,)).start()      # a NON-daemon thread that never finishes
    if sys.argv[1] == "arm":
        arm_exit_watchdog(grace_s=1.0, code=3)
"""


def test_a_process_stuck_on_a_non_daemon_thread_is_released_with_its_exit_code():
    started = time.time()
    done = _py(_HANG, "arm", timeout=20)
    assert done.returncode == 3, "the real exit code survives the forced exit"
    assert time.time() - started < 15


def test_without_the_watchdog_the_same_process_hangs():
    """The control: this is the hang the watchdog bounds (`threading._shutdown` waits for the thread)."""
    with pytest.raises(subprocess.TimeoutExpired):
        _py(_HANG, "off", timeout=4)


def test_only_the_real_process_entry_arms_it_so_tests_that_call_main_are_never_killed():
    tree = ast.parse((REPO / "wisp" / "__main__.py").read_text(encoding="utf-8"))
    main_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main")
    entry_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "entry")

    def called(fn: ast.FunctionDef) -> set[str]:
        return {getattr(c.func, "id", getattr(c.func, "attr", "")) for c in ast.walk(fn) if isinstance(c, ast.Call)}

    assert "arm_exit_watchdog" in called(entry_fn) and "arm_exit_watchdog" not in called(main_fn)
    src = (REPO / "wisp" / "__main__.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":\n    entry()' in src
    assert 'wisp = "wisp.__main__:entry"' in (REPO / "pyproject.toml").read_text(encoding="utf-8")


def test_the_real_entry_releases_a_stuck_process_when_main_returns():
    done = _py("""
        import threading, time
        import wisp.__main__ as m
        m.main = lambda: threading.Thread(target=time.sleep, args=(60,)).start()   # main returns, a stuck thread remains
        m.entry()
    """, timeout=25, env={"WISP_EXIT_GRACE_S": "1"})
    assert done.returncode == 0


_STUCK_THEN = """
    import sys, threading, time
    import wisp.__main__ as m
    def fake_main():
        threading.Thread(target=time.sleep, args=(60,)).start()     # a stuck NON-daemon thread on every exit path
        {action}
    m.main = fake_main
    m.entry()
"""


@pytest.mark.parametrize("action,expected", [
    ("sys.exit(5)", 5),
    ("raise SystemExit", 0),
    ("raise RuntimeError('crash')", 1),
])
def test_the_entry_releases_a_stuck_process_on_every_exit_path_and_keeps_the_exit_code(action, expected):
    """A normal exit would never reveal a missing watchdog, so each path leaves a stuck thread behind: without the watchdog this
    hangs (and the subprocess timeout fails the test)."""
    done = _py(_STUCK_THEN.format(action=action), timeout=25, env={"WISP_EXIT_GRACE_S": "1"})
    assert done.returncode == expected


def test_a_keyboard_interrupt_with_a_stuck_thread_is_released_and_ends_by_sigint():
    done = _py(_STUCK_THEN.format(action="raise KeyboardInterrupt"), timeout=25, env={"WISP_EXIT_GRACE_S": "1"})
    assert done.returncode in (130, -signal.SIGINT), "130 as a shell sees it (subprocess reports a signal death as -2)"
