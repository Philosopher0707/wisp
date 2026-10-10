"""Keep-or-revert: an attempt that did not move the objective is undone, and what it did is kept on the side.

The rule is Karpathy's `autoresearch/program.md`: after each experiment, if the metric improved keep the change, otherwise `git reset` to where the attempt
started. Here the metric is the harness's own measurement (`core/progress.py`), the "reset" is a bounded in-memory snapshot taken before the attempt, and nothing is
destroyed silently: the attempt's version of every file it changed is written next to the journal before the workspace is put back.

These tests drive the real `ConvergenceController` with a real `CommandProbe` (a real subprocess); only the "model" is a script that edits files.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from wisp.core.convergence import (
    CommandProbe,
    CommandSpec,
    ConvergenceController,
    Objective,
    TurnObservation,
    WorkspaceSnapshot,
    criteria_for,
)
from wisp.core.goal import GoalState

# state.txt holds the number of failing checks; the command prints it the way pytest does and exits 1 while it is not zero.
CHECK = (
    "import sys, pathlib\n"
    "n = int(pathlib.Path('state.txt').read_text())\n"
    "print(f'{n} failed' if n else '3 passed')\n"
    "sys.exit(1 if n else 0)\n"
)


class Script:
    """A `run_turn` whose behaviour is a list of callables; running out of script fails loudly (a loop must be bounded by its budget, not by the script)."""

    def __init__(self, ws: Path, steps):
        self.ws, self.steps, self.calls = ws, list(steps), []

    async def __call__(self, request):
        self.calls.append(request)
        assert self.steps, f"attempt {request.attempt + 1} had no script"
        returned = self.steps.pop(0)(self.ws)
        return returned if isinstance(returned, TurnObservation) else TurnObservation(turn_succeeded=True, terminal_outcome="succeeded")


def state(ws: Path, n: int) -> None:
    (ws / "state.txt").write_text(str(n))


@pytest.fixture
def ws(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "check.py").write_text(CHECK)
    state(work, 3)
    (work / "notes.py").write_text("ORIGINAL = 1\n")
    (work / "old.py").write_text("KEEP_ME = 1\n")
    return work


def build(ws: Path, steps, *, revert=True, journal=True, attempts=3, resume=False, factory=WorkspaceSnapshot):
    specs = (CommandSpec(criteria_id="verify:cmd0", argv=(sys.executable, "check.py")),)
    probe = CommandProbe(specs)
    path = ws.parent / "journal.jsonl" if journal else None
    turn = Script(ws, steps)
    controller = ConvergenceController(
        run_turn=turn, probe=probe, max_attempts=attempts, journal_path=path, baseline=probe.measure(str(ws)),
        revert_no_progress=revert, snapshot_factory=factory)
    objective = Objective(goal="fix the failing checks", workspace=str(ws), criteria=criteria_for(specs))
    return controller, turn, objective, path


def run(controller, objective, resume=False):
    return asyncio.run(controller.converge(objective, resume=resume))


def churn(ws: Path) -> None:
    """Activity that moves nothing the objective measures: edit one file, add one, delete one."""
    (ws / "notes.py").write_text("ORIGINAL = 2  # changed\n")
    (ws / "junk.py").write_text("x = 1\n")
    (ws / "old.py").unlink()


def test_an_attempt_that_moved_nothing_is_undone_file_by_file(ws):
    controller, _turn, objective, _path = build(ws, [churn], attempts=1)
    result = run(controller, objective)
    assert (ws / "notes.py").read_text() == "ORIGINAL = 1\n"  # edited file restored
    assert not (ws / "junk.py").exists()  # created file removed
    assert (ws / "old.py").read_text() == "KEEP_ME = 1\n"  # deleted file restored
    record = result.attempts[0]
    assert record.progress == "no_progress"
    assert record.reverted == ("junk.py", "notes.py", "old.py")


def test_the_attempts_own_version_is_kept_on_the_side_before_the_workspace_is_restored(ws):
    controller, _turn, objective, path = build(ws, [churn], attempts=1)
    run(controller, objective)
    kept = path.with_suffix(".discarded") / "attempt-1"
    assert (kept / "notes.py").read_text() == "ORIGINAL = 2  # changed\n"
    assert (kept / "junk.py").read_text() == "x = 1\n"
    assert not (kept / "old.py").exists()  # the attempt deleted it, so there is no version of it to keep


def test_the_journal_says_what_was_reverted(ws):
    controller, _turn, objective, path = build(ws, [churn], attempts=1)
    run(controller, objective)
    attempt = [json.loads(line) for line in path.read_text().splitlines() if '"kind": "attempt"' in line][0]
    assert attempt["reverted"] == ["junk.py", "notes.py", "old.py"]


def test_an_attempt_that_moves_the_measurement_is_kept(ws):
    def better(w):
        state(w, 2)
        (w / "notes.py").write_text("ORIGINAL = 3  # a real step\n")

    controller, _turn, objective, _path = build(ws, [better], attempts=1)
    result = run(controller, objective)
    assert result.attempts[0].progress == "meaningful_progress"
    assert result.attempts[0].reverted == ()
    assert (ws / "notes.py").read_text() == "ORIGINAL = 3  # a real step\n"
    assert (ws / "state.txt").read_text() == "2"


def test_a_regression_is_undone_not_just_ignored(ws):
    def worse(w):
        state(w, 5)

    controller, _turn, objective, _path = build(ws, [worse], attempts=1)
    result = run(controller, objective)
    assert result.attempts[0].progress == "no_progress"
    assert (ws / "state.txt").read_text() == "3"  # back to the state the attempt started from, not left at 5


def test_the_next_attempt_is_measured_against_the_state_the_revert_restored(ws):
    """Attempt 1 makes it 5 (reverted to 3); attempt 2 makes it 4. 4 is better than 5 but worse than the 3 the workspace is actually at, so it is not progress."""
    controller, _turn, objective, _path = build(ws, [lambda w: state(w, 5), lambda w: state(w, 4)], attempts=2)
    result = run(controller, objective)
    assert [a.progress for a in result.attempts] == ["no_progress", "no_progress"]
    assert (ws / "state.txt").read_text() == "3"


def test_a_good_attempt_after_a_reverted_one_converges(ws):
    controller, _turn, objective, _path = build(ws, [lambda w: state(w, 5), lambda w: state(w, 0)], attempts=2)
    result = run(controller, objective)
    assert result.goal_state is GoalState.GOAL_MET
    assert [bool(a.reverted) for a in result.attempts] == [True, False]


def test_the_next_attempt_is_told_that_the_last_one_was_reverted_and_what_state_it_starts_from(ws):
    controller, turn, objective, _path = build(ws, [lambda w: state(w, 5), lambda w: state(w, 0)], attempts=2)
    run(controller, objective)
    shown = "\n".join(turn.calls[1].evidence)
    assert "reverted" in shown and "state.txt" in shown
    assert "3 failed" in shown or "verify:cmd0" in shown  # the measurement of the state it will start from, not of the reverted one
    assert "5 failed" not in shown


def test_nothing_is_reverted_unless_asked(ws):
    controller, _turn, objective, _path = build(ws, [churn], attempts=1, revert=False)
    result = run(controller, objective)
    assert result.attempts[0].reverted == ()
    assert (ws / "junk.py").exists()


def test_without_a_journal_there_is_nowhere_to_keep_the_discarded_work_so_nothing_is_reverted(ws):
    controller, _turn, objective, _path = build(ws, [churn], attempts=1, journal=False)
    result = run(controller, objective)
    assert result.attempts[0].reverted == ()
    assert "no journal" in result.attempts[0].revert_note
    assert (ws / "junk.py").exists()


def test_a_workspace_too_big_to_snapshot_is_left_alone_and_the_record_says_why(ws):
    controller, _turn, objective, _path = build(ws, [churn], attempts=1, factory=lambda: WorkspaceSnapshot(max_files=1))
    result = run(controller, objective)
    assert result.attempts[0].reverted == ()
    assert "snapshot refused" in result.attempts[0].revert_note
    assert (ws / "junk.py").exists()


def test_with_no_earlier_measurement_nothing_is_reverted(ws):
    """Fail closed: 'I cannot tell whether it helped' is not 'it did not help'."""
    specs = (CommandSpec(criteria_id="verify:cmd0", argv=(sys.executable, "check.py")),)
    turn = Script(ws, [churn])
    controller = ConvergenceController(
        run_turn=turn, probe=CommandProbe(specs), max_attempts=1, journal_path=ws.parent / "j.jsonl", baseline=None, revert_no_progress=True)
    result = run(controller, Objective(goal="g", workspace=str(ws), criteria=criteria_for(specs)))
    assert result.attempts[0].progress == "progress_undeterminable"
    assert result.attempts[0].reverted == ()
    assert (ws / "junk.py").exists()


def test_an_attempt_that_edits_the_check_itself_is_undone(ws):
    """Karpathy's prepare.py is read-only. A measurement whose inputs moved is not evidence, so the edit is reverted instead of being kept as the new normal."""
    specs = (CommandSpec(criteria_id="verify:cmd0", argv=(sys.executable, "check.py"), inputs=("check.py",)),)
    probe = CommandProbe(specs)
    baseline = probe.measure(str(ws))
    turn = Script(ws, [lambda w: (w / "check.py").write_text("import sys\nprint('3 passed')\nsys.exit(0)\n")])
    controller = ConvergenceController(
        run_turn=turn, probe=probe, max_attempts=1, journal_path=ws.parent / "j.jsonl", baseline=baseline, revert_no_progress=True)
    result = run(controller, Objective(goal="g", workspace=str(ws), criteria=criteria_for(specs, baseline=baseline)))
    assert not result.converged
    assert (ws / "check.py").read_text() == CHECK
    assert result.attempts[0].reverted == ("check.py",)


