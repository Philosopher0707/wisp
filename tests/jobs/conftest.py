from __future__ import annotations

import os
import time

import pytest

from wisp.jobs import procs
from wisp.jobs.store import FINISHED, JobStore


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return os.path.realpath(root)


@pytest.fixture
def root(tmp_path):
    return tmp_path / "jobs"


@pytest.fixture
def store(root, ws):
    return JobStore(root, ws)


@pytest.fixture
def host_tier(monkeypatch):
    """The explicit host tier: no Docker, no PTY. Real processes, deterministic on every machine."""
    monkeypatch.setenv("WISP_SANDBOX", "off")


def wait_until(predicate, timeout=30.0, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("condition not met within %.0fs" % timeout)


def wait_finished(store, job_id, timeout=40.0):
    return wait_until(lambda: (lambda v: v if v is not None and v.status in FINISHED else None)(store.view(job_id)), timeout)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return procs.start_time(pid) != ""


def fake_running(store: JobStore, command="sleep 1") -> str:
    """A job directory that looks running: the supervisor is this test process, which is alive."""
    import json

    job_id, d = store.new_dir()
    (d / "meta.json").write_text(json.dumps({"id": job_id, "command": command, "workspace": store.workspace, "created": time.time(), "mutation_index": 0, "max_runtime": 60}))
    (d / "supervisor.json").write_text(json.dumps({"pid": os.getpid(), "start": procs.start_time(os.getpid()), "container": ""}))
    return job_id
