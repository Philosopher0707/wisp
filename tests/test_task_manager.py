# tests/test_task_manager.py — task lifecycle over RunStore (M6 T1).
import pytest
from wisp.infra.store import UnifiedStore
from wisp.runs.store import SQLiteRunStore
from wisp.task.manager import TaskManager


@pytest.fixture()
def tm(tmp_path):
    return TaskManager(SQLiteRunStore(UnifiedStore(tmp_path / "w.db")))


def test_start_list_inspect(tm):
    tid = tm.start("refactor auth", workspace="/w")
    assert tid.startswith("task-")
    tasks = tm.list()
    assert [t["task_id"] for t in tasks] == [tid]
    insp = tm.inspect(tid)
    assert insp["goal"] == "refactor auth" and insp["status"] == "running"


def test_pause_resume_cancel(tm):
    tid = tm.start("job", workspace="/w")
    assert tm.pause(tid)["status"] == "paused"
    assert tm.resume(tid)["status"] == "running"
    assert tm.cancel(tid)["status"] == "cancelled"


def test_illegal_resume_rejected(tm):
    tid = tm.start("job", workspace="/w")
    tm.cancel(tid)
    with pytest.raises(ValueError, match="illegal|terminal|immutable"):
        tm.resume(tid)


def test_inspect_unknown(tm):
    with pytest.raises(KeyError):
        tm.inspect("task-nope")


def test_attach_plan(tm):
    tid = tm.start("job", workspace="/w")
    plan = {"files": ["a.py"], "actions": [{"tool": "read_file"}]}
    tm.attach_plan(tid, plan)
    assert tm.inspect(tid)["plan"] == plan
