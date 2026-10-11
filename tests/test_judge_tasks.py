"""Properties every judge task must have. A bad task silently corrupts every score built on it."""
import tempfile
from pathlib import Path

import pytest

from wisp.judge import core as J
from wisp.judge.tasks import HELD_OUT

TASKS = J.load_tasks()


def _ws(task):
    ws = Path(tempfile.mkdtemp(prefix="jt-"))
    for rel, body in task.files.items():
        (ws / rel).write_text(body)
    return ws, J.snapshot(ws)


def test_every_shipped_task_is_a_real_task():
    """Visible check fails on the fixture, and the hidden check fails too (nothing is pre-solved)."""
    for t in TASKS.values():
        ws, _ = _ws(t)
        assert J.run_check(ws, t.visible) != 0, t.id
        (ws / J.HIDDEN_NAME).write_text(t.hidden)
        assert J.run_check(ws, J.HIDDEN_NAME) != 0, t.id



def test_held_out_ids_all_exist():
    assert HELD_OUT <= set(TASKS)
    assert len(HELD_OUT) >= 3


@pytest.mark.parametrize("seed", ["1", "2", "3"])
def test_hidden_checks_do_not_depend_on_the_hash_seed(seed, monkeypatch):
    """A buggy fixture must fail the hidden check under any PYTHONHASHSEED (dedupe once passed by chance)."""
    monkeypatch.setenv("PYTHONHASHSEED", seed)
    for t in TASKS.values():
        ws, _ = _ws(t)
        (ws / J.HIDDEN_NAME).write_text(t.hidden)
        assert J.run_check(ws, J.HIDDEN_NAME) != 0, (t.id, seed)
