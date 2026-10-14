"""The baseline: what `wisp` (the CLI and the REPL) does today, each claim proved by running it.

M0 (2026-10-14) cut Wisp to the CLI and the REPL. Each test here is one claim in
`docs/baseline/2026-10-14.md`, and drives the real command (`python -m wisp`, under a pty for the
REPL) against a scripted local model (`_model.py`): no fake core, no injected executor. A claim
whose test fails is not part of the baseline.
"""

from __future__ import annotations

import json
import os
import pty
import select
import sqlite3
import subprocess
import sys
import time

import pytest

from tests.baseline._model import WISP, ScriptedModel, wisp_env

TIMEOUT = 90

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"


@pytest.fixture()
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".config" / "wisp").mkdir(parents=True)
    return h


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    (w / "calc.py").write_text(BUGGY)
    (w / "test_calc.py").write_text(TEST)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=w, check=True)
    return w


def _print(prompt, home, ws, model, **env):
    proc = subprocess.run([*WISP, "--print", prompt, "-w", str(ws)], cwd=ws, env=wisp_env(home, model, **env),
                          capture_output=True, text=True, timeout=TIMEOUT)
    try:
        return proc, json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise AssertionError(f"--print did not return JSON (rc={proc.returncode}):\n{proc.stdout}\n{proc.stderr}")


def _tool_results(model, name):
    return [str(m.get("content")) for r in model.of(name) for m in r["messages"] if m.get("role") == "tool"]


# ── B1. The command surface is the CLI and the REPL ────────────────────────────────────────────

def test_b1_help_lists_the_kept_commands_and_none_of_the_removed(home, ws):
    model = ScriptedModel({})
    try:
        out = subprocess.run([*WISP, "--help"], cwd=ws, env=wisp_env(home, model), capture_output=True, text=True,
                             timeout=TIMEOUT).stdout
    finally:
        model.close()
    commands = {line.split()[0] for line in out.splitlines() if line.startswith("  ") and line.split()}
    assert {"run", "repl", "session", "swarm", "graph"} <= commands
    assert not {"server", "tui", "acp"} & commands


# ── B2. A coding task, end to end, through the real command ───────────────────────────────────

def test_b2_a_coding_task_reads_fixes_verifies_and_the_fix_is_real(home, ws):
    model = ScriptedModel({"fix": [
        ("tool", "read_file", {"path": "calc.py"}),
        ("tool", "edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"}),
        ("tool", "run_tests", {}),
        ("text", "Fixed add() in calc.py; the test passes."),
    ]})
    try:
        proc, result = _print("BASELINE{fix} make the test pass", home, ws, model)
    finally:
        model.close()
    assert result["ok"] is True, result
    assert (ws / "calc.py").read_text() == FIXED, "the edit did not reach the file"
    results = _tool_results(model, "fix")
    assert any("a - b" in r for r in results[:1]), "the model was not shown the file it read"
    assert "UNVERIFIED" not in result["content"], "a verified turn was called unverified"
    independent = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=ws,
                                 capture_output=True, text=True, timeout=TIMEOUT)
    assert independent.returncode == 0, "the fix does not make the test pass when run independently"


# ── B3. A change that was never verified is said to be unverified ─────────────────────────────

def test_b3_an_unverified_change_is_named_by_the_harness_not_the_model(home, ws):
    model = ScriptedModel({"claim": [
        ("tool", "edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"}),
        ("text", "Done. All tests pass."),
    ]})
    try:
        _, result = _print("BASELINE{claim} fix it", home, ws, model)
    finally:
        model.close()
    # Two judges cover this case (core/honest_finish's "UNVERIFIED" line and the reasoning core's R1); on this
    # path R1 writes the note. One finish judge is milestone M1.
    assert "Harness note: the completion claim above is not backed by any verification" in result["content"], (
        result["content"])


@pytest.mark.xfail(strict=True, reason="known defect (baseline 2026-10-14): every withheld draft is kept in the answer")
def test_b3_known_defect_a_withheld_draft_appears_once(home, ws):
    model = ScriptedModel({"claim": [
        ("tool", "edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"}),
        ("text", "Done. All tests pass."),
    ]})
    try:
        _, result = _print("BASELINE{claim} fix it", home, ws, model)
    finally:
        model.close()
    assert result["content"].count("Done. All tests pass.") == 1, result["content"]


# ── B4. Irreversible commands are refused before they run, even with full permissions ─────────

def test_b4_a_recursive_delete_is_refused_and_the_files_survive(home, ws):
    keep = ws / "important"
    keep.mkdir()
    (keep / "data.txt").write_text("keep me\n")
    model = ScriptedModel({"rm": [("tool", "run_bash", {"command": "rm -rf important"}), ("text", "ok")]})
    try:
        _, result = _print("BASELINE{rm} clean up", home, ws, model)
    finally:
        model.close()
    assert (keep / "data.txt").read_text() == "keep me\n", "the delete ran"
    told = " ".join(_tool_results(model, "rm")).lower()
    assert "refus" in told or "denied" in told or "blocked" in told, told


# ── B5. The REPL asks before a write, and the answer decides ───────────────────────────────────

def _pty(home, ws, model, **env):
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ws)
        os.execvpe(sys.executable, [*WISP, "repl", "-w", str(ws)], wisp_env(home, model, **env))
    return pid, fd


def _read_until(fd, marker: bytes, timeout=TIMEOUT) -> bytes:
    out, end = b"", time.time() + timeout
    while time.time() < end and marker not in out:
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out


