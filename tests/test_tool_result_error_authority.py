"""Tool-result error classification: one predicate, not two.

Context (see PHASE_CANONICAL_AUTHORITY_MAP.md, D-4):

`wisp.transport.renderer.result_is_error` is the canonical predicate — its own
docstring calls it the "Authoritative tool-result failure check". The CLI
already delegates to it.

`wisp.benchmark.scoring._is_error_result` did NOT: it tested
`status == "error"`, which misses the structured denial statuses. Because
`TurnStats.tool_health` is derived from `tool_errors`, a benchmark turn whose
every tool call was denied scored `tool_health == 1.0` — a perfect score for a
turn that accomplished nothing. That is a scoring-integrity defect.

These tests pin the agreement and the consequence.
"""

from __future__ import annotations

import pytest

from wisp.benchmark.scoring import TurnStats, _is_error_result as bench_pred
from wisp.transport.renderer import result_is_error as canonical

# Every status the tool-result envelope can carry. "ok" is the only success.
STATUS_CASES = [
    ("ok", {"status": "ok"}, False),
    ("error", {"status": "error"}, True),
    ("POLICY_DENIED", {"status": "POLICY_DENIED"}, True),
    ("USER_DENIED", {"status": "USER_DENIED"}, True),
    ("APPROVAL_TIMEOUT", {"status": "APPROVAL_TIMEOUT"}, True),
    ("CANCELLED", {"status": "CANCELLED"}, True),
    ("SCHEMA_INVALID", {"status": "SCHEMA_INVALID"}, True),
]


@pytest.mark.parametrize("label,result,expected", STATUS_CASES)
def test_canonical_predicate_classifies_every_status(label, result, expected):
    assert canonical(result) is expected, f"canonical mis-classified {label}"


@pytest.mark.parametrize("label,result,expected", STATUS_CASES)
def test_benchmark_predicate_agrees_with_canonical(label, result, expected):
    """The regression: the benchmark predicate returned False for denials."""
    assert bench_pred(result) is canonical(result), (
        f"benchmark and canonical predicates disagree on {label}"
    )


def test_a_fully_denied_turn_does_not_report_perfect_tool_health():
    """The consequence the divergence actually caused."""
    stats = TurnStats(tool_calls=4)
    for _ in range(4):
        if bench_pred({"status": "POLICY_DENIED"}):
            stats.tool_errors += 1
    assert stats.tool_errors == 4
    assert stats.tool_health == 0.0, (
        "a turn whose every tool call was denied must not report perfect "
        f"tool health (got {stats.tool_health})"
    )


def test_a_healthy_turn_still_reports_perfect_tool_health():
    """The fix must not make healthy turns look bad."""
    stats = TurnStats(tool_calls=3)
    for _ in range(3):
        if bench_pred({"status": "ok"}):
            stats.tool_errors += 1
    assert stats.tool_errors == 0
    assert stats.tool_health == 1.0


def test_string_envelopes_are_parsed_by_both():
    """Slow tools surface as JSON strings; both predicates must see through."""
    import json
    payload = json.dumps({"status": "POLICY_DENIED"})
    assert canonical(payload) is True
    assert bench_pred(payload) is True


def test_legacy_text_markers_are_recognised_by_both():
    for text in ("Error: boom", "[Error] boom"):
        assert canonical(text) is True
        assert bench_pred(text) is True
