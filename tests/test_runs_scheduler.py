# tests/test_runs_scheduler.py — bounded admission + leases + idempotency (M3 J2).
from wisp.infra.store import UnifiedStore
from wisp.runs.record import RunRecord, RunState
from wisp.runs.scheduler import Admission, Scheduler
from wisp.runs.store import SQLiteRunStore


def _sched(tmp_path, max_running=2):
    store = SQLiteRunStore(UnifiedStore(tmp_path / "w.db"))
    return Scheduler(store, max_running=max_running, owner="test-proc"), store


def test_admit_until_bound_then_deny(tmp_path):
    sched, store = _sched(tmp_path, max_running=2)
    assert sched.admit("a") == Admission(allowed=True)
    store.create(RunRecord(run_id="a", status=RunState.RUNNING))
    assert sched.admit("b").allowed is True
    store.create(RunRecord(run_id="b", status=RunState.RUNNING))
    denied = sched.admit("c")
    assert denied.allowed is False and "limit" in denied.reason


def test_finished_rows_free_capacity(tmp_path):
    sched, store = _sched(tmp_path, max_running=1)
    store.create(RunRecord(run_id="a", status=RunState.RUNNING))
    assert sched.admit("b").allowed is False
    store.transition("a", RunState.RUNNING, RunState.SUCCEEDED)
    assert sched.admit("b").allowed is True


def test_duplicate_run_id_rejected(tmp_path):
    sched, store = _sched(tmp_path)
    store.create(RunRecord(run_id="a", status=RunState.RUNNING))
    denied = sched.admit("a")
    assert denied.allowed is False and "duplicate" in denied.reason


def test_heartbeat_renews_own_leases(tmp_path):
    sched, store = _sched(tmp_path)
    store.create(RunRecord(run_id="a", status=RunState.RUNNING))
    assert store.claim_lease("a", owner="test-proc", ttl_s=60) is True
    renewed = sched.heartbeat()
    assert renewed == ["a"]
    # another owner's live lease is untouched
    store.create(RunRecord(run_id="b", status=RunState.RUNNING))
    assert store.claim_lease("b", owner="other", ttl_s=600) is True
    assert sched.heartbeat() == ["a"]


def test_idempotent_guard(tmp_path):
    sched, _ = _sched(tmp_path)
    assert sched.already_done("key-1") is False
    sched.memoize("key-1", '{"ok": true}')
    assert sched.already_done("key-1") is True
    # second write does not overwrite (first write wins)
    sched.memoize("key-1", '{"ok": false}')
    assert sched.recall("key-1") == '{"ok": true}'
