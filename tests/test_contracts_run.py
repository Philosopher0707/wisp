# tests/test_contracts_run.py
import pytest
from wisp.contracts.run import RunStatus, EVENT_KINDS, Transition
from wisp.multi_agent.task import EventKind


def test_run_status_is_produced_vocabulary():
    assert {s.value for s in RunStatus} == {"running", "completed", "failed", "cancelled"}


def test_event_kinds_match_producers():
    assert set(EVENT_KINDS) == {EventKind.PLANNING, EventKind.TASK_STARTED,
        EventKind.TASK_PROGRESS, EventKind.TASK_COMPLETED, EventKind.TASK_FAILED,
        EventKind.TASK_RETRY, EventKind.DONE}


def test_transition_round_trip():
    t = Transition(run_id="r1", seq=0, from_state="running", to_state="completed")
    assert Transition.from_dict(t.to_dict()) == t


def test_from_dict_unknown_field_rejected():
    with pytest.raises(ValueError, match="unknown transition fields"):
        Transition.from_dict({"run_id": "r", "seq": 0,
                              "from_state": "a", "to_state": "b", "bogus": 1})
