"""M7: ghost SQLite rows for background runs.

Two mechanisms produced rows claiming status="running" forever:
1. cancel() wrote "cancelled", but the CancelledError skipped the
   except-Exception branch and _execute's finally re-persisted the stale
   in-memory status="running" OVER the cancellation.
2. Process death left "running" rows nothing would ever update; the runs
   list polluted permanently after every restart.
"""

import asyncio
import time

import pytest

from wisp.background_agent import (
    BackgroundRunner,
    _PROCESS_START,
    reap_orphaned_runs,
)


class _MemStore:
    """bg_* surface over a dict — mirrors the SQLite store's semantics."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def bg_create(self, run: dict):
        self.rows[run["id"]] = {
            "id": run["id"], "prompt": run.get("prompt", ""),
            "model": run.get("model", "unknown"),
            "workspace": run.get("workspace", "."),
            "status": run.get("status", "pending"),
            "created_at": run.get("created_at", 0),
            "started_at": None, "finished_at": None, "content": None,
            "tool_calls": "[]", "files_changed": "[]", "error": None,
            "iterations": 0,
        }

    def bg_get(self, run_id):
        row = self.rows.get(run_id)
        if row is None:
            return None
        d = dict(row)
        d["run_id"] = d.pop("id")
        return d

    def bg_update(self, run_id, **kwargs):
        self.rows[run_id].update(kwargs)

    def bg_list(self):
        # real store maps row.id → "run_id" in list results
        out = []
        for r in self.rows.values():
            d = dict(r)
            d["run_id"] = d.pop("id")
            out.append(d)
        return out

    def bg_delete(self, run_id):
        self.rows.pop(run_id, None)


def _runner() -> tuple[BackgroundRunner, _MemStore]:
    store = _MemStore()
    runner = BackgroundRunner.__new__(BackgroundRunner)
    runner._store = store
    runner._tasks = {}
    runner.workspace = "/tmp"
    return runner, store


@pytest.mark.asyncio
async def test_cancel_persists_cancelled_not_running():
    """The resurrection bug: finally-block must not overwrite the
    cancellation with the stale 'running' status."""
    runner, store = _runner()
    store.bg_create({"id": "bg-x", "status": "pending"})
    store.bg_update("bg-x", status="running", started_at=time.time())

    import wisp.entry as entry_mod
    orig = entry_mod.run_headless

    async def _parked(**kwargs):
        await asyncio.Event().wait()  # park forever; test cancels the task

    entry_mod.run_headless = _parked
    try:
        store.bg_update("bg-x", status="running")
        task = asyncio.create_task(runner._execute("bg-x"))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        entry_mod.run_headless = orig
    row = store.bg_get("bg-x")
    assert row["status"] == "cancelled", (
        f"ghost resurrected: {row['status']=}"
    )
    assert "Cancel" in (row["error"] or "")


@pytest.mark.asyncio
async def test_process_restart_reaps_stale_running_rows():
    store = _MemStore()
    old_start = _PROCESS_START - 1000
    fresh_start = time.time()
    store.bg_create({"id": "ghost", "status": "pending"})
    store.bg_update("ghost", status="running", started_at=old_start)
    store.bg_create({"id": "live-now", "status": "pending"})
    store.bg_update("live-now", status="running", started_at=fresh_start)
    store.bg_create({"id": "finished-old", "status": "done"})

    reaped = reap_orphaned_runs(store)
    assert reaped == 1
    assert store.bg_get("ghost")["status"] == "failed"
    assert "restarted" in store.bg_get("ghost")["error"].lower()
    assert store.bg_get("live-now")["status"] == "running", (
        "must not touch runs owned by a live process"
    )
    assert store.bg_get("finished-old")["status"] == "done"


@pytest.mark.asyncio
async def test_row_without_started_at_is_reaped():
    """started_at NULL + status running can only be pre-start corruption."""
    store = _MemStore()
    store.bg_create({"id": "no-ts", "status": "running"})
    assert reap_orphaned_runs(store) == 1
    assert store.bg_get("no-ts")["status"] == "failed"
