# tests/test_trace_cli.py — trace/replay/audit/task-evidence CLI (M5 T5).
import io
import json

from wisp.infra.store import UnifiedStore
from wisp.trace.cli import main as trace_main
from wisp.trace.span import Span
from wisp.trace.store import SQLiteTraceStore


def _seed_db(path):
    store = SQLiteTraceStore(UnifiedStore(path))
    store.append(Span(trace_id="t1", span_id="s1", kind="turn", name="turn 1",
                      started_at=1.0, finished_at=5.0,
                      attrs={"run_id": "bg-1", "model": "m1"}))
    store.append(Span(trace_id="t1", span_id="s2", kind="tool_call",
                      name="read_file", parent_span_id="s1",
                      started_at=2.0, finished_at=3.0,
                      attrs={"run_id": "bg-1", "path": "a.py"}))
    return store


def _run(args, monkeypatch, db):
    monkeypatch.setenv("WISP_DB", str(db))
    out = io.StringIO()
    code = trace_main(args, out=out)
    return code, out.getvalue()


def test_trace_shows_span_tree(tmp_path, monkeypatch):
    db = tmp_path / "w.db"
    _seed_db(db)
    code, text = _run(["trace", "t1"], monkeypatch, db)
    assert code == 0
    assert "read_file" in text and "turn" in text


def test_trace_resolves_run_id(tmp_path, monkeypatch):
    db = tmp_path / "w.db"
    _seed_db(db)
    code, text = _run(["trace", "bg-1"], monkeypatch, db)
    assert code == 0 and "read_file" in text


def test_trace_missing(tmp_path, monkeypatch):
    db = tmp_path / "w.db"
    _seed_db(db)
    code, text = _run(["trace", "nope"], monkeypatch, db)
    assert code == 1 and "no spans" in text.lower()


def test_replay_dry_run(tmp_path, monkeypatch):
    db = tmp_path / "w.db"
    _seed_db(db)
    code, text = _run(["replay", "--dry-run", "t1"], monkeypatch, db)
    assert code == 0
    assert "read_file" in text and "dry-run" in text.lower()


def test_replay_refuses_live():
    out = io.StringIO()
    assert trace_main(["replay", "t1"], out=out) == 2
    assert "dry-run" in out.getvalue().lower()


def test_export_evidence_json(tmp_path, monkeypatch, capsys):
    from wisp.trace.cli import task_main
    db = tmp_path / "w.db"
    _seed_db(db)
    monkeypatch.setenv("WISP_DB", str(db))
    out = io.StringIO()
    code = task_main(["export-evidence", "t1", "--out", str(tmp_path / "ev.json")],
                     out=out)
    assert code == 0
    ev = json.loads((tmp_path / "ev.json").read_text())
    assert ev["trace_id"] == "t1" and ev["redacted"] is True


def test_audit_verify(tmp_path, monkeypatch):
    from wisp.infra.audit import AuditTrail
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("WISP_AUDIT_LOG", str(log))
    trail = AuditTrail(log)
    trail.record("tool.read_file", actor="test")
    out = io.StringIO()
    assert trace_main(["audit-verify"], out=out) == 0
    assert "intact" in out.getvalue().lower()
