"""Idempotency: the seven ways a key mechanism fails, and what each one costs.

A tool that mutates something is retried for reasons the caller cannot control — a
timeout, a dropped connection, a user pressing enter twice, a supervisor
restarting a turn. An idempotency key is the mechanism that makes the retry safe.
This module is the mechanism, and it is organised as **the failure table**, because
a key mechanism that handles the happy path and none of the failures is worse than
no mechanism: it produces confidence without the property.

| # | the failure | the mitigation, and where it lives here |
|---|---|---|
| 1 | crash after the side effect, before storing the result | `IN_PROGRESS` is written **before** the effect runs; the effect is handed the key so it can be idempotent downstream |
| 2 | two requests race on the same key | `begin` is a **conditional write** (`insert_if_absent`) — exactly one caller wins |
| 3 | the key is reused with a different body | the record stores a **fingerprint**; a mismatch raises `KeyReuse` |
| 4 | the store is unavailable | a **stated policy**: `FAIL_CLOSED` (reject) or `FAIL_OPEN` (allow, risky) — per domain, never a default |
| 5 | the client generates a new key on retry | the key is **derivable** (`key_for`) so a retry of the same request is the same key; a fingerprint seen under a *different* key is detected and reported |
| 6 | a very long-running operation | `PENDING` + a poll handle, so a caller can answer **202 Accepted** and a polling URL |
| 7 | PII in the stored response | a **redactor is applied before storing**, not before displaying |

**Row 5 is the one people get wrong**, and it is worth stating why the fix is not a
check. If the client mints a fresh key per attempt, no store can tell the retry
from a new request — the information is simply absent. So the mitigation is to make
the stable key the *easy* one: `key_for(tool, args)` derives it from the request, so
a caller that uses it gets stability for free. The detection below is a **tripwire
for a bug**, not a substitute for the derivation.

**Row 4 has no safe answer**, and this module refuses to pretend otherwise. Fail
closed and a storage blip rejects legitimate work; fail open and a duplicate side
effect slips through. The policy is a **required** argument with no default, so the
choice is made by the domain that bears the cost rather than by this module.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Callable, Protocol

from wisp.core.action_key import action_key
from wisp.runtime.clock import Clock, SystemClock


class RecordState(StrEnum):
    """A record's state. `IN_PROGRESS` exists so a crash is *visible*."""

    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class OutagePolicy(StrEnum):
    """What to do when the store cannot answer (row 4). **No default.**"""

    FAIL_CLOSED = "fail_closed"
    FAIL_OPEN = "fail_open"


class IdempotencyError(RuntimeError):
    """Base for this module's failures. `code` is what a caller branches on."""

    code = "idempotency_error"


class KeyReuse(IdempotencyError):
    """The same key arrived with a different body (row 3)."""

    code = "key_reused"

    def __init__(self, key: str, *, stored: str, arrived: str) -> None:
        super().__init__(
            f"key {key!r} was first used with fingerprint {stored!r} and is now "
            f"presented with {arrived!r}. A key identifies ONE request; reusing it "
            "for a different body would return the first request's result for the "
            "second request's work."
        )
        self.key, self.stored, self.arrived = key, stored, arrived


class UnstableKey(IdempotencyError):
    """A fingerprint arrived under a second key (row 5) — a retry minted a new key.

    This is a **detection of a caller bug**, not a defence: by the time it fires the
    retry has already been let through once. `key_for` exists so callers do not have
    to get this right by hand.
    """

    code = "unstable_key"

    def __init__(self, fingerprint: str, *, first_key: str, second_key: str) -> None:
        super().__init__(
            f"the same request (fingerprint {fingerprint!r}) arrived under two keys "
            f"({first_key!r} then {second_key!r}). A key that changes between "
            "attempts defeats the mechanism entirely: the store sees a new request "
            "and the side effect runs twice. Derive the key with `key_for`."
        )
        self.fingerprint = fingerprint
        self.first_key, self.second_key = first_key, second_key


class StoreUnavailable(IdempotencyError):
    """The store could not answer and the policy is `FAIL_CLOSED` (row 4)."""

    code = "store_unavailable"


class Outcome(StrEnum):
    """What the guard did. Returned, not inferred from side effects.

    Each carries the HTTP status a service should answer with, so the mapping is
    stated once here rather than re-derived at every route.
    """

    #: We ran it. 201 — a new resource.
    EXECUTED = "executed"
    #: It had already completed; here is the recorded result. 200.
    REPLAYED = "replayed"
    #: **A concurrent duplicate.** Another request holds this key and is running
    #: right now. 409 — the caller must not treat this as success and must not
    #: retry immediately, because the work is already in flight.
    CONFLICT = "conflict"
    #: The caller asked for an async story (it supplied a poll handle) and the
    #: work is still running. 202 + the handle. This is **not** the default for a
    #: duplicate — see `CONFLICT`.
    PENDING = "pending"

    @property
    def http_status(self) -> int:
        return _STATUS[self]


