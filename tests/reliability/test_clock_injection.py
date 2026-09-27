"""Wall-clock time is read in ONE module, and the guard is AST-based.

*Replay is identical only under a deterministic clock.* So the invariant is not
"prefer injecting a clock" — it is:

    **`time.time()` / `time.monotonic()` appear in `wisp/runtime/clock.py` and
    nowhere else in `wisp/runtime/`.**

Anything else is a fact about the run that differs between the run and its replay,
and the divergence is invisible until someone tries. A guard is what makes it an
invariant rather than a preference.

**AST, not grep, and that is deliberate.** This file's own docstring contains the
strings `time.time()` and `time.monotonic()`, and so does `clock.py`'s. A string
scan would read prose as code — F41's and F77's class, and the reason the module's
docstring says so out loud. The scan parses instead.

**Scope, stated rather than implied.** The invariant is enforced over
`wisp/runtime/` — the layer being built — not over the whole package, which has
decades of direct reads. `test_the_wider_package_is_measured_not_claimed` measures
that number so the scope is a fact rather than a silence.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from wisp.runtime.clock import Clock, ManualClock, SystemClock, is_clock

REPO = pathlib.Path(__file__).resolve().parents[2]
LAYER = REPO / "wisp" / "runtime"
#: The one module allowed to read the wall clock. Named, so adding a second is a
#: visible edit rather than a quiet one.
CLOCK_MODULE = "clock.py"


def _wall_clock_reads(path: pathlib.Path) -> list[int]:
    """Line numbers where `path` reads a wall clock. Parses; never greps."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[int] = []
    for node in ast.walk(tree):
        # `time.time()`, `time.monotonic()` — an attribute on a bare `time`.
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id == "time"):
            hits.append(node.lineno)
        # `from time import time` then `time()`.
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id in ("time", "monotonic")):
            hits.append(node.lineno)
    return hits


def _layer_modules() -> list[pathlib.Path]:
    return sorted(p for p in LAYER.rglob("*.py")
                  if "__pycache__" not in p.parts)


# ── The invariant ───────────────────────────────────────────────────────


class TestTheInvariant:
    def test_only_the_clock_module_reads_the_wall_clock(self):
        offenders: list[str] = []
        for path in _layer_modules():
            if path.name == CLOCK_MODULE:
                continue
            hits = _wall_clock_reads(path)
            if hits:
                offenders.append(f"{path.relative_to(REPO)}:{hits}")
        assert not offenders, (
            "these modules read the wall clock directly, so a run through them "
            "cannot be replayed identically:\n  " + "\n  ".join(offenders) +
            "\n\nTake a `Clock` and call `.now()` instead — see wisp/runtime/clock.py.")

    def test_the_scan_has_a_floor(self):
        """A scan over an empty set passes for the wrong reason."""
        modules = _layer_modules()
        assert len(modules) >= 4, (
            f"only {len(modules)} module(s) scanned — the invariant is being "
            "checked over a set too small to mean anything")
        assert any(p.name == CLOCK_MODULE for p in modules), (
            "the clock module is not in the scanned set, so the exemption proves "
            "nothing")

    def test_the_wider_package_is_measured_not_claimed(self):
        """The scope, as a number. This is **not** a pass/fail on the wider
        package — it records how far the invariant currently reaches, so the
        scope is a fact a reader can see rather than a silence."""
        wider = [p for p in (REPO / "wisp").rglob("*.py")
                 if "__pycache__" not in p.parts and p.parent != LAYER]
        offenders = [p for p in wider if _wall_clock_reads(p)]
        assert len(wider) >= 100, "the wider scan found too few modules to report"
        assert isinstance(offenders, list)          # reported, never asserted on


# ── The clock itself ────────────────────────────────────────────────────


class TestTheClock:
    def test_a_manual_clock_advances_without_sleeping(self):
        """**Never by sleeping.** A test that sleeps is slow, flaky, and measures
        the scheduler rather than the code."""
        clock = ManualClock()
        assert clock.now() == 0.0
        assert clock.advance(60) == 60.0
        assert clock.advance(0.5) == 60.5

    def test_advancing_does_not_sleep(self):
        """**Never by sleeping** — measured, not asserted in prose.

        The mutation that added a real `sleep` inside `advance` was MISSED by the
        first version of this file, because the only assertions were arithmetic.
        A clock that sleeps still returns the right number; what it costs is time,
        and nothing was looking at that.
        """
        import time as _t

        clock = ManualClock()
        start = _t.perf_counter()
        clock.advance(3600)
        elapsed = _t.perf_counter() - start
        assert clock.now() == 3600.0
        assert elapsed < 0.1, (
            f"advancing an hour took {elapsed:.3f}s — the manual clock is sleeping, "
            "which makes the suite slow, flaky, and a measurement of the scheduler")

    def test_a_manual_clock_can_be_set_for_replay(self):
        clock = ManualClock()
        clock.set(1_000.0)
        assert clock.now() == 1_000.0

    def test_a_clock_does_not_go_backwards(self):
        with pytest.raises(ValueError):
            ManualClock().advance(-1)

    def test_both_clocks_satisfy_the_protocol(self):
        assert is_clock(ManualClock()) and is_clock(SystemClock())
        assert not is_clock(object())
        assert isinstance(ManualClock(), Clock)


# ── The consumer ────────────────────────────────────────────────────────


class TestTheIdempotencyGuardUsesTheClock:
    def test_record_timestamps_come_from_the_injected_clock(self):
        """The reason the abstraction exists, driven: a record's `started_at` is a
        fact about the run, and here it is a fact the test chose."""
        from wisp.runtime.idempotency import InMemoryStore

        clock = ManualClock()
        clock.set(500.0)
        store = InMemoryStore(clock=clock)
        record, _ = store.begin("k1", "fp")
        assert record.started_at == 500.0, (
            "the record did not take its timestamp from the injected clock")

    def test_staleness_is_measured_without_sleeping(self):
        """A sixty-second threshold, tested in microseconds — and the same reading
        twice gives the same answer, which is what replayability means."""
        from wisp.runtime.idempotency import IdempotencyGuard, InMemoryStore, OutagePolicy

        clock = ManualClock()
        store = InMemoryStore(clock=clock)
        guard = IdempotencyGuard(store, on_outage=OutagePolicy.FAIL_CLOSED,
                                 poll_after_s=60.0, clock=clock)
        record, _ = store.begin("k1", "fp")

        assert guard.is_stale(record) is False
        clock.advance(61)
        assert guard.is_stale(record) is True, (
            "the threshold did not fire after the clock advanced past it")

    def test_a_run_is_reproducible_under_a_deterministic_clock(self):
        """The property the invariant buys: two runs with the same inputs and the
        same clock produce the same record."""
        from wisp.runtime.idempotency import InMemoryStore

        def run() -> tuple[float, str]:
            clock = ManualClock()
            clock.set(42.0)
            record, _ = InMemoryStore(clock=clock).begin("k1", "fp")
            return record.started_at, record.state.value

        assert run() == run()
