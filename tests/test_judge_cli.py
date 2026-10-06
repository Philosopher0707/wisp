"""`wisp judge` is wired like the other subcommands and works end to end against the mock provider."""
import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

from wisp.judge import cli


def test_judge_is_a_registered_subcommand_with_help():
    from wisp.__main__ import _SUBCOMMAND_HELP, _SUBCOMMAND_NAMES, print_subcommand_help
    assert "judge" in _SUBCOMMAND_NAMES and "judge" in _SUBCOMMAND_HELP
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert print_subcommand_help("judge") is True
    assert "SOLVED" in buf.getvalue()


def test_list_marks_held_out_tasks():
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert cli.run_judge(["list"]) == 0
    out = buf.getvalue()
    assert "off_by_one" in out and "(held-out)" in out


def test_judge_existing_run_reports_solved(tmp_path):
    from wisp.judge.core import load_tasks
    t = load_tasks()["off_by_one"]
    before, after = tmp_path / "before", tmp_path / "after"
    for d in (before, after):
        d.mkdir()
        for rel, body in t.files.items():
            (d / rel).write_text(body)
    (after / "totals.py").write_text("def total(values):\n    return sum(values)\n")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.run_judge(["judge", "off_by_one", str(before), str(after)])
    assert rc == 0 and json.loads(buf.getvalue())["verdict"] == "SOLVED"


def test_unknown_task_is_an_error_not_a_crash(tmp_path):
    assert cli.run_judge(["judge", "nope", str(tmp_path), str(tmp_path)]) == 2
    assert cli.run_judge(["run", "--tasks", "nope"]) == 2


def test_missing_key_fails_early_without_running_anything(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DEFINITELY_UNSET_KEY", raising=False)
    assert cli.run_judge(["run", "--key-from", "DEFINITELY_UNSET_KEY"]) == 2


def test_real_wisp_with_mock_provider_is_judged_noop(tmp_path):
    """End to end through the real CLI: the mock edits nothing, so the verdict is NO-OP and the exit is non-zero.

    Wiring only: what the mock claims is the mock's business, so claim honesty is not asserted here."""
    out = tmp_path / "out.json"
    # Run THIS checkout's wisp: an older installed wisp has no `judge` subcommand and would send
    # "judge run ..." to the model as a prompt.
    repo = str(Path(__file__).resolve().parents[1])
    env = {**os.environ, "PYTHONPATH": repo + os.pathsep + os.environ.get("PYTHONPATH", "")}
    p = subprocess.run(
        [sys.executable, "-m", "wisp", "judge", "run", "--tasks", "off_by_one", "--provider", "mock",
         "--model", "mock", "--timeout", "120", "--json-out", str(out)],
        capture_output=True, text=True, timeout=240, cwd=tmp_path, env=env)
    assert p.returncode == 1, p.stdout + p.stderr
    row = json.loads(out.read_text())[0]
    assert row["verdict"] == "NO-OP" and row["changed"] == []
    assert row["claim"] is not None  # wisp's own JSON was parsed