_STATUS: dict["Outcome", int] = {
    Outcome.EXECUTED: 201,
    Outcome.REPLAYED: 200,
    Outcome.CONFLICT: 409,
    Outcome.PENDING: 202,
}


@dataclass(frozen=True)
class Record:
    key: str
    fingerprint: str
    state: RecordState
    result: Any = None
    started_at: float = 0.0
    poll_url: str = ""


@dataclass(frozen=True)
class GuardResult:
    """The outcome, the record, and — for `PENDING` — the handle to poll."""

    outcome: Outcome
    record: Record

    @property
    def replayed(self) -> bool:
        return self.outcome is Outcome.REPLAYED

    @property
    def status_code(self) -> int:
        """The HTTP status a service should answer with. Stated once, in `Outcome`."""
        return self.outcome.http_status

    @property
    def poll_url(self) -> str:
        return self.record.poll_url


class Store(Protocol):
    """What the guard needs from storage. Deliberately three methods.

    `begin` is the **conditional write** (row 2): it returns the existing record and
    `False` if one was there, or a fresh `IN_PROGRESS` record and `True` if this
    caller created it. A read-then-write pair would let two callers both see nothing
    and both proceed.
    """

    def begin(self, key: str, fingerprint: str) -> tuple[Record, bool]: ...
    def finish(self, key: str, *, state: RecordState, result: Any = None) -> None: ...
    def find_by_fingerprint(self, fingerprint: str) -> Record | None: ...


class InMemoryStore:
    """A store for tests and single-process use.

    **Not durable**, and it says so: a crash loses every record, which is exactly the
    condition row 1 exists for. A production caller supplies a store that survives
    the process — the guard cannot check that, and does not pretend to.
    """

    def __init__(self, clock: Clock | None = None) -> None:
        # A clock, never a direct wall-clock read: `started_at` is a fact about the
        # run, and a fact read from the host's clock differs between a run and its
        # replay. See `wisp/runtime/clock.py`.
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._records: dict[str, Record] = {}
        self._by_fingerprint: dict[str, str] = {}

    def begin(self, key: str, fingerprint: str) -> tuple[Record, bool]:
        existing = self._records.get(key)
        if existing is not None:
            return existing, False
        record = Record(key=key, fingerprint=fingerprint,
                        state=RecordState.IN_PROGRESS, started_at=self._clock.now())
        self._records[key] = record
        self._by_fingerprint.setdefault(fingerprint, key)
        return record, True

    def finish(self, key: str, *, state: RecordState, result: Any = None) -> None:
        current = self._records[key]
        self._records[key] = replace(current, state=state, result=result)

    def find_by_fingerprint(self, fingerprint: str) -> Record | None:
        key = self._by_fingerprint.get(fingerprint)
        return self._records.get(key) if key else None


def key_for(tool: str, args: Any) -> str:
    """The **stable** key for a request (row 5).

    Derived from the tool and its arguments, so a retry of the same request is the
    same key without the caller having to remember anything. Delegates to
    `wisp.core.action_key.action_key`, which is the one canonical form of
    (tool, arguments) in this repository — a second derivation would be a second
    identity for one request, which is the bug this function exists to prevent.
    """
    return action_key(tool, args)


