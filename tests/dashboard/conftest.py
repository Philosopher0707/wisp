from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

SECRET_PROMPT = "PRIVATE-PROMPT-TEXT-do-not-leak"
SECRET_KEY = "sk-or-v1-" + "ab12cd34" * 8


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def make_workspace(root: Path, name="proj", model="vendor/model-a", tools=None, with_db=True) -> Path:
    ws = root / name
    (ws / ".wisp").mkdir(parents=True)
    (ws / ".agent").mkdir()
    rows = tools if tools is not None else [("run_bash", "ok", "2026-10-01T10:00:00"), ("run_bash", "error", "2026-10-01T11:00:00"),
                                           ("edit_file", "ok", "2026-10-02T09:00:00"), ("remember", "error", "2026-10-02T09:30:00")]
    with (ws / ".wisp" / "audit.jsonl").open("w") as fh:
        for tool, status, ts in rows:
            fh.write(json.dumps({"timestamp": ts, "tool": tool, "result_status": status, "decision": "approved", "arg_summary": SECRET_PROMPT}) + "\n")
        fh.write("{torn line\n")
    (ws / ".agent" / "runtime.log").write_text("Transient status 429 on attempt 1/3\nTransient status 429 on attempt 2/3\noutside workspace /x\n" + SECRET_PROMPT + "\n")
    if with_db:
        con = sqlite3.connect(ws / ".wisp" / "wisp.db")
        con.executescript("""
            create table sessions (id text, model text, workspace text, title text, messages text, compaction_history text, created_at text, updated_at text, msg_count integer);
            create table session_events (id integer primary key, session_id text, sequence_num integer, event_type text, payload text, created_at text);
            create table background_runs (id text, prompt text, status text);
            create table graph_runs (run_id text, status text);
        """)
        con.execute("insert into sessions values ('s1', ?, ?, ?, ?, '', '2026-10-01T10:00:00', '2026-10-02T12:00:00', 7)", (model, str(ws), SECRET_PROMPT, SECRET_PROMPT))
        con.execute("insert into sessions values ('s2', ?, ?, 't', '[]', '', '2026-10-03T10:00:00', '2026-10-03T10:30:00', 3)", (model, str(ws)))
        for et in ["user_message", "user_message", "done", "tool_call", "tool_result"]:
            con.execute("insert into session_events (session_id, sequence_num, event_type, payload, created_at) values ('s1', 1, ?, ?, 'x')", (et, SECRET_PROMPT))
        con.executemany("insert into background_runs values (?, ?, ?)", [("a", SECRET_PROMPT, "succeeded"), ("b", SECRET_PROMPT, "failed")])
        con.execute("insert into graph_runs values ('g1', 'running')")
        con.commit()
        con.close()
    return ws
