"""The clock, and the one place wall-clock time may be read.

## Why this is a module and not a habit

*Replay is identical only under a deterministic clock.* Everything this layer
records — when an idempotency record started, how long an `IN_PROGRESS` holder has
been running — is a **fact about the run**, and a fact read from the host's
wall-clock is a fact that differs between the run and its replay. A trace that
records `time.time()` cannot be replayed identically even when every other input is
pinned, and the divergence is invisible until someone tries.

So time is **injected**, and this module holds the only call to `time.time()` in
`wisp/runtime/`. That is the invariant a guard enforces
(`tests/reliability/test_clock_injection.py`), by AST rather than by grep — a
string scan over Python reads docstrings as code (F41/F77's class, and this file
mentions `time.time()` in prose precisely so the guard has to be real).

## Why a Protocol and not a base class

The guard takes anything with `now()`. A test supplies `ManualClock` and advances it
by hand — **never by sleeping**: a test that sleeps is slow, flaky, and measures the
scheduler rather than the code. A production caller supplies `SystemClock` or its
own, and the layer never learns which.

## What this does NOT make deterministic

Only the *reading* of time. A run whose behaviour depends on a race, on the
scheduler, or on a network will still diverge — this removes one source, and the
tension table's cost (*you need a clock abstraction*) is exactly this scope: an
abstraction, not a guarantee.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Anything that can say what time it is.

    `now()` returns **seconds**, monotonic in spirit — the absolute epoch does not
    matter and callers must only subtract two readings. That is deliberate: a
    duration is replayable, an absolute timestamp is not.
    """

    def now(self) -> float: ...


@dataclass
class SystemClock:
    """The production clock. **The only `time.time()` call in this layer.**"""

    def now(self) -> float:
        return time.time()


@dataclass
class ManualClock:
    """A clock a caller moves by hand. The reason the abstraction exists.

    Never sleeps, so a test of a sixty-second staleness threshold runs in
    microseconds and measures the threshold rather than the scheduler.
    """

    _now: float = field(default=0.0)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError(
                "a clock does not go backwards; a negative advance would make a "
                "stale record look fresh and hide the condition being tested")
        self._now += seconds
        return self._now

    def set(self, seconds: float) -> float:
        """Jump to an absolute reading. Provided for replay, where a trace's
        recorded instants are replayed rather than accumulated."""
        self._now = seconds
        return self._now


def is_clock(obj: object) -> bool:
    """Whether `obj` satisfies the protocol — the guard's own check, so a caller
    can validate an injected clock once rather than discovering it at first use."""
    return isinstance(obj, Clock)


__all__ = ["Clock", "SystemClock", "ManualClock", "is_clock"]
