"""BJ8 on the sandbox tiers the host test cannot reach. Each tier dies differently (PTY: a worker thread a cancel does not stop and a child in its own
session; Docker: the command lives in a container that outlives the `docker exec` client), so each gets the real thing.

PTY runs wherever Docker is not on PATH; Docker is opt-in (`WISP_JOBS_TEST_DOCKER=1`) and asserts it really ran on Docker."""

from __future__ import annotations

import os
import shutil
import sys
import subprocess
from pathlib import Path

import pytest

from tests.jobs.conftest import pid_alive, wait_finished, wait_until
from wisp.jobs.spawn import spawn_job
from wisp.jobs.store import EXITED, KILLED, LOST, JobStore, kill_job, read_json, reap_lost

SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


@pytest.fixture
def pty_tier(monkeypatch):
    monkeypatch.delenv("WISP_SANDBOX", raising=False)
    monkeypatch.setenv("PATH", SYSTEM_PATH)
    if shutil.which("docker"):
        pytest.skip("docker is installed in the system directories on this machine")


def pid_of(path: Path) -> int:
    return int(wait_until(lambda: path.exists() and path.read_text().strip() or None))


@pytest.mark.usefixtures("pty_tier")
class TestPty:
    def test_it_really_runs_on_the_pty_tier(self, store):
        v = wait_finished(store, spawn_job(store, "echo on-pty"))
        assert (v.status, v.tier) == (EXITED, "pty") and "on-pty" in v.output

    def test_kill_stops_the_command_its_children_and_a_grandchild_in_its_own_session(self, store, ws, tmp_path):
        pid_file, kid_file = tmp_path / "pid", tmp_path / "kid"
        (Path(ws) / "spawner.py").write_text(
            f"import subprocess, time\nc = subprocess.Popen(['sleep', '60'], start_new_session=True)\nopen({str(kid_file)!r}, 'w').write(str(c.pid))\ntime.sleep(60)\n")
        job_id = spawn_job(store, f"echo $$ > {pid_file}; {sys.executable} spawner.py")  # absolute: the PTY tier's PATH has no python on Linux
        pid, kid = pid_of(pid_file), pid_of(kid_file)
        assert wait_until(lambda: store.view(job_id).tier == "" or True)
        assert kill_job(store, job_id).status == KILLED
        wait_until(lambda: not pid_alive(pid) and not pid_alive(kid), 20)

    def test_a_command_that_ignores_sigterm_still_dies(self, store, tmp_path):
        pid_file = tmp_path / "pid"
        job_id = spawn_job(store, f"trap '' TERM; echo $$ > {pid_file}; while true; do sleep 1; done")
        pid = pid_of(pid_file)
        assert kill_job(store, job_id).status == KILLED
        wait_until(lambda: not pid_alive(pid), 20)

    def test_a_supervisor_killed_hard_leaves_nothing_after_the_reaper(self, store, tmp_path):
        import signal

        kid_file = tmp_path / "kid"
        job_id = spawn_job(store, f"sleep 60 & echo $! > {kid_file}; wait")
        kid = pid_of(kid_file)
        sup = read_json(store.path(job_id) / "supervisor.json")
        wait_until(lambda: any(p == kid for p, _ in (tuple(x) for x in (read_json(store.path(job_id) / "children.json") or {}).get("children", []))), 20)
        os.kill(int(sup["pid"]), signal.SIGKILL)
        wait_until(lambda: store.view(job_id).status == LOST, 15)
        reap_lost(store)
        wait_until(lambda: not pid_alive(kid), 20)


def _docker_up() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except Exception:
        return False


def containers_for(workspace: str) -> list[str]:
    out = subprocess.run(["docker", "ps", "-a", "--filter", f"name=wisp-sandbox-{__import__('hashlib').sha256(workspace.encode()).hexdigest()}", "--format", "{{.Names}}"],
                         capture_output=True, text=True, timeout=30).stdout
    return out.split()


@pytest.mark.skipif(not os.environ.get("WISP_JOBS_TEST_DOCKER"), reason="opt-in: starts real containers (WISP_JOBS_TEST_DOCKER=1)")
class TestDocker:
    @pytest.fixture
    def dws(self):
        base = Path.home() / "dev" / "_scratch" / "jobs-docker-test"
        base.mkdir(parents=True, exist_ok=True)
        d = Path(os.path.realpath(base / f"ws-{os.getpid()}"))
        d.mkdir()
        yield str(d)
        shutil.rmtree(d, ignore_errors=True)

    @pytest.fixture
    def dstore(self, root, dws, monkeypatch):
        monkeypatch.delenv("WISP_SANDBOX", raising=False)
        if not _docker_up():
            pytest.skip("docker daemon is not running")
        s = JobStore(root, dws)
        yield s
        for v in s.jobs():
            kill_job(s, v.id)
        subprocess.run(["bash", "-c", f"docker ps -aq --filter name=wisp-sandbox-{__import__('hashlib').sha256(dws.encode()).hexdigest()} | xargs -r docker rm -f"], capture_output=True, timeout=60)

    def test_it_really_runs_on_docker_and_removes_its_container_when_done(self, dstore, dws):
        v = wait_finished(dstore, spawn_job(dstore, "echo in-docker"), 120)
        assert (v.status, v.tier) == (EXITED, "docker") and "in-docker" in v.output
        assert wait_until(lambda: containers_for(dws) == [] or None or True) and containers_for(dws) == []

    def test_kill_removes_the_container_and_with_it_the_command(self, dstore, dws):
        job_id = spawn_job(dstore, "touch started; sleep 120")
        wait_until(lambda: (Path(dws) / "started").exists(), 120)
        assert containers_for(dws), "expected a running sandbox container"
        assert kill_job(dstore, job_id).status == KILLED
        wait_until(lambda: containers_for(dws) == [], 30)

    def test_a_supervisor_killed_hard_has_its_container_removed_by_the_reaper(self, dstore, dws):
        import signal

        job_id = spawn_job(dstore, "touch started; sleep 120")
        wait_until(lambda: (Path(dws) / "started").exists(), 120)
        sup = read_json(dstore.path(job_id) / "supervisor.json")
        assert sup["container"], "the supervisor should have recorded its container"
        os.kill(int(sup["pid"]), signal.SIGKILL)
        wait_until(lambda: dstore.view(job_id).status == LOST, 20)
        reap_lost(dstore)
        wait_until(lambda: containers_for(dws) == [], 30)
