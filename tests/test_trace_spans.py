# tests/test_trace_spans.py — Span model + store over tmp SQLite (M5 T1).
import pytest
from wisp.trace.span import SPAN_KINDS, Span, SpanStatus
from wisp.trace.store import SQLiteTraceStore
from wisp.infra.store import UnifiedStore


def _span(**overrides):
    base = {"trace_id": "t1", "span_id": "s1", "kind": "tool_call",
            "name": "read_file", "started_at": 1.0, "finished_at": 2.0,
            "attrs": {"path": "a.py"}, "status": SpanStatus.OK}
    base.update(overrides)
    return Span(**base)


def test_span_kinds_cover_contract():
    assert set(SPAN_KINDS) >= {"run", "turn", "model_request", "tool_call",
        "policy_decision", "approval", "retry", "subagent", "checkpoint", "artifact"}


def test_bad_kind_rejected():
    with pytest.raises(ValueError):
        _span(kind="teleport")


def test_span_round_trip():
    assert Span.from_dict(_span().to_dict()) == _span()


def test_store_append_query(tmp_path):
    store = SQLiteTraceStore(UnifiedStore(tmp_path / "w.db"))
    store.append(_span())
    store.append(_span(span_id="s2", parent_span_id="s1", kind="approval",
                       name="approve read", attrs={}))
    spans = store.query("t1")
    assert [s.span_id for s in spans] == ["s1", "s2"]
    assert store.query("nope") == []


def test_store_run_index(tmp_path):
    store = SQLiteTraceStore(UnifiedStore(tmp_path / "w.db"))
    store.append(_span(trace_id="t1", attrs={"run_id": "bg-1"}))
    store.append(_span(trace_id="t2", span_id="s9", attrs={"run_id": "bg-2"}))
    assert [s.span_id for s in store.query_run("bg-1")] == ["s1"]
