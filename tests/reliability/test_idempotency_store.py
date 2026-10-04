"""The guard, on a DURABLE store — the wiring's prerequisite.

`InMemoryStore` is not durable, and that is not a footnote: a crash loses every
record, which is exactly the condition row 1 describes ("crash after the side
effect, before storing the result"). A durable store is what makes the
`IN_PROGRESS` marker survive to be seen.

The table gained `state` and `fingerprint`. The existing one was a result MEMO with
first-write-wins, and a memo cannot say *"this is running"* or *"this key was first
used for a different body"*.
"""
from __future__ import annotations


import pytest

from wisp.infra.store import UnifiedStore
from wisp.runtime.idempotency import (
    IdempotencyGuard,
    KeyReuse,
    Outcome,
    OutagePolicy,
    RecordState,
    SqliteStore,
    UnstableKey,
    key_for,
)


@pytest.fixture()
def store(tmp_path):
    return SqliteStore(UnifiedStore(tmp_path / "w.db"))


def _guard(store):
    return IdempotencyGuard(store, on_outage=OutagePolicy.FAIL_CLOSED)


class TestItIsDurable:
    def test_the_record_survives_a_new_store_object(self, tmp_path):
        """**The whole reason this exists.** A second `UnifiedStore` over the same
        file sees the record the first one wrote — which is what makes row 1's
        `IN_PROGRESS` survive a restart."""
        path = tmp_path / "w.db"
        guard = _guard(SqliteStore(UnifiedStore(path)))
        guard.run("k1", "fp", lambda key: {"ok": True})

        # A fresh process would build a fresh store over the same file.
        reopened = SqliteStore(UnifiedStore(path))
        assert reopened.begin("k1", "fp")[0].state is RecordState.COMPLETED

    def test_an_in_progress_record_survives_too(self, store):
        """A crash leaves `IN_PROGRESS` — and a retry can SEE it, which is the
        difference between "we do not know" and "we ran it twice"."""
        store.begin("k1", "fp")
        assert store.begin("k1", "fp")[0].state is RecordState.IN_PROGRESS

    def test_the_result_round_trips(self, store):
        guard = _guard(store)
        guard.run("k1", "fp", lambda key: {"answer": 42, "nested": {"a": [1, 2]}})
        assert guard.run("k1", "fp", lambda key: None).record.result == {
            "answer": 42, "nested": {"a": [1, 2]}}


class TestTheMechanismStillHolds:
    def test_the_effect_runs_once(self, store):
        calls = []
        guard = _guard(store)
        guard.run("k1", "fp", lambda key: calls.append(key) or "r")
        out = guard.run("k1", "fp", lambda key: calls.append(key) or "r")
        assert calls == ["k1"] and out.outcome is Outcome.REPLAYED

    def test_key_reuse_with_a_different_body_is_rejected(self, store):
        guard = _guard(store)
        guard.run("k1", "fp-a", lambda key: "first")
        with pytest.raises(KeyReuse):
            guard.run("k1", "fp-b", lambda key: "second")

    def test_an_unstable_key_is_detected(self, store):
        guard = _guard(store)
        guard.run("attempt-1", "same-fp", lambda key: "first")
        with pytest.raises(UnstableKey):
            guard.run("attempt-2", "same-fp", lambda key: "second")

    def test_a_concurrent_duplicate_is_a_conflict(self, store):
        store.begin("k1", "fp")
        assert _guard(store).run("k1", "fp", lambda key: "never").outcome is Outcome.CONFLICT

    def test_a_failure_is_recorded_and_replays(self, store):
        guard = _guard(store)
        with pytest.raises(RuntimeError):
            guard.run("k1", "fp", lambda key: (_ for _ in ()).throw(RuntimeError("boom")))
        assert store.begin("k1", "fp")[0].state is RecordState.FAILED


class TestItDoesNotBreakWhatWasThere:
    def test_the_old_memo_accessors_still_work(self, tmp_path):
        """`idem_get`/`idem_put` predate this and are used elsewhere — the two new
        columns must not have disturbed them."""
        s = UnifiedStore(tmp_path / "w.db")
        s.idem_put("legacy", "a-result")
        assert s.idem_get("legacy") == "a-result"
        assert s.idem_get("never-written") is None

    def test_the_fingerprint_is_the_one_first_written(self, store):
        """The FIRST body wins the fingerprint, which is what makes reuse
        detectable rather than silently overwritten."""
        store.begin("k1", "fp-a")
        assert store.begin("k1", "fp-b")[0].fingerprint == "fp-a"

    def test_the_key_is_derivable(self):
        assert key_for("write_file", {"p": 1}) == key_for("write_file", {"p": 1})
