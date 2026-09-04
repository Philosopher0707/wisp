"""Phase 3.2 (D10) tests — bounded per-session auxiliary maps.

Target: _approval_states / _steering_inbox / _touched_files / _turn_counts
stay capped like _session_locks (LRU, oldest-20% eviction). Cold sessions
(no recent turn) are evicted before hot ones. All four maps are
best-effort caches, so eviction is loss-safe by construction.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from wisp.core.runtime import AgentRuntime


def _runtime() -> AgentRuntime:
    return AgentRuntime(
        store=MagicMock(),
        security=MagicMock(),
        extensions=MagicMock(),
        telemetry=MagicMock(),
        core_factory=MagicMock(),
        config=MagicMock(),
    )


def test_approval_states_bounded():
    rt = _runtime()
    cap = rt._max_session_state
    for i in range(cap + 50):
        rt.approval_state(f"sess-{i}")
    assert len(rt._approval_states) <= cap
    # Newest entries survive eviction.
    assert "sess-%d" % (cap + 49) in rt._approval_states


def test_steering_inbox_bounded():
    rt = _runtime()
    cap = rt._max_session_state
    for i in range(cap + 50):
        rt.inject_steering(f"sess-{i}", f"note {i}")
    assert len(rt._steering_inbox) <= cap
    assert rt._steering_inbox["sess-%d" % (cap + 49)] == ["note %d" % (cap + 49)]


def test_touched_files_and_turn_counts_bounded():
    rt = _runtime()
    cap = rt._max_session_state
    for i in range(cap + 50):
        sid = f"sess-{i}"
        rt._note_touched_file(sid, {"name": "read_file", "arguments": {"path": f"/tmp/{i}.py"}})
        rt._record_session_memory(sid, {"workspace": ".", "messages": []}, f"prompt {i}")
    assert len(rt._touched_files) <= cap
    assert len(rt._turn_counts) <= cap


def test_eviction_prefers_cold_sessions():
    rt = _runtime()
    rt._max_session_state = 10
    # Hot session: recent turn access stamp.
    import time

    rt._session_access["hot"] = time.monotonic()
    rt.approval_state("hot")
    # Flood with cold sessions (no access stamp → epoch 0).
    for i in range(30):
        rt.approval_state(f"cold-{i}")
    assert "hot" in rt._approval_states
    assert len(rt._approval_states) <= 10
