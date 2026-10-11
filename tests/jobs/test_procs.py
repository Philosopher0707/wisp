"""Process helpers with real processes."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.jobs.conftest import pid_alive, wait_until
from wisp.jobs import procs


def test_start_time_identifies_this_process():
    assert procs.start_time(os.getpid()) != "" and procs.start_time(2**30) == "" and procs.start_time(0) == ""


def test_a_wrong_start_time_means_a_different_process():
    assert procs.alive(os.getpid(), "") and procs.alive(os.getpid(), procs.start_time(os.getpid())) and not procs.alive(os.getpid(), "Mon Jan  1 00:00:00 1990")


def test_descendants_find_a_child_that_made_its_own_session(tmp_path):
    flag = tmp_path / "pid"
    code = f"import os,subprocess,sys,time\nc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True)\nopen({str(flag)!r},'w').write(str(c.pid))\ntime.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", code])
    try:
        wait_until(flag.exists)
        child = int(flag.read_text())
        assert child in {p for p, _ in procs.descendants(parent.pid)}
    finally:
        procs.kill_all(procs.descendants(parent.pid) + [(parent.pid, procs.start_time(parent.pid))], grace=0.5)
        parent.wait(timeout=5)


def test_kill_all_escalates_to_sigkill_for_a_process_that_ignores_sigterm(tmp_path):
    ready = tmp_path / "ready"
    code = f"import signal,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nopen({str(ready)!r},'w').write('x')\ntime.sleep(60)"
    p = subprocess.Popen([sys.executable, "-c", code])
    wait_until(ready.exists)
    forced = procs.kill_all([(p.pid, procs.start_time(p.pid))], grace=0.5)
    p.wait(timeout=5)
    assert forced == [p.pid] and not pid_alive(p.pid)


def test_kill_all_never_touches_a_process_whose_start_time_differs():
    p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        assert procs.kill_all([(p.pid, "Mon Jan  1 00:00:00 1990")], grace=0.2) == [] and p.poll() is None
    finally:
        p.kill()
        p.wait(timeout=5)


def test_kill_all_reports_nothing_forced_when_everything_obeyed_sigterm():
    p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    forced = procs.kill_all([(p.pid, procs.start_time(p.pid))], grace=5.0)
    p.wait(timeout=5)
    assert forced == [] and not pid_alive(p.pid)


def test_group_members_find_an_orphan_that_was_reparented_to_init(tmp_path):
    flag = tmp_path / "kid"
    code = f"import subprocess,sys,time\nc=subprocess.Popen(['sleep','60'])\nopen({str(flag)!r},'w').write(str(c.pid))\ntime.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    try:
        wait_until(flag.exists)
        kid = int(flag.read_text())
        group = procs.group_of(kid)
        assert group == parent.pid
        parent.kill()
        parent.wait(timeout=5)
        wait_until(lambda: procs.descendants(parent.pid) == [] and pid_alive(kid))
        assert kid in {p for p, _ in procs.group_members({group})}
    finally:
        procs.kill_all(procs.group_members({parent.pid}), grace=0.5)


def test_group_members_of_nothing_is_nothing():
    assert procs.group_members(set()) == [] and procs.group_of(2**30) == 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="macOS ps hides other processes' environments in a listing; the group sweep covers it")
def test_find_by_env_sees_a_process_that_carries_the_marker():
    p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"], env={**os.environ, "WISP_JOB_ID": "bj-0123456789abcdef"})
    try:
        wait_until(lambda: p.pid in {x for x, _ in procs.find_by_env("bj-0123456789abcdef")})
        assert procs.find_by_env("bj-ffffffffffffffff") == []
    finally:
        p.kill()
        p.wait(timeout=5)


def test_the_supervisors_kill_sweeps_remembered_process_groups_even_with_no_pids_remembered(tmp_path):
    """A fast command can orphan a process before any snapshot saw its pid; its process group is still known."""
    from wisp.jobs import supervisor

    flag = tmp_path / "kid"
    code = f"import subprocess,time\nc=subprocess.Popen(['sleep','60'])\nopen({str(flag)!r},'w').write(str(c.pid))\ntime.sleep(60)"
    parent = subprocess.Popen([sys.executable, "-c", code], start_new_session=True)
    wait_until(flag.exists)
    kid = int(flag.read_text())
    group = procs.group_of(kid)
    parent.kill()
    parent.wait(timeout=5)
    assert pid_alive(kid)
    supervisor._kill_tree({}, "", {group})
    wait_until(lambda: not pid_alive(kid), 15)
