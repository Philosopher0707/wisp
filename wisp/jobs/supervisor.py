"""The detached process that owns ONE job: `python -m wisp.jobs.supervisor <job dir>`.

It runs the command through `run_bash_confined`, the one execution path (BJ1), and records the outcome. It is its own session leader, so it
outlives the wisp process that started it.

Kill (BJ8) cannot rely on cancelling the run: the PTY tier runs in a worker thread that a cancel does not stop, its child has its own
session so a process-group kill misses it, and the Docker tier leaves the command running inside its container when the `docker exec`
client dies. So the supervisor kills the whole process TREE (found by parent pid) and removes its own sandbox containers.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

import wisp.jobs.procs as procs
from wisp.jobs.store import EXITED, KILLED, TIMED_OUT, _write_atomic, read_json

WATCHDOG_SLACK_S = 30.0


def _cleanup_sandboxes(workspace: str) -> None:
    """Remove the container(s) this process created; harmless when there are none."""
    try:
        from wisp.sandbox.router import get_router

        for tier in get_router(workspace).tiers:
            cleanup = getattr(tier, "cleanup", None)
            if callable(cleanup) and getattr(tier, "_container_ready", False):
                cleanup()
    except Exception:  # noqa: BLE001 — cleanup is best effort and must not hide the job's result
        pass


def _kill_tree(remembered: dict[int, str], job_id: str = "", groups: set[int] | None = None) -> list[int]:
    """Kill the job's processes: the tree as it is now plus every process seen earlier (a process whose parent exited is reparented to init and
    no longer shows up in the tree). `kill_all` checks each against its start time, so a reused pid is never touched."""
    targets = dict(remembered)
    targets.update(dict(procs.descendants(os.getpid())))
    mine = {os.getpgrp(), os.getpid()}
    seen_groups = {g for g in (groups or set()) | {procs.group_of(p) for p in targets} if g > 1 and g not in mine}
    targets.update(dict(procs.group_members(seen_groups)))  # an orphan reparented to init keeps its process group (works on macOS too)
    if job_id:
        targets.update(dict(procs.find_by_env(job_id)))  # Linux: every process the job started carries the marker
    targets.pop(os.getpid(), None)
    return procs.kill_all(list(targets.items()))


def _record(d: Path, status: str, **fields: object) -> None:
    _write_atomic(d / "state.json", {"status": status, "finished": time.time(), **fields})


async def _snapshot_children(d: Path, remembered: dict[int, str], groups: set[int]) -> None:
    """Remember the job's processes (in memory, for the kill, and on disk, so a reaper can find them if this supervisor is killed hard)."""
    while True:
        try:
            snap = await asyncio.to_thread(procs.descendants, os.getpid())
            remembered.update(dict(snap))
            groups.update(g for g in (procs.group_of(p) for p, _ in snap) if g > 1)
            _write_atomic(d / "children.json", {"children": [[p, s] for p, s in remembered.items()]})
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(0.25)


async def _amain(d: Path, meta: dict[str, Any]) -> int:
    from wisp.tools.errors import ToolError
    from wisp.tools.bash import _format_bash_output, run_bash_confined

    workspace = str(meta["workspace"])
    max_runtime = int(meta["max_runtime"])
    loop = asyncio.get_running_loop()
    reason = {"why": ""}
    remembered: dict[int, str] = {}
    groups: set[int] = set()
    run_task = asyncio.ensure_future(run_bash_confined(str(meta["command"]), workspace, timeout=max_runtime))

    def stop(why: str) -> None:
        reason["why"] = reason["why"] or why
        snap = procs.descendants(os.getpid())  # BEFORE the cancel: the tiers' own kill orphans grandchildren out of the tree
        remembered.update(dict(snap))
        groups.update(g for g in (procs.group_of(p) for p, _ in snap) if g > 1)
        run_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop, "killed")
    loop.call_later(max_runtime + WATCHDOG_SLACK_S, stop, "timed_out")  # the tiers enforce `timeout`; this is the backstop if one does not
    snapshots = asyncio.ensure_future(_snapshot_children(d, remembered, groups))
    try:
        run = await run_task
    except asyncio.CancelledError:
        forced = _kill_tree(remembered, str(meta["id"]), groups)
        _cleanup_sandboxes(workspace)
        status = TIMED_OUT if reason["why"] == "timed_out" else KILLED
        _record(d, status, exit_code=None, note=("killed" if status == KILLED else "past its deadline") + (f"; {len(forced)} process(es) needed SIGKILL" if forced else ""))
        return 143
    except ToolError as exc:
        _cleanup_sandboxes(workspace)
        (d / "out.log").write_text(f"[refused: {exc}]", encoding="utf-8")
        _record(d, EXITED, exit_code=-1, note=f"refused: {exc}")
        return 1
    finally:
        snapshots.cancel()
    _kill_tree(remembered, str(meta["id"]), groups)  # anything the command left running in the background is part of the job
    _cleanup_sandboxes(workspace)
    log = d / "out.log"
    log.write_text(_format_bash_output(run.returncode, run.stdout, run.stderr), encoding="utf-8")
    os.chmod(log, 0o600)
    _record(d, TIMED_OUT if run.timed_out else EXITED, exit_code=run.returncode, tier=run.provider, duration_ms=run.duration_ms)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m wisp.jobs.supervisor <job dir>", file=sys.stderr)
        return 2
    d = Path(argv[1])
    meta = read_json(d / "meta.json")
    if meta is None:
        return 2
    container = ""
    try:
        from wisp.sandbox.router import get_router

        tier = get_router(str(meta["workspace"])).route()
        container = getattr(tier, "container_name", "") if getattr(tier, "name", "") == "docker" else ""
    except Exception:  # noqa: BLE001
        pass
    _write_atomic(d / "supervisor.json", {"pid": os.getpid(), "start": procs.start_time(os.getpid()), "container": container})
    if (d / "state.json").exists():  # killed before it got going
        return 0
    return asyncio.run(_amain(d, meta))


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # a PTY worker thread must not hold the interpreter open after the result is recorded