class IdempotencyGuard:
    """Runs an effect at most once per key, and records what happened.

    The order of operations is the mechanism (row 1): **`IN_PROGRESS` is written
    before the effect runs.** If the process dies mid-effect, the record says so —
    a retry finds `IN_PROGRESS` and does **not** silently re-run a side effect that
    may already have happened. That is the difference between "we do not know" and
    "we ran it twice".
    """

    def __init__(self, store: Store, *, on_outage: OutagePolicy,
                 redact: Callable[[Any], Any] | None = None,
                 poll_after_s: float | None = None,
                 clock: Clock | None = None) -> None:
        # `on_outage` is required and has NO default: row 4 has no safe answer, so
        # the choice must be made by the domain that bears the cost.
        if not isinstance(on_outage, OutagePolicy):
            raise IdempotencyError(
                f"on_outage must be an OutagePolicy, got {on_outage!r}. There is no "
                "default: failing closed rejects legitimate work, failing open "
                "allows a duplicate side effect, and only the domain knows which it "
                "can afford."
            )
        self._store = store
        self._on_outage = on_outage
        self._redact = redact
        self._poll_after_s = poll_after_s
        self._clock: Clock = clock if clock is not None else SystemClock()

    @property
    def on_outage(self) -> OutagePolicy:
        return self._on_outage

    def run(self, key: str, fingerprint: str, effect: Callable[[str], Any], *,
            poll_url: str = "") -> GuardResult:
        """Run `effect(key)` at most once for `key`. Returns what happened.

        `effect` receives the **key**, not just the arguments: row 1's second half is
        that the side effect itself must be idempotent downstream, and it can only
        be if it is told the identity of the operation.
        """
        if not key:
            raise IdempotencyError(
                "the key is empty. An absent key is not a permissive key — it means "
                "the caller has no way to tell a retry from a new request.")

        try:
            record, created = self._store.begin(key, fingerprint)
        except Exception as exc:                       # row 4
            if self._on_outage is OutagePolicy.FAIL_OPEN:
                # Fail OPEN means "allow the work", so the work must actually run.
                # The first version of this branch returned `EXECUTED` without
                # calling the effect at all — the worst of both worlds: no
                # protection *and* no work, reported as success. The test caught it.
                #
                # There is no record to write (the store is down), which is exactly
                # what fail-open gives up: a retry during the outage will run again.
                result = effect(key)
                stored = self._redact(result) if self._redact is not None else result
                return GuardResult(Outcome.EXECUTED,
                                   Record(key=key, fingerprint=fingerprint,
                                          state=RecordState.COMPLETED,
                                          result=stored, poll_url=poll_url))
            raise StoreUnavailable(
                f"the idempotency store is unavailable ({exc!r}) and this domain "
                "fails CLOSED: without the store we cannot tell a retry from a new "
                "request, so the safe answer is to do nothing."
            ) from exc

        if not created:
            # Someone already holds this key.
            if record.fingerprint != fingerprint:      # row 3
                raise KeyReuse(key, stored=record.fingerprint, arrived=fingerprint)
            if record.state is RecordState.COMPLETED:
                return GuardResult(Outcome.REPLAYED, record)      # the happy replay
            if record.state is RecordState.FAILED:
                # A recorded failure is a real outcome: replaying it is honest, and
                # re-running would repeat a side effect the record says happened.
                return GuardResult(Outcome.REPLAYED, record)
            # **A concurrent duplicate.** Another request holds this key and is
            # running right now. The default answer is 409 CONFLICT: this caller
            # must neither re-run the effect nor be told it succeeded. Reporting
            # success here would be the double-execution the mechanism exists to
            # prevent, just with a nicer status code.
            #
            # 202 is available, but only when the caller supplies a poll handle —
            # that is the caller *declaring* it has an async story. Without one
            # there is nothing to poll, and "pending" would be a status with no
            # handle: the caller could never learn the outcome. So the async shape
            # is opt-in, and the safe default is the conflict.
            if poll_url:
                return GuardResult(Outcome.PENDING, replace(record, poll_url=poll_url))
            return GuardResult(Outcome.CONFLICT, record)

        # We won the race. Tripwire for row 5: has this exact request already run
        # under a different key? If so the caller is minting keys per attempt.
        seen = self._store.find_by_fingerprint(fingerprint)
        if seen is not None and seen.key != key:
            raise UnstableKey(fingerprint, first_key=seen.key, second_key=key)

        try:
            result = effect(key)
        except Exception:
            # The effect failed. Record the failure so a retry replays it rather
            # than repeating whatever part of the side effect did land.
            self._store.finish(key, state=RecordState.FAILED)
            raise

        # Row 7 — redact BEFORE storing. Redacting on the way out would leave the
        # PII in the store, which is the place it is hardest to remove.
        stored = self._redact(result) if self._redact is not None else result
        self._store.finish(key, state=RecordState.COMPLETED, result=stored)
        return GuardResult(Outcome.EXECUTED,
                           Record(key=key, fingerprint=fingerprint,
                                  state=RecordState.COMPLETED, result=stored,
                                  poll_url=poll_url))

    def is_stale(self, record: Record, *, now: float | None = None) -> bool:
        """True when an `IN_PROGRESS` record has outlived `poll_after_s`.

        Row 1's other half. A record stuck in `IN_PROGRESS` is either a long run or
        a **dead holder** — and the two are indistinguishable from the record alone.
        This reports the *suspicion* and refuses to act on it: reclaiming a stale
        record would re-run a side effect that may have landed, so the decision
        belongs to the caller. With no `poll_after_s` configured it is always False,
        because inventing a staleness threshold is inventing a policy.
        """
        if self._poll_after_s is None or record.state is not RecordState.IN_PROGRESS:
            return False
        age = (now if now is not None else self._clock.now()) - record.started_at
        return age > self._poll_after_s


__all__ = [
    "RecordState", "OutagePolicy", "IdempotencyError", "KeyReuse", "UnstableKey",
    "StoreUnavailable", "Outcome", "Record", "GuardResult", "Store", "InMemoryStore",
    "IdempotencyGuard", "key_for",
]