def test_a_resume_after_a_reverted_attempt_compares_against_the_restored_state(ws):
    controller, _turn, objective, path = build(ws, [lambda w: state(w, 5)], attempts=1)
    run(controller, objective)
    assert (ws / "state.txt").read_text() == "3"
    controller2, _turn2, objective2, _ = build(ws, [lambda w: state(w, 4)], attempts=2)
    controller2._journal = path
    result = run(controller2, objective2, resume=True)
    assert len(result.attempts) == 2
    assert result.attempts[1].progress == "no_progress"  # 4 is better than the reverted 5, worse than the 3 it is really at
    assert (ws / "state.txt").read_text() == "3"


def test_the_snapshot_diff_and_revert_ignore_what_the_snapshot_never_looks_at(ws):
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref: main\n")
    snap = WorkspaceSnapshot()
    assert snap.capture(str(ws))
    (ws / ".git" / "HEAD").write_text("ref: other\n")
    (ws / "__pycache__").mkdir()
    (ws / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert snap.changes(str(ws)) == ((), (), ())


def test_a_symlink_is_never_followed_by_the_snapshot_so_a_revert_cannot_write_outside_the_workspace(ws, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("precious\n")
    (ws / "link.txt").symlink_to(outside)
    controller, _turn, objective, _path = build(ws, [lambda w: outside.write_text("changed by the attempt\n")], attempts=1)
    result = run(controller, objective)
    assert result.attempts[0].reverted == ()  # nothing inside the workspace changed
    assert outside.read_text() == "changed by the attempt\n"  # and the revert did not write through the link


def test_a_journal_kept_inside_the_workspace_is_not_mistaken_for_the_attempts_work(ws):
    records = ws / "records"
    records.mkdir()
    specs = (CommandSpec(criteria_id="verify:cmd0", argv=(sys.executable, "check.py")),)
    probe = CommandProbe(specs)
    turn = Script(ws, [churn, lambda w: state(w, 4)])
    controller = ConvergenceController(
        run_turn=turn, probe=probe, max_attempts=2, journal_path=records / "j.jsonl", baseline=probe.measure(str(ws)), revert_no_progress=True)
    result = run(controller, Objective(goal="g", workspace=str(ws), criteria=criteria_for(specs)))
    assert result.attempts[1].reverted == ("state.txt",)  # not also the first attempt's discarded copies
    assert (records / "j.discarded" / "attempt-1" / "junk.py").exists()


def test_an_attempt_that_reaches_the_goal_is_never_reverted_even_when_the_measurement_did_not_move(ws):
    """A green workspace that stays green has 'no progress', but the objective is met: the work is the proof, so it stays."""
    state(ws, 0)
    controller, _turn, objective, _path = build(ws, [churn], attempts=1)
    result = run(controller, objective)
    assert result.goal_state is GoalState.GOAL_MET
    assert result.attempts[0].progress == "no_progress"
    assert result.attempts[0].reverted == ()
    assert (ws / "junk.py").exists()


def test_after_an_authorization_event_the_workspace_is_left_as_the_attempt_made_it_for_the_human_to_see(ws):
    from wisp.core.events import DENIAL_POLICY_DENIED

    def denied(w):
        churn(w)
        return TurnObservation(turn_succeeded=False, terminal_outcome="failed", failure_message=DENIAL_POLICY_DENIED, failure_recoverable=False)

    controller, _turn, objective, _path = build(ws, [denied], attempts=2)
    result = run(controller, objective)
    assert result.goal_state is GoalState.ESCALATED_TO_HUMAN
    assert result.attempts[0].reverted == ()
    assert (ws / "junk.py").exists()


def test_the_snapshot_never_walks_into_the_directories_it_ignores(ws, monkeypatch):
    """The snapshot runs twice per attempt, on every attempt. Filtering `node_modules` out of the result after walking all of it is not skipping it."""
    import os

    (ws / "node_modules" / "pkg").mkdir(parents=True)
    (ws / "node_modules" / "pkg" / "index.js").write_text("x")
    (ws / ".git").mkdir()
    (ws / ".git" / "HEAD").write_text("ref: main\n")
    scanned: list[str] = []
    real = os.scandir

    def spy(path="."):
        scanned.append(str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", spy)
    snap = WorkspaceSnapshot()
    assert snap.capture(str(ws))
    assert snap.changes(str(ws)) == ((), (), ())
    assert scanned and not [p for p in scanned if {"node_modules", ".git"} & set(Path(p).parts)]


def test_a_workspace_with_more_files_than_the_bound_is_refused_without_reading_them_all(ws):
    for i in range(30):
        (ws / f"f{i}.txt").write_text("x")
    snap = WorkspaceSnapshot(max_files=5)
    assert not snap.capture(str(ws)) and "more than 5 files" in snap.refused


def test_wisps_own_state_directories_are_never_part_of_an_attempt(ws):
    """`.wisp/` holds the session store and `.agent/logs/` the run_bash logs; the running agent writes both while an attempt runs, so a revert must not delete or rewrite them."""

    def attempt(w):
        (w / ".agent" / "logs").mkdir(parents=True)
        (w / ".agent" / "logs" / "run.log").write_text("written by the agent itself\n")
        (w / ".wisp").mkdir()
        (w / ".wisp" / "wisp.db").write_text("live store")
        churn(w)

    controller, _turn, objective, _path = build(ws, [attempt], attempts=1)
    result = run(controller, objective)
    assert result.attempts[0].reverted == ("junk.py", "notes.py", "old.py")
    assert (ws / ".agent" / "logs" / "run.log").exists() and (ws / ".wisp" / "wisp.db").read_text() == "live store"


def test_a_deleted_executable_comes_back_executable(ws):
    import os
    import stat

    script = ws / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    controller, _turn, objective, _path = build(ws, [lambda w: (w / "run.sh").unlink()], attempts=1)
    result = run(controller, objective)
    assert result.attempts[0].reverted == ("run.sh",)
    assert script.read_text() == "#!/bin/sh\necho hi\n"
    assert stat.S_IMODE(os.stat(script).st_mode) == 0o755


def test_a_permission_change_alone_is_the_attempts_change_and_is_undone(ws):
    import os
    import stat

    target = ws / "notes.py"
    target.chmod(0o644)
    controller, _turn, objective, _path = build(ws, [lambda w: (w / "notes.py").chmod(0o755)], attempts=1)
    result = run(controller, objective)
    assert result.attempts[0].reverted == ("notes.py",)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644


def test_the_rollback_rungs_restore_keeps_the_executable_bit_too(ws):
    import os
    import stat

    script = ws / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    snap = WorkspaceSnapshot()
    assert snap.capture(str(ws))
    script.unlink()
    assert snap.restore(str(ws))
    assert stat.S_IMODE(os.stat(script).st_mode) == 0o755


@pytest.mark.skipif(hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0, reason="root can write a read-only file")
def test_a_revert_that_fails_part_way_claims_nothing_and_says_the_workspace_may_be_mixed(ws):
    import os

    def attempt(w):
        (w / "notes.py").write_text("ORIGINAL = 2  # changed\n")
        (w / "notes.py").chmod(0o444)  # the revert cannot write this one back
        (w / "junk.py").write_text("x = 1\n")

    controller, _turn, objective, path = build(ws, [attempt], attempts=1)
    try:
        result = run(controller, objective)
    finally:
        (ws / "notes.py").chmod(0o644)
    record = result.attempts[0]
    assert record.reverted == ()  # it did not put the workspace back, so it does not say it did
    assert "mixed state" in record.revert_note and "notes.py" in record.revert_note
    assert (path.with_suffix(".discarded") / "attempt-1" / "notes.py").read_text() == "ORIGINAL = 2  # changed\n"
    assert os.path.exists(path)  # the journal is intact


def test_the_converge_command_passes_the_revert_flag_to_the_loop(monkeypatch, tmp_path):
    from wisp import autonomous, autonomous_cli
    from wisp.core.goal import GoalState

    seen: dict = {}

    async def fake(objective, workspace, **kwargs):
        seen.update(kwargs)
        from wisp.core.convergence import ConvergenceResult

        return ConvergenceResult(goal_state=GoalState.GOAL_UNVERIFIED, attempts=(), reason="stub")

    monkeypatch.setattr(autonomous, "converge_on_objective", fake)
    autonomous_cli.run_converge(["fix it", "--revert", "--journal", str(tmp_path / "j.jsonl")], workspace=str(tmp_path))
    assert seen["revert_no_progress"] is True
    seen.clear()
    autonomous_cli.run_converge(["fix it"], workspace=str(tmp_path))
    assert seen["revert_no_progress"] is False


@pytest.mark.skipif(hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0, reason="root can remove a file from a read-only directory")
def test_a_created_file_that_cannot_be_removed_is_reported_not_swallowed(ws):
    def attempt(w):
        (w / "sub").mkdir()
        (w / "sub" / "new.txt").write_text("x\n")
        (w / "sub").chmod(0o555)

    controller, _turn, objective, _path = build(ws, [attempt], attempts=1)
    try:
        result = run(controller, objective)
    finally:
        (ws / "sub").chmod(0o755)
    assert result.attempts[0].reverted == ()
    assert "mixed state" in result.attempts[0].revert_note and "sub/new.txt" in result.attempts[0].revert_note


def test_the_rollback_rungs_restore_also_undoes_a_permission_change(ws):
    import os
    import stat

    target = ws / "notes.py"
    target.chmod(0o644)
    snap = WorkspaceSnapshot()
    assert snap.capture(str(ws))
    target.chmod(0o755)
    assert snap.restore(str(ws))
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o644


def test_the_converge_command_prints_what_was_reverted(monkeypatch, tmp_path, capsys):
    from wisp import autonomous, autonomous_cli
    from wisp.core.convergence import AttemptRecord, ConvergenceResult
    from wisp.core.goal import GoalState

    record = AttemptRecord(index=0, rung="INITIAL", directive="", observation=TurnObservation(turn_succeeded=True, terminal_outcome="succeeded"),
                           reverted=("a.py", "b.py"))
    skipped = AttemptRecord(index=1, rung="REPAIR", directive="", observation=TurnObservation(turn_succeeded=True, terminal_outcome="succeeded"),
                            revert_note="not reverted: there is no journal to keep the discarded work in")

    async def fake(objective, workspace, **kwargs):
        return ConvergenceResult(goal_state=GoalState.GOAL_UNVERIFIED, attempts=(record, skipped), reason="stub")

    monkeypatch.setattr(autonomous, "converge_on_objective", fake)
    autonomous_cli.run_converge(["fix it", "--revert"], workspace=str(tmp_path))
    out = capsys.readouterr().out
    assert "reverted: a.py, b.py" in out
    assert "not reverted: there is no journal to keep the discarded work in" in out
