# tests/test_task_cli.py — task CLI goldens + JSON envelope (M6 T4).
import io
import json

from wisp.task.cli import main as task_main


def _run(args, monkeypatch, db):
    monkeypatch.setenv("WISP_DB", str(db))
    out = io.StringIO()
    code = task_main(args, out=out)
    return code, out.getvalue()


def test_start_list_inspect_cycle(tmp_path, monkeypatch):
    db = tmp_path / "w.db"
    code, text = _run(["start", "refactor auth"], monkeypatch, db)
    assert code == 0
    tid = text.strip()
    assert tid.startswith("task-")
    code, text = _run(["list"], monkeypatch, db)
    assert tid in text and "running" in text
    code, text = _run(["inspect", tid], monkeypatch, db)
    assert "refactor auth" in text and "queued -> running" in text


def test_json_envelope(tmp_path, monkeypatch):
    db = tmp_path / "w.db"
    code, text = _run(["start", "job", "--json"], monkeypatch, db)
    assert code == 0
    body = json.loads(text)
    assert body["ok"] is True and body["data"]["task_id"].startswith("task-")
    code, text = _run(["inspect", "task-nope", "--json"], monkeypatch, db)
    assert code == 1
    body = json.loads(text)
    assert body["ok"] is False and body["error"]


def test_pause_resume_cancel(tmp_path, monkeypatch):
    db = tmp_path / "w.db"
    _, text = _run(["start", "job"], monkeypatch, db)
    tid = text.strip()
    assert _run(["pause", tid], monkeypatch, db)[1].strip().endswith("paused")
    assert _run(["resume", tid], monkeypatch, db)[1].strip().endswith("running")
    assert _run(["cancel", tid], monkeypatch, db)[1].strip().endswith("cancelled")


def test_approve_plan_flow(tmp_path, monkeypatch):
    db = tmp_path / "w.db"
    _, text = _run(["start", "login page"], monkeypatch, db)
    tid = text.strip()
    plan = {"goal": "login", "files": ["a.py"],
            "actions": [{"tool": "read_file", "args": {"path": "a.py"}}]}
    pf = tmp_path / "plan.json"
    pf.write_text(json.dumps(plan), encoding="utf-8")
    code, text = _run(["approve-plan", tid, "--plan", str(pf),
                       "--scope", "all", "--approver", "dev"],
                      monkeypatch, db)
    assert code == 0 and "approved scope=all" in text
    code, text = _run(["review", tid], monkeypatch, db)
    assert code == 0 and "a.py" in text


def test_completion_scripts():
    from wisp.task.cli import _cmd_completion
    out = io.StringIO()
    assert _cmd_completion(["bash"], out) == 0
    assert "complete -F" in out.getvalue()
    out = io.StringIO()
    assert _cmd_completion(["zsh"], out) == 0
    assert "#compdef" in out.getvalue()
    assert _cmd_completion([], io.StringIO()) == 2
