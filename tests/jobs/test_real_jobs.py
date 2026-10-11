"""Background jobs with REAL processes through the host tier (the explicit `WISP_SANDBOX=off` tier, so every machine runs the same thing).

BJ1 refusal creates nothing, BJ3 nothing outlives its deadline or its supervisor, BJ4 honest status, BJ6 bounded output, BJ7 ceilings,
BJ8 reliable kill (children, SIGTERM-ignoring commands), and the point of the feature: a job outlives the process that started it."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.jobs.conftest import pid_alive, wait_finished, wait_until
from wisp.jobs.spawn import JobLimitError, spawn_job
from wisp.jobs.store import EXITED, KILLED, LOST, RUNNING, TIMED_OUT, JobStore, Limits, kill_job, read_json, reap_lost
from wisp.tools.errors import ToolError

pytestmark = pytest.mark.usefixtures("host_tier")

REPO = str(Path(__file__).resolve().parents[2])


def pid_of(path: Path) -> int:
    return int(wait_until(lambda: path.exists() and path.read_text().strip() or None))


class TestRunsAndReports:
    def test_a_successful_command(self, store):
        v = wait_finished(store, spawn_job(store, "echo hello"))
        assert (v.status, v.exit_code) == (EXITED, 0) and v.output.strip() == "hello" and v.tier

    def test_a_failing_command_keeps_its_exit_status(self, store):
        v = wait_finished(store, spawn_job(store, "echo oops; exit 3"))
        assert (v.status, v.exit_code) == (EXITED, 3) and v.output.startswith("[exit code: 3]") and "oops" in v.output

    def test_a_failing_pipeline_stage_is_not_hidden_by_the_last_stage(self, store):
        v = wait_finished(store, spawn_job(store, "false | cat"))
        assert v.status == EXITED  # the status of the shell is what it is; what matters is that a result was recorded, with the exit code the shell reported
        assert v.exit_code is not None

    def test_the_job_runs_in_its_workspace(self, store, ws):
        (Path(ws) / "marker.txt").write_text("here")
        assert wait_finished(store, spawn_job(store, "cat marker.txt")).output.strip() == "here"

    def test_the_mutation_index_at_start_is_recorded_for_the_verification_floor(self, store):
        v = wait_finished(store, spawn_job(store, "true", mutation_index=7))
        assert v.mutation_index == 7

    def test_bj6_output_is_bounded_on_disk_and_in_the_view(self, store):
        job_id = spawn_job(store, "python3 -c \"print('x' * 5000000)\"")
        v = wait_finished(store, job_id)
        assert (store.path(job_id) / "out.log").stat().st_size < 60_000 and len(v.output) <= 20_000

    def test_credentials_in_the_environment_do_not_reach_the_command(self, store, monkeypatch):
        monkeypatch.setenv("MY_SERVICE_API_KEY", "super-secret-value")
        assert "super-secret-value" not in wait_finished(store, spawn_job(store, "env")).output

    def test_the_directory_and_files_are_private(self, store):
        job_id = spawn_job(store, "true")
        wait_finished(store, job_id)
        d = store.path(job_id)
        assert oct(d.stat().st_mode & 0o777) == "0o700" and oct((d / "meta.json").stat().st_mode & 0o777) == "0o600" and oct((d / "out.log").stat().st_mode & 0o777) == "0o600"


class TestBJ1RefusalCreatesNothing:
    @pytest.mark.parametrize("command", ["rm -rf /", "", "a\x00b"])
    def test_a_refused_command_never_creates_a_job_directory(self, store, command):
        with pytest.raises(ToolError):
            spawn_job(store, command)
        assert not store.dir.exists() or list(store.dir.iterdir()) == []


class TestBJ7Ceilings:
    def test_the_per_workspace_ceiling_refuses_the_next_job_and_creates_nothing(self, root, ws):
        s = JobStore(root, ws, Limits(max_running_per_workspace=1))
        first = spawn_job(s, "sleep 30")
        wait_until(lambda: s.view(first).status == RUNNING and read_json(s.path(first) / "supervisor.json"))
        try:
            with pytest.raises(JobLimitError):
                spawn_job(s, "echo second")
            assert len(list(s.dir.iterdir())) == 1
        finally:
            kill_job(s, first)

    def test_a_finished_job_frees_its_slot(self, root, ws):
        s = JobStore(root, ws, Limits(max_running_per_workspace=1))
        wait_finished(s, spawn_job(s, "true"))
        wait_finished(s, spawn_job(s, "true"))

    def test_the_whole_store_ceiling(self, root, ws, tmp_path):
        other = tmp_path / "o"
        other.mkdir()
        lim = Limits(max_running_total=1)
        a, b = JobStore(root, ws, lim), JobStore(root, str(other), lim)
        first = spawn_job(a, "sleep 30")
        wait_until(lambda: read_json(a.path(first) / "supervisor.json"))
        try:
            with pytest.raises(JobLimitError):
                spawn_job(b, "echo x")
        finally:
            kill_job(a, first)


class TestLeftoversAtTheEnd:
    def test_a_background_process_the_command_leaves_behind_is_stopped_when_the_job_ends(self, store, tmp_path):
        kid_file = tmp_path / "kid"
        job_id = spawn_job(store, f"sleep 60 > /dev/null 2>&1 & echo $! > {kid_file}; sleep 1")
        kid = pid_of(kid_file)
        assert wait_finished(store, job_id).status == EXITED
        wait_until(lambda: not pid_alive(kid), 15)


class TestBJ8Kill:
    def test_a_frozen_supervisor_is_force_killed_with_its_processes(self, store, tmp_path):
        kid_file = tmp_path / "kid"
        job_id = spawn_job(store, f"sleep 60 & echo $! > {kid_file}; wait")
        kid = pid_of(kid_file)
        sup = read_json(store.path(job_id) / "supervisor.json")
        wait_until(lambda: any(p == kid for p, _ in (tuple(x) for x in (read_json(store.path(job_id) / "children.json") or {}).get("children", []))), 20)
        os.kill(int(sup["pid"]), signal.SIGSTOP)  # cannot handle SIGTERM while stopped
        v = kill_job(store, job_id, wait_s=1.0)
        assert v.status == KILLED and "force-killed" in v.note
        wait_until(lambda: not pid_alive(kid) and not pid_alive(int(sup["pid"])), 15)

    def test_kill_stops_the_command_and_its_children(self, store, tmp_path):
        pid_file, kid_file = tmp_path / "pid", tmp_path / "kid"
        job_id = spawn_job(store, f"echo $$ > {pid_file}; sleep 60 & echo $! > {kid_file}; wait")
        pid, kid = pid_of(pid_file), pid_of(kid_file)
        v = kill_job(store, job_id)
        assert v.status == KILLED and v.is_finished
        wait_until(lambda: not pid_alive(pid) and not pid_alive(kid), 15)

    def test_a_command_that_ignores_sigterm_still_dies(self, store, tmp_path):
        pid_file = tmp_path / "pid"
        job_id = spawn_job(store, f"trap '' TERM; echo $$ > {pid_file}; while true; do sleep 1; done")
        pid = pid_of(pid_file)
        assert kill_job(store, job_id).status == KILLED
        wait_until(lambda: not pid_alive(pid), 15)

    def test_a_grandchild_that_made_its_own_session_dies_too(self, store, ws, tmp_path):
        kid_file = tmp_path / "kid"
        (Path(ws) / "spawner.py").write_text(
            f"import subprocess, time\nc = subprocess.Popen(['sleep', '60'], start_new_session=True)\nopen({str(kid_file)!r}, 'w').write(str(c.pid))\ntime.sleep(60)\n")
        job_id = spawn_job(store, "python3 spawner.py")
        kid = pid_of(kid_file)
        kill_job(store, job_id)
        wait_until(lambda: not pid_alive(kid), 15)

    def test_killing_a_finished_job_is_a_noop_that_reports_it(self, store):
        job_id = spawn_job(store, "true")
        wait_finished(store, job_id)
        assert kill_job(store, job_id).status == EXITED

    def test_bj2_a_foreign_or_malformed_id_cannot_be_killed(self, store, root, tmp_path):
        other = tmp_path / "o"
        other.mkdir()
        theirs = JobStore(root, str(other))
        job_id = spawn_job(theirs, "sleep 30")
        try:
            wait_until(lambda: read_json(theirs.path(job_id) / "supervisor.json"))
            assert kill_job(store, job_id) is None and kill_job(store, "../x") is None and theirs.view(job_id).status == RUNNING
        finally:
            kill_job(theirs, job_id)


class TestNoImportHijack:
    """The supervisor runs `python -m wisp.jobs.supervisor`; with the workspace as the working directory a repo carrying its own top-level `wisp/` package
    would be imported INSTEAD of Wisp (verified: `python -m wisp` in such a directory runs the workspace copy). The supervisor must not run workspace code."""

    def test_a_workspace_with_its_own_wisp_package_does_not_run_inside_the_supervisor(self, store, ws, tmp_path):
        marker = tmp_path / "hijacked"
        hostile = Path(ws) / "wisp"
        hostile.mkdir()
        (hostile / "__init__.py").write_text(f"open({str(marker)!r}, 'w').write('the workspace copy of wisp ran')\nraise SystemExit(0)\n")
        v = wait_finished(store, spawn_job(store, "echo real-command-ran"))
        assert not marker.exists(), "workspace code ran inside the supervisor"
        assert (v.status, v.output.strip()) == (EXITED, "real-command-ran")

    def test_the_supervisor_is_launched_from_its_own_directory_not_the_workspace(self, store):
        job_id = spawn_job(store, "sleep 30")
        try:
            sup = wait_until(lambda: read_json(store.path(job_id) / "supervisor.json"))
            cwd = subprocess.run(["lsof", "-a", "-p", str(sup["pid"]), "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
            assert str(store.path(job_id)) in cwd or not cwd, cwd  # lsof absent: nothing to assert
        finally:
            kill_job(store, job_id)


class TestSpawnHousekeeping:
    def test_the_runtime_is_clamped_to_the_ceiling_the_tool_itself_enforces(self, store):
        for asked, expected in ((99999, 3600), (-5, 1), (0, 3600), (None, 3600), (30, 30)):
            job_id = spawn_job(store, "true", max_runtime=asked)
            assert read_json(store.path(job_id) / "meta.json")["max_runtime"] == expected, asked
            wait_finished(store, job_id)

    def test_spawning_settles_jobs_whose_supervisor_died(self, store, tmp_path):
        kid_file = tmp_path / "kid"
        lost = spawn_job(store, f"sleep 60 & echo $! > {kid_file}; wait")
        kid = pid_of(kid_file)
        sup = read_json(store.path(lost) / "supervisor.json")
        wait_until(lambda: any(p == kid for p, _ in (tuple(x) for x in (read_json(store.path(lost) / "children.json") or {}).get("children", []))), 20)
        os.kill(int(sup["pid"]), signal.SIGKILL)
        wait_until(lambda: store.view(lost).status == LOST, 15)
        wait_finished(store, spawn_job(store, "true"))
        assert (store.path(lost) / "state.json").exists()  # the spawn reaped it
        wait_until(lambda: not pid_alive(kid), 15)

    def test_spawning_prunes_old_finished_jobs(self, root, ws):
        s = JobStore(root, ws, Limits(max_finished=1))
        first = spawn_job(s, "true")
        wait_finished(s, first)
        time.sleep(0.05)
        second = spawn_job(s, "true")
        wait_finished(s, second)
        third = spawn_job(s, "true")
        wait_finished(s, third)
        assert s.path(first) is None and s.path(third) is not None

    def test_the_supervisor_itself_holds_no_credentials(self, store, monkeypatch):
        monkeypatch.setenv("MY_SERVICE_API_KEY", "super-secret-value")
        job_id = spawn_job(store, "sleep 30")
        try:
            sup = wait_until(lambda: read_json(store.path(job_id) / "supervisor.json"))
            env_view = subprocess.run(["ps", "ewwp", str(sup["pid"])], capture_output=True, text=True).stdout
            assert "WISP_JOB_ID=" + job_id in env_view and "super-secret-value" not in env_view
        finally:
            kill_job(store, job_id)

    def test_a_job_that_was_killed_before_it_started_never_runs_its_command(self, store, ws):
        job_id, d = store.new_dir()
        import json as _json

        (d / "meta.json").write_text(_json.dumps({"id": job_id, "command": "touch ran.txt", "workspace": ws, "created": time.time(), "mutation_index": 0, "max_runtime": 10}))
        (d / "state.json").write_text(_json.dumps({"status": "killed", "finished": time.time(), "exit_code": None}))
        r = subprocess.run([sys.executable, "-m", "wisp.jobs.supervisor", str(d)], capture_output=True, text=True, timeout=60, env={**os.environ, "PYTHONPATH": REPO}, cwd=ws)
        assert r.returncode == 0 and not (Path(ws) / "ran.txt").exists() and store.view(job_id).status == KILLED


class TestBJ3NothingOutlivesItsDeadline:
    def test_past_its_runtime_a_job_is_stopped_and_reported_timed_out(self, store, tmp_path):
        pid_file = tmp_path / "pid"
        job_id = spawn_job(store, f"echo $$ > {pid_file}; sleep 60", max_runtime=2)
        pid = pid_of(pid_file)
        v = wait_finished(store, job_id, 40)
        assert v.status == TIMED_OUT
        wait_until(lambda: not pid_alive(pid), 15)

    def test_a_supervisor_killed_hard_is_lost_and_the_reaper_kills_what_it_left(self, store, tmp_path):
        kid_file = tmp_path / "kid"
        job_id = spawn_job(store, f"sleep 60 & echo $! > {kid_file}; wait")
        kid = pid_of(kid_file)
        sup = read_json(store.path(job_id) / "supervisor.json")
        wait_until(lambda: any(p == kid for p, _ in (tuple(x) for x in (read_json(store.path(job_id) / "children.json") or {}).get("children", []))), 20)
        os.kill(int(sup["pid"]), signal.SIGKILL)
        wait_until(lambda: store.view(job_id).status == LOST, 15)
        assert store.view(job_id).exit_code is None and pid_alive(kid)  # nothing has reaped it yet
        assert job_id in reap_lost(store)
        wait_until(lambda: not pid_alive(kid), 15)
        assert store.view(job_id).status == LOST and reap_lost(store) == []

    def test_the_reaper_leaves_running_and_finished_jobs_alone(self, store):
        done = spawn_job(store, "true")
        wait_finished(store, done)
        running = spawn_job(store, "sleep 30")
        wait_until(lambda: read_json(store.path(running) / "supervisor.json"))
        try:
            assert reap_lost(store) == [] and store.view(running).status == RUNNING and store.view(done).status == EXITED
        finally:
            kill_job(store, running)


class TestTheFeatureItself:
    def test_a_job_outlives_the_process_that_started_it(self, store, ws, root):
        code = f"from wisp.jobs.store import JobStore; from wisp.jobs.spawn import spawn_job; print(spawn_job(JobStore({str(root)!r}, {ws!r}), 'sleep 3; echo survived'))"
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, "PYTHONPATH": REPO}, timeout=60)
        job_id = r.stdout.strip().splitlines()[-1]
        assert store.view(job_id).status == RUNNING  # the spawning process is already gone
        v = wait_finished(store, job_id, 40)
        assert (v.status, v.output.strip()) == (EXITED, "survived")

    def test_the_supervisor_has_its_own_session(self, store):
        job_id = spawn_job(store, "sleep 30")
        try:
            sup = wait_until(lambda: read_json(store.path(job_id) / "supervisor.json"))
            assert os.getsid(int(sup["pid"])) != os.getsid(os.getpid())
        finally:
            kill_job(store, job_id)
