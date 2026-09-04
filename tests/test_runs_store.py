# tests/test_runs_store.py — RunStore over a real tmp SQLite store.
import pytest
from wisp.infra.store import UnifiedStore
from wisp.runs.record import RunRecord, RunState
from wisp.runs.store import SQLiteRunStore


@pytest.fixture()
def rstore(tmp_path):
    return SQLiteRunStore(UnifiedStore(tmp_path / "wisp.db"))


def test_create_get_round_trip(rstore):
    rec = RunRecord(run_id="bg-1", prompt="do things", workspace="/w",
                    status=RunState.RUNNING)
    rstore.create(rec)
    back = rstore.get("bg-1")
    assert back == rec


def test_transition_appends_and_moves_state(rstore):
    rstore.create(RunRecord(run_id="bg-2", status=RunState.RUNNING))
    rstore.transition("bg-2", RunState.RUNNING, RunState.PAUSED, reason="user")
    assert rstore.get("bg-2").status == RunState.PAUSED
    ts = rstore.transitions("bg-2")
    assert len(ts) == 1 and ts[0].from_state == "running"
    assert ts[0].to_state == "paused" and ts[0].reason == "user"


def test_illegal_transition_rejected(rstore):
    rstore.create(RunRecord(run_id="bg-3", status=RunState.QUEUED))
    with pytest.raises(ValueError, match="illegal"):
        rstore.transition("bg-3", RunState.QUEUED, RunState.SUCCEEDED)
    assert rstore.get("bg-3").status == RunState.QUEUED
    assert rstore.transitions("bg-3") == []


def test_terminal_immutable(rstore):
    rstore.create(RunRecord(run_id="bg-4", status=RunState.RUNNING))
    rstore.transition("bg-4", RunState.RUNNING, RunState.SUCCEEDED)
    with pytest.raises(ValueError, match="immutable|illegal"):
        rstore.transition("bg-4", RunState.SUCCEEDED, RunState.RUNNING)


def test_legacy_statuses_still_parse(rstore):
    store = rstore._store
    store.bg_create({"id": "legacy-1", "prompt": "p", "status": "pending"})
    assert rstore.get("legacy-1").status == RunState.QUEUED
    store.bg_create({"id": "legacy-2", "prompt": "p", "status": "completed"})
    assert rstore.get("legacy-2").status == RunState.SUCCEEDED


def test_idempotency_round_trip(rstore):
    assert rstore.idempotent_get("k1") is None
    rstore.idempotent_put("k1", '{"ok": true}')
    assert rstore.idempotent_get("k1") == '{"ok": true}'


def test_lease_claim_and_expiry(rstore):
    rstore.create(RunRecord(run_id="bg-5", status=RunState.RUNNING))
    assert rstore.claim_lease("bg-5", owner="proc-A", ttl_s=60) is True
    # second owner loses while lease live
    assert rstore.claim_lease("bg-5", owner="proc-B", ttl_s=60) is False
    # same owner may renew
    assert rstore.claim_lease("bg-5", owner="proc-A", ttl_s=60) is True
    # lapsed lease is claimable by a new owner
    assert rstore.claim_lease("bg-5", owner="proc-A", ttl_s=-1) is True
    assert rstore.claim_lease("bg-5", owner="proc-B", ttl_s=60) is True
    assert rstore.claim_lease("bg-5", owner="proc-C", ttl_s=60) is False
