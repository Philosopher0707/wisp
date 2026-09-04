# tests/test_runs_record.py
from wisp.runs.record import (
    LEGAL_TRANSITIONS,
    RunRecord,
    RunState,
    is_legal,
)


def test_terminal_states_immutable():
    for terminal in (RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED):
        assert LEGAL_TRANSITIONS[terminal] == ()
        for other in RunState:
            assert not is_legal(terminal, other)


def test_happy_path_legal():
    assert is_legal(RunState.QUEUED, RunState.PLANNING)
    assert is_legal(RunState.PLANNING, RunState.RUNNING)
    assert is_legal(RunState.RUNNING, RunState.AWAITING_APPROVAL)
    assert is_legal(RunState.AWAITING_APPROVAL, RunState.RUNNING)
    assert is_legal(RunState.RUNNING, RunState.SUCCEEDED)


def test_pause_resume_cancel():
    assert is_legal(RunState.RUNNING, RunState.PAUSED)
    assert is_legal(RunState.PAUSED, RunState.RUNNING)
    assert is_legal(RunState.RUNNING, RunState.CANCELLED)
    assert is_legal(RunState.QUEUED, RunState.CANCELLED)


def test_illegal_skip_rejected():
    assert not is_legal(RunState.QUEUED, RunState.SUCCEEDED)
    assert not is_legal(RunState.PAUSED, RunState.SUCCEEDED)
    assert not is_legal(RunState.AWAITING_APPROVAL, RunState.PLANNING)


def test_record_round_trip():
    r = RunRecord(run_id="bg-1", prompt="p", status=RunState.RUNNING)
    d = r.to_dict()
    assert RunRecord.from_dict(d) == r
    assert d["status"] == "running"
