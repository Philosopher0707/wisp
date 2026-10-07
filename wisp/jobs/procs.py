"""Process helpers over `ps`, so they work on macOS and Linux without a dependency. POSIX only."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def _ps(args: list[str]) -> str:
    try:
        return subprocess.run(["ps", *args], capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001 — a missing or hung `ps` means "unknown", handled by callers
        return ""


def _stat_and_start(pid: int) -> tuple[str, str]:
    if pid <= 0:
        return "", ""
    parts = _ps(["-o", "stat=,lstart=", "-p", str(pid)]).split()
    return (parts[0], " ".join(parts[1:])) if len(parts) > 1 else ("", "")


def start_time(pid: int) -> str:
    """The process's start time as `ps` prints it, or '' when it does not exist. Paired with the pid it identifies one process even after pid reuse."""
    return _stat_and_start(pid)[1]


def alive(pid: int, started: str) -> bool:
    """True only when `pid` exists, is not a zombie, AND started at `started`: a reused pid is a different process."""
    stat, now = _stat_and_start(pid)
    return bool(now) and not stat.startswith("Z") and (not started or now == started)


def descendants(root: int) -> list[tuple[int, str]]:
    """Every descendant of `root` as (pid, start time), found by parent pid. This reaches a child that made its own session (the PTY tier does)."""
    children: dict[int, list[int]] = {}
    for line in _ps(["-axo", "pid=,ppid="]).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    out: list[int] = []
    stack = list(children.get(root, []))
    while stack:
        pid = stack.pop()
        out.append(pid)
        stack.extend(children.get(pid, []))
    return [(p, start_time(p)) for p in out]


def group_of(pid: int) -> int:
    out = _ps(["-o", "pgid=", "-p", str(pid)]).split()
    return int(out[0]) if out and out[0].isdigit() else 0


def group_members(pgids: set[int]) -> list[tuple[int, str]]:
    """Every live process in one of the process groups `pgids` (found by `ps`, so a reparented orphan that kept its group is still there)."""
    if not pgids:
        return []
    found: list[int] = []
    for line in _ps(["-axo", "pid=,pgid="]).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and int(parts[1]) in pgids and int(parts[0]) != os.getpid():
            found.append(int(parts[0]))
    return [(p, start_time(p)) for p in found]


def find_by_env(job_id: str) -> list[tuple[int, str]]:
    """Every process whose environment carries `WISP_JOB_ID=<job_id>`, however it was reparented. A job's processes inherit the marker, so this finds the
    orphan a command left behind (`cmd &` then exit). A process that scrubs its own environment (`env -i`) escapes it: a stated limit."""
    if not sys.platform.startswith("linux"):
        return []  # macOS `ps` hides other processes' environments in a listing: the process-group sweep is the portable path there
    needle = f"WISP_JOB_ID={job_id}"
    out: list[tuple[int, str]] = []
    for line in _ps(["-axeww", "-o", "pid=,command="]).splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) != os.getpid() and needle in parts[1].split():
            out.append((int(parts[0]), ""))
    return [(p, start_time(p)) for p, _ in out]


def kill_all(targets: list[tuple[int, str]], grace: float = 2.0) -> list[int]:
    """SIGTERM every live target (checked against its start time), wait up to `grace`, then SIGKILL what is left. Returns the pids that needed SIGKILL."""
    live = [(p, s) for p, s in targets if alive(p, s)]
    for pid, _ in live:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(alive(p, s) for p, s in live):
        time.sleep(0.05)
    forced = []
    for pid, started in live:
        if alive(pid, started):
            forced.append(pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    return forced
