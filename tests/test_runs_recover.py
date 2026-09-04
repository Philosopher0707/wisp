# tests/test_runs_recover.py — manager persistence + crash recovery (M3 J1).
import asyncio

from wisp.infra.store import UnifiedStore
from wisp.multi_agent.background import BackgroundAgentManager
from wisp.multi_agent.task import SubagentContract, SubagentResult
from wisp.runs.record import RunRecord, RunState
from wisp.runs.store import SQLiteRunStore


class _FakeOrch:
    def __init__(self, delay=0.02, success=True):
        self.delay = delay
        self.success = success

    async def _run_with_retry(self, contract):
        await asyncio.sleep(self.delay)
        return SubagentResult(task_id=contract.name, success=self.success,
                              output="done", files_changed=[],
                              elapsed_seconds=self.delay, error=None,
                              session_id="sess-1")


def _mgr(tmp_path, orch=None):
    store = SQLiteRunStore(UnifiedStore(tmp_path / "w.db"))
    mgr = BackgroundAgentManager(orch or _FakeOrch(), run_store=store)
    return mgr, store


def _contract():
    return SubagentContract(name="bg-t", role="coder", task="do it")


async def _launch_scenario(mgr, store, wait=0.0):
    # Single loop: asyncio.run() teardown cancels live tasks, so launch,
    # observe, and settle must share one loop (as in production). Reads
    # happen inside the loop — teardown cancellation persists afterwards
    # (faithfully: the agent really was cancelled).
    out = await mgr.launch(_contract())
    assert out["ok"]
    if wait:
        await mgr.result(out["agent_id"], wait_seconds=wait)
    rec = store.get(out["agent_id"])
    ts = store.transitions(out["agent_id"])
    return out, rec, ts


def test_launch_persists_running_row(tmp_path):
    mgr, store = _mgr(tmp_path, _FakeOrch(delay=5.0))
    try:
        out, rec, ts = asyncio.run(_launch_scenario(mgr, store))
        assert rec is not None and rec.status == RunState.RUNNING
        assert [(t.from_state, t.to_state) for t in ts] == [("queued", "running")]
    finally:
        mgr.shutdown_pending()


def test_settlement_persists(tmp_path):
    mgr, store = _mgr(tmp_path)
    out, _, _ = asyncio.run(_launch_scenario(mgr, store, wait=5))
    res = asyncio.run(mgr.result(out["agent_id"]))
    assert res["ok"] and res["status"] == "completed"
    rec = store.get(out["agent_id"])
    assert rec.status == RunState.SUCCEEDED
    assert store.transitions(out["agent_id"])[-1].to_state == "succeeded"


def test_cancel_persists(tmp_path):
    async def scenario():
        out = await mgr.launch(_contract())
        mgr.cancel(out["agent_id"])
        return out

    mgr, store = _mgr(tmp_path, _FakeOrch(delay=5.0))
    out = asyncio.run(scenario())
    rec = store.get(out["agent_id"])
    assert rec.status == RunState.CANCELLED
    mgr.shutdown_pending()


def test_recover_parks_stale_rows(tmp_path):
    mgr, store = _mgr(tmp_path)
    # Simulate a previous process: rows with no live owner.
    store.create(RunRecord(run_id="stale-run", prompt="p", status=RunState.RUNNING))
    store.create(RunRecord(run_id="stale-q", prompt="p", status=RunState.QUEUED))
    store.create(RunRecord(run_id="done-old", prompt="p", status=RunState.RUNNING))
    store.transition("done-old", RunState.RUNNING, RunState.SUCCEEDED)
    report = mgr.recover()
    assert store.get("stale-run").status == RunState.PAUSED
    assert store.get("stale-q").status == RunState.CANCELLED
    assert store.get("done-old").status == RunState.SUCCEEDED
    assert report == {"paused": 1, "cancelled": 1, "left": 0}


def test_recover_respects_live_leases(tmp_path):
    mgr, store = _mgr(tmp_path)
    store.create(RunRecord(run_id="live", prompt="p", status=RunState.RUNNING))
    assert store.claim_lease("live", owner="other-proc", ttl_s=600) is True
    report = mgr.recover(lease_owner="me")
    assert store.get("live").status == RunState.RUNNING
    assert report["left"] == 1
