# tests/test_trace_export.py — evidence export + replay plan (M5 T2).
import json

from wisp.infra.store import UnifiedStore
from wisp.trace.export import export_evidence, replay_plan
from wisp.trace.span import Span, SpanStatus
from wisp.trace.store import SQLiteTraceStore


def _store(tmp_path):
    return SQLiteTraceStore(UnifiedStore(tmp_path / "w.db"))


def _seed(store):
    store.append(Span(trace_id="t1", span_id="s1", kind="turn", name="turn 1",
                      started_at=1.0, finished_at=5.0,
                      attrs={"run_id": "bg-1", "model": "ollama/qwen"}))
    store.append(Span(trace_id="t1", span_id="s2", kind="tool_call",
                      name="run_bash", parent_span_id="s1",
                      started_at=2.0, finished_at=3.0,
                      attrs={"run_id": "bg-1",
                             "command": "deploy --token ghp_abcdefghijklmnopqrstuvwxyZ1234567890"}))
    store.append(Span(trace_id="t1", span_id="s3", kind="approval",
                      name="approve write", parent_span_id="s1",
                      started_at=3.5, finished_at=4.0,
                      attrs={"run_id": "bg-1", "verdict": "approve"},
                      status=SpanStatus.OK))


def test_export_contains_no_secrets(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    ev = export_evidence(store, "t1")
    blob = json.dumps(ev)
    assert "ghp_" not in blob
    assert ev["trace_id"] == "t1" and ev["span_count"] == 3
    assert ev["redacted"] is True


def test_export_tool_sequence_ordered(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    ev = export_evidence(store, "t1")
    kinds = [s["kind"] for s in ev["spans"]]
    assert kinds == ["turn", "tool_call", "approval"]


def test_replay_plan_lists_tools_without_executing(tmp_path):
    store = _store(tmp_path)
    _seed(store)
    plan = replay_plan(store, "t1")
    assert plan == [{"seq": 0, "tool": "run_bash",
                     "args": {"command": "deploy --token [REDACTED:github-token]"}}]
    # dry-run marker: nothing here can execute
    assert all("tool" in step for step in plan)


def test_replay_empty_trace(tmp_path):
    assert replay_plan(_store(tmp_path), "missing") == []
    assert export_evidence(_store(tmp_path), "missing")["span_count"] == 0
