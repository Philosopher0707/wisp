"""Telemetry ring accounting: incremental, and measured in the right unit.

Context (see PHASE_BOUNDARY_FORENSIC.md / F12):

`SubagentTelemetryBuffer.append` recomputed the ring's size on every call:

    while len(buf) > self._max_events or sum(len(e.text) for e in buf) > self._max_bytes:

Two defects:

1. **O(n) per append, O(n^2) amortised** — the sum was recomputed on every
   append *and on every eviction step*, on a structure written on every
   subagent lifecycle event.
2. **Wrong unit** — the ring counted *characters* while `snap.bytes` counted
   *UTF-8 bytes*, against a bound named `_max_bytes`. A ring of non-ASCII text
   could hold up to ~4x the intended byte budget.

The running total is now maintained incrementally in bytes, matching
`snap.bytes` and the constant's name.
"""

from __future__ import annotations


from wisp.multi_agent.telemetry import SubagentTelemetryBuffer


def _ring_bytes(buf: SubagentTelemetryBuffer, agent_id: str) -> int:
    """The buffer's own running total, read under its lock."""
    with buf._lock:
        return buf._ring_bytes.get(agent_id, 0)


def _actual_bytes(buf: SubagentTelemetryBuffer, agent_id: str) -> int:
    return sum(len(e.text.encode("utf-8", "replace"))
               for e in buf.transcript(agent_id, last_n=0) or buf._events[agent_id])


# ── The running total stays consistent ───────────────────────────────

def test_running_total_matches_the_ring_contents():
    buf = SubagentTelemetryBuffer(max_events=1000, max_bytes=10_000_000)
    for i in range(50):
        buf.append("a", "progress", f"event {i}")
    with buf._lock:
        expected = sum(len(e.text.encode("utf-8", "replace")) for e in buf._events["a"])
    assert _ring_bytes(buf, "a") == expected


def test_running_total_tracks_eviction():
    buf = SubagentTelemetryBuffer(max_events=5, max_bytes=10_000_000)
    for i in range(40):
        buf.append("a", "progress", f"event-{i}")
    with buf._lock:
        ring = list(buf._events["a"])
    assert len(ring) == 5
    assert [e.text for e in ring] == [f"event-{i}" for i in range(35, 40)]
    with buf._lock:
        expected = sum(len(e.text.encode("utf-8", "replace")) for e in ring)
    assert _ring_bytes(buf, "a") == expected


def test_drop_clears_the_running_total():
    buf = SubagentTelemetryBuffer()
    buf.append("a", "progress", "hello")
    assert _ring_bytes(buf, "a") > 0
    buf.drop("a")
    assert _ring_bytes(buf, "a") == 0


# ── The byte bound is measured in bytes, not characters ──────────────

def test_byte_bound_is_enforced_in_bytes_not_characters():
    """Multi-byte text must consume the budget it actually occupies."""
    # Each 'é' is 2 UTF-8 bytes. Budget 100 bytes -> ~50 chars fit.
    buf = SubagentTelemetryBuffer(max_events=10_000, max_bytes=100)
    for _ in range(200):
        buf.append("a", "progress", "é" * 10)   # 20 bytes per event
    with buf._lock:
        ring = list(buf._events["a"])
        total = sum(len(e.text.encode("utf-8", "replace")) for e in ring)
    assert total <= 100, f"ring holds {total} bytes against a 100-byte bound"
    # At 20 bytes/event, at most 5 fit.
    assert len(ring) <= 5


def test_event_count_bound_still_applies():
    buf = SubagentTelemetryBuffer(max_events=3, max_bytes=10_000_000)
    for i in range(10):
        buf.append("a", "progress", f"e{i}")
    with buf._lock:
        assert len(buf._events["a"]) == 3


def test_ring_never_exceeds_the_byte_bound():
    """The invariant, regardless of event sizes."""
    buf = SubagentTelemetryBuffer(max_events=10_000, max_bytes=64)
    for size in (1, 5, 200, 3, 64, 65, 1000):
        buf.append("a", "progress", "x" * size)
        with buf._lock:
            total = sum(len(e.text.encode("utf-8", "replace"))
                        for e in buf._events["a"])
        assert total <= 64, f"ring holds {total} bytes against a 64-byte bound"


def test_an_event_larger_than_the_byte_bound_empties_the_ring():
    """Pre-existing behaviour, preserved: an event that alone exceeds the byte
    bound is evicted, leaving an empty ring.

    Unreachable with the shipped defaults — MAX_EVENT_CHARS (4000) x 4 bytes is
    the largest an event can be, which is far below DEFAULT_MAX_BYTES
    (256_000). Pinned here so the behaviour is explicit rather than incidental.
    """
    buf = SubagentTelemetryBuffer(max_events=100, max_bytes=10)
    buf.append("a", "progress", "x" * 1000)
    with buf._lock:
        assert len(buf._events["a"]) == 0


def test_defaults_cannot_produce_an_oversized_event():
    """Why the case above is unreachable in practice."""
    from wisp.multi_agent.telemetry import DEFAULT_MAX_BYTES, MAX_EVENT_CHARS
    # Worst case: every character is 4 UTF-8 bytes.
    assert MAX_EVENT_CHARS * 4 < DEFAULT_MAX_BYTES


# ── Appends stay linear ──────────────────────────────────────────────

def test_many_appends_keep_accounting_consistent():
    """Guards the incremental total across heavy churn (the O(n^2) path)."""
    buf = SubagentTelemetryBuffer(max_events=50, max_bytes=100_000)
    for i in range(3000):
        buf.append("a", "progress", f"churn-{i}")
    with buf._lock:
        ring = list(buf._events["a"])
        expected = sum(len(e.text.encode("utf-8", "replace")) for e in ring)
    assert len(ring) == 50
    assert _ring_bytes(buf, "a") == expected


def test_snapshot_bytes_matches_ring_units():
    """snap.bytes and the ring bound must use the same unit."""
    buf = SubagentTelemetryBuffer(max_events=1000, max_bytes=10_000_000)
    buf.append("a", "progress", "héllo wörld")
    snap = buf.snapshot("a")
    assert snap is not None
    assert snap.bytes == len("héllo wörld".encode("utf-8"))
