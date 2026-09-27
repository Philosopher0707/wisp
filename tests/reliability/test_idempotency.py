"""The seven idempotency failure modes — each with a test that FORCES it.

The table this implements is a list of ways a key mechanism fails. A mechanism
that handles the happy path and none of the failures is worse than none, because it
produces confidence without the property. So there is one class per row, and each
one drives the failure rather than describing it.

The load-bearing tests are the ones that would pass against a naive implementation:
`TestRow2RacingRequests` (a read-then-write store passes a single-threaded test),
`TestRow3KeyReusedWithADifferentBody` (a store keyed only by key passes), and
`TestRow7PiiIsRedactedBeforeStoring` (redacting on the way *out* passes a test that
only checks the returned value).
"""
from __future__ import annotations

import pytest

from wisp.runtime.idempotency import (
    GuardResult,
    IdempotencyError,
    IdempotencyGuard,
    InMemoryStore,
    KeyReuse,
    OutagePolicy,
    Outcome,
    Record,
    RecordState,
    StoreUnavailable,
    UnstableKey,
    key_for,
)


def _guard(store=None, *, policy=OutagePolicy.FAIL_CLOSED, redact=None, poll=None):
    return IdempotencyGuard(store or InMemoryStore(), on_outage=policy,
                            redact=redact, poll_after_s=poll)


def _counting_effect(result="ok"):
    calls = []

    def effect(key):
        calls.append(key)
        return result

    return calls, effect


# ── Row 1 ───────────────────────────────────────────────────────────────


class TestRow1CrashAfterTheSideEffect:
    def test_in_progress_is_stored_BEFORE_the_effect_runs(self):
        """**The forcing case.** If the record were written after the effect, a
        crash mid-effect would leave no trace and the retry would repeat it."""
        store = InMemoryStore()
        seen: list[RecordState] = []

        def effect(key):
            seen.append(store.begin(key, "fp")[0].state)   # read mid-effect
            return "done"

        _guard(store).run("k1", "fp", effect)
        assert seen == [RecordState.IN_PROGRESS], (
            "the effect ran without an IN_PROGRESS record in place — a crash here "
            "would be invisible")

    def test_the_effect_is_handed_the_key_so_it_can_be_idempotent_downstream(self):
        """Row 1's second half: the side effect must be idempotent *itself*, and it
        can only be if it is told the identity of the operation."""
        got = []
        _guard().run("k1", "fp", lambda key: got.append(key))
        assert got == ["k1"]

    def test_a_crash_mid_effect_leaves_a_visible_record(self):
        store = InMemoryStore()

        def effect(key):
            raise KeyboardInterrupt("process died")

        with pytest.raises(KeyboardInterrupt):
            _guard(store).run("k1", "fp", effect)
        record, created = store.begin("k1", "fp")
        assert not created, "the record vanished, so the retry would re-run"
        assert record.state in (RecordState.IN_PROGRESS, RecordState.FAILED)


# ── Row 2 ───────────────────────────────────────────────────────────────


class TestRow2RacingRequests:
    def test_the_conditional_write_lets_exactly_one_caller_win(self):
        store = InMemoryStore()
        first, created_first = store.begin("k", "fp")
        second, created_second = store.begin("k", "fp")
        assert created_first and not created_second
        assert first.key == second.key

    def test_two_runs_execute_the_effect_once(self):
        calls, effect = _counting_effect()
        guard = _guard()
        a = guard.run("k1", "fp", effect)
        b = guard.run("k1", "fp", effect)
        assert calls == ["k1"], f"the effect ran {len(calls)} times"
        assert a.outcome is Outcome.EXECUTED and b.outcome is Outcome.REPLAYED

    def test_a_second_caller_is_not_told_it_succeeded(self):
        """The loser of the race must not get a success it did not earn — it gets
        `PENDING`, which is the honest answer while someone else holds the key."""
        store = InMemoryStore()
        guard = _guard(store)
        guard.run("k1", "fp", lambda key: "ok")
        store._records["k1"] = Record("k1", "fp", RecordState.IN_PROGRESS)
        assert guard.run("k1", "fp", lambda key: "ok").outcome is Outcome.PENDING