def _close(pid, fd):
    try:
        os.write(fd, b"exit\n")
        _read_until(fd, b"\x00", 5)
    except OSError:
        pass
    try:
        os.kill(pid, 9)
    except OSError:
        pass
    os.waitpid(pid, 0)
    os.close(fd)


def _repl_write(home, ws, answer: bytes) -> str:
    """Ask the REPL (auto_edit) for a write, answer the prompt, return the transcript after the answer."""
    model = ScriptedModel({"write": [("tool", "write_file", {"path": "new.txt", "content": "from the model\n"}),
                                     ("text", "BASELINE-DONE")]})
    pid, fd = _pty(home, ws, model, WISP_PERMISSION_MODE="auto_edit")
    try:
        _read_until(fd, b"wisp", 60)
        os.write(fd, b"BASELINE{write} create new.txt\n")
        asked = _read_until(fd, b"[y] yes")
        assert b"[y] yes" in asked, asked.decode(errors="replace")[-2000:]
        os.write(fd, answer + b"\n")
        after = _read_until(fd, "wisp ❯".encode(), 60)
    finally:
        _close(pid, fd)
        model.close()
    return after.decode(errors="replace")


@pytest.mark.parametrize("answer,written", [(b"n", False), (b"y", True)])
def test_b5_the_repl_asks_before_writing_and_the_answer_decides(home, ws, answer, written):
    _repl_write(home, ws, answer)
    assert (ws / "new.txt").exists() is written


@pytest.mark.xfail(strict=True, reason="known defect (baseline 2026-10-14): a refused write is reported as a code change")
def test_b5_known_defect_a_refused_write_is_not_called_a_change(home, ws):
    after = _repl_write(home, ws, b"n")
    assert not (ws / "new.txt").exists()
    assert "changed code" not in after and "Files: new.txt" not in after, after[-1500:]


# ── B6. A session is saved and continued ──────────────────────────────────────────────────────

def test_b6_a_named_session_is_continued_with_its_history(home, ws):
    model = ScriptedModel({"one": [("text", "noted: the word is heron")], "two": [("text", "heron")]})
    try:
        _, first = _print("BASELINE{one} remember the word heron", home, ws, model)
        sid = first["session_id"]
        proc = subprocess.run([*WISP, "-S", sid, "--print", "BASELINE{two} what was the word?", "-w", str(ws)], cwd=ws,
                              env=wisp_env(home, model), capture_output=True, text=True, timeout=TIMEOUT)
    finally:
        model.close()
    assert model.of("two"), proc.stderr[-500:]
    assert any("remember the word heron" in str(m.get("content")) for m in model.of("two")[-1]["messages"])


@pytest.mark.xfail(strict=True, reason="known defect (baseline 2026-10-14): every --print run in a workspace "
                                       "joins one stored session, 'headless'")
def test_b6_known_defect_unrelated_print_runs_do_not_share_history(home, ws):
    model = ScriptedModel({"one": [("text", "noted")], "two": [("text", "ok")]})
    try:
        _print("BASELINE{one} my secret word is heron", home, ws, model)
        _print("BASELINE{two} an unrelated question", home, ws, model)
    finally:
        model.close()
    seen = " ".join(str(m.get("content")) for m in model.of("two")[0]["messages"])
    assert "heron" not in seen, "the second one-shot run was sent the first run's prompt"


@pytest.mark.xfail(strict=True, reason="known defect (baseline 2026-10-14): --print reports iterations 0")
def test_b6_known_defect_print_reports_the_iterations_it_ran(home, ws):
    model = ScriptedModel({"it": [("tool", "read_file", {"path": "calc.py"}), ("text", "read it")]})
    try:
        _, result = _print("BASELINE{it} read calc.py", home, ws, model)
    finally:
        model.close()
    assert len(model.of("it")) == 2
    assert result["iterations"] >= 1, result


# ── B7. What the model is offered by default, and what that leaves out ────────────────────────

def test_b7_the_default_parent_is_offered_the_eleven_core_tools_and_no_spawn(home, ws):
    from wisp.tools.profile import CORE_TOOLS

    model = ScriptedModel({"look": [("text", "ok")]})
    try:
        _print("BASELINE{look} hi", home, ws, model)
    finally:
        model.close()
    offered = set(model.of("look")[0]["tools"])
    assert set(CORE_TOOLS) <= offered
    assert "spawn" not in offered and "fanout" not in offered, (
        "the default parent can now delegate; update docs/baseline/2026-10-14.md B7")


def test_b8_with_the_full_profile_a_child_does_the_work_and_the_parent_gets_evidence(home, ws):
    model = ScriptedModel({
        "boss": [("tool", "spawn", {"task": "BASELINE{child} fix calc.py", "role": "coder"}), ("text", "delegated")],
        "child": [("tool", "edit_file", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b"}),
                  ("text", "I fixed calc.py")],
    })
    try:
        _, result = _print("BASELINE{boss} get calc fixed", home, ws, model, WISP_TOOL_PROFILE="full")
    finally:
        model.close()
    assert model.of("child"), "no child request reached the model"
    assert (ws / "calc.py").read_text() == FIXED
    handback = next(r for r in _tool_results(model, "boss") if '"evidence"' in r)
    data = json.loads(handback)["data"]
    assert data["files"] == ["calc.py"] and {"kind": "file", "ref": "calc.py"} in data["evidence"], data
    db = ws / ".wisp" / "wisp.db"
    rows = sqlite3.connect(db).execute("select status from agent_records").fetchall() if db.exists() else []
    assert rows, "the child has no agent record"