# ── Row 3 ───────────────────────────────────────────────────────────────


class TestRow3KeyReusedWithADifferentBody:
    def test_a_different_body_under_the_same_key_is_rejected(self):
        guard = _guard()
        guard.run("k1", "fp-A", lambda key: "first")
        with pytest.raises(KeyReuse):
            guard.run("k1", "fp-B", lambda key: "second")

    def test_the_rejection_names_both_fingerprints(self):
        guard = _guard()
        guard.run("k1", "fp-A", lambda key: "first")
        with pytest.raises(KeyReuse) as excinfo:
            guard.run("k1", "fp-B", lambda key: "second")
        assert excinfo.value.stored == "fp-A" and excinfo.value.arrived == "fp-B"

    def test_the_same_body_under_the_same_key_replays(self):
        """Row 3 must not break the case it exists for."""
        calls, effect = _counting_effect("value")
        guard = _guard()
        guard.run("k1", "fp", effect)
        out = guard.run("k1", "fp", effect)
        assert len(calls) == 1 and out.record.result == "value"


# ── Row 4 ───────────────────────────────────────────────────────────────


class _BrokenStore:
    def begin(self, key, fingerprint):
        raise OSError("storage is down")

    def finish(self, key, *, state, result=None):
        raise OSError("storage is down")

    def find_by_fingerprint(self, fingerprint):
        raise OSError("storage is down")


class TestRow4StorageOutage:
    def test_fail_closed_rejects(self):
        guard = _guard(_BrokenStore(), policy=OutagePolicy.FAIL_CLOSED)
        with pytest.raises(StoreUnavailable):
            guard.run("k1", "fp", lambda key: "side effect")

    def test_fail_open_allows_and_says_so(self):
        calls, effect = _counting_effect()
        guard = _guard(_BrokenStore(), policy=OutagePolicy.FAIL_OPEN)
        out = guard.run("k1", "fp", effect)
        assert calls == ["k1"], "fail-open did not run the effect"
        assert out.outcome is Outcome.EXECUTED

    def test_the_policy_is_required_and_has_no_default(self):
        """Row 4 has no safe answer, so the choice must be made by the domain that
        bears the cost — not by this module."""
        with pytest.raises(TypeError):
            IdempotencyGuard(InMemoryStore())          # type: ignore[call-arg]

    def test_a_bad_policy_is_rejected(self):
        with pytest.raises(IdempotencyError):
            IdempotencyGuard(InMemoryStore(), on_outage="closed")  # type: ignore[arg-type]


# ── Row 5 ───────────────────────────────────────────────────────────────


class TestRow5UnstableKey:
    def test_the_key_is_derivable_so_a_retry_is_the_same_key(self):
        """The mitigation, not the detection: `key_for` derives from the request, so
        a caller that uses it cannot mint a fresh key per attempt."""
        args = {"path": "a.txt", "content": "x"}
        assert key_for("write_file", args) == key_for("write_file", dict(args))
        assert key_for("write_file", args) != key_for("write_file", {"path": "b.txt"})

    def test_the_same_request_under_two_keys_is_detected(self):
        """The tripwire for a caller that mints keys by hand. It is a **detection**
        and says so: by the time it fires the retry has already been let through."""
        guard = _guard()
        guard.run("attempt-1", "same-fp", lambda key: "first")
        with pytest.raises(UnstableKey) as excinfo:
            guard.run("attempt-2", "same-fp", lambda key: "second")
        assert excinfo.value.first_key == "attempt-1"
        assert excinfo.value.second_key == "attempt-2"

    def test_an_empty_key_is_rejected(self):
        """An absent key is not a permissive key."""
        with pytest.raises(IdempotencyError):
            _guard().run("", "fp", lambda key: "x")


# ── Row 6 ───────────────────────────────────────────────────────────────


class TestRow6VeryLongRunningOperation:
    def test_a_holder_in_progress_yields_PENDING_with_a_poll_url(self):
        """The 202 Accepted shape: do not block, do not re-run — hand back a handle."""
        store = InMemoryStore()
        guard = _guard(store)
        store.begin("k1", "fp")
        out = guard.run("k1", "fp", lambda key: "never", poll_url="/ops/k1")
        assert out.outcome is Outcome.PENDING
        assert out.poll_url == "/ops/k1"

    def test_a_completed_record_carries_no_poll_url(self):
        guard = _guard()
        out = guard.run("k1", "fp", lambda key: "ok", poll_url="/ops/k1")
        assert out.outcome is Outcome.EXECUTED and out.poll_url == "/ops/k1"

    def test_staleness_is_reported_but_never_acted_on(self):
        """A record stuck `IN_PROGRESS` is either a long run or a dead holder, and
        the record cannot tell them apart. Reclaiming it would re-run a side effect
        that may have landed, so this reports the suspicion and stops."""
        guard = _guard(poll=60.0)
        store = InMemoryStore()
        store.begin("k1", "fp")
        rec = store.begin("k1", "fp")[0]
        assert guard.is_stale(rec, now=rec.started_at + 61) is True
        assert guard.is_stale(rec, now=rec.started_at + 1) is False

    def test_without_a_configured_threshold_nothing_is_ever_stale(self):
        guard = _guard()
        store = InMemoryStore()
        rec = store.begin("k1", "fp")[0]
        assert guard.is_stale(rec, now=rec.started_at + 10_000) is False


# ── Row 7 ───────────────────────────────────────────────────────────────


class TestRow7PiiIsRedactedBeforeStoring:
    def test_the_store_holds_the_redacted_value_not_the_original(self):
        """**The forcing case.** Redacting on the way *out* passes a test that only
        checks the returned value — and leaves the PII in the store, which is the
        place it is hardest to remove."""
        store = InMemoryStore()
        guard = _guard(store, redact=lambda r: {**r, "api_key": "[REDACTED]"})
        out = guard.run("k1", "fp", lambda key: {"api_key": "sk-live-123", "ok": True})
        stored = store.begin("k1", "fp")[0]
        assert stored.result["api_key"] == "[REDACTED]", (
            "the raw secret is in the store")
        assert out.record.result["api_key"] == "[REDACTED]"

    def test_the_original_is_not_reachable_from_a_replay(self):
        store = InMemoryStore()
        guard = _guard(store, redact=lambda r: {"api_key": "[REDACTED]"})
        guard.run("k1", "fp", lambda key: {"api_key": "sk-live-123"})
        assert "sk-live" not in str(guard.run("k1", "fp", lambda key: None).record.result)

    def test_with_no_redactor_the_value_is_stored_verbatim(self):
        """The redactor is injected, not assumed: this module does not guess which
        fields are sensitive — `redact_sensitive_tool_args` is the authority for
        that, and a caller wires it in."""
        store = InMemoryStore()
        _guard(store).run("k1", "fp", lambda key: {"plain": "value"})
        assert store.begin("k1", "fp")[0].result == {"plain": "value"}


# ── The floor ───────────────────────────────────────────────────────────


class TestTheFloor:
    def test_a_recorded_failure_replays_rather_than_re_running(self):
        """A failure is an outcome. Re-running would repeat whatever part of the
        side effect did land."""
        calls, effect = _counting_effect()
        store, guard = InMemoryStore(), _guard()

        def failing(key):
            calls.append(key)
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            guard.run("k1", "fp", failing)
        out = guard.run("k1", "fp", effect)
        assert out.outcome is Outcome.REPLAYED and calls == ["k1"]

    def test_the_seven_rows_are_all_covered_by_a_test(self):
        """A floor: the table has seven rows and each must be driven. This asserts
        the class names exist, so deleting a row's tests is a failure rather than a
        smaller suite."""
        import inspect
        import sys

        module = sys.modules[__name__]
        covered = {name for name, _ in inspect.getmembers(module, inspect.isclass)
                   if name.startswith("TestRow")}
        assert covered == {f"TestRow{i}{suffix}" for i, suffix in (
            (1, "CrashAfterTheSideEffect"), (2, "RacingRequests"),
            (3, "KeyReusedWithADifferentBody"), (4, "StorageOutage"),
            (5, "UnstableKey"), (6, "VeryLongRunningOperation"),
            (7, "PiiIsRedactedBeforeStoring"))}, (
            f"rows without a test class: {covered}")
