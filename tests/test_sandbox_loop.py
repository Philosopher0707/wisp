"""M2: Docker sandbox setup must never block the event loop.

Cold-start container setup (docker rm -f + docker run -d, up to ~40s of
blocking subprocess) ran inline inside async run(); the diagnostics
route likewise called is_available() (docker info, 10s) on the loop.
Both now go through asyncio.to_thread — pinned by thread-identity.
"""

import asyncio
import threading
import time

import pytest

from wisp.sandbox import DockerSandbox
from wisp.sandbox.router import SandboxRouter


@pytest.mark.asyncio
async def test_container_setup_runs_off_the_loop(monkeypatch):
    sandbox = DockerSandbox("/tmp")
    seen_threads: dict[str, int] = {}

    def _fake_run(cmd, **kwargs):
        seen_threads["setup"] = threading.get_ident()
        time.sleep(0.2)  # simulate a slow daemon
        return type("R", (), {"returncode": 0, "stdout": "cid\n",
                              "stderr": ""})()

    monkeypatch.setattr("wisp.sandbox.subprocess.run", _fake_run)
    monkeypatch.setattr(
        "wisp.sandbox.shutil.which", lambda _: "/usr/bin/docker")

    async def _fake_exec(*cmd, **kwargs):
        seen_threads["exec"] = threading.get_ident()

        class P:
            returncode = 0
            async def communicate(self):
                return b"out", b"err"
            def kill(self):
                pass
            async def wait(self):
                pass

        await asyncio.sleep(0.01)
        return P()

    monkeypatch.setattr(
        "wisp.sandbox.asyncio.create_subprocess_exec", _fake_exec)

    code, out, err = await sandbox.run("echo hi")
    assert code == 0, err
    assert "setup" in seen_threads and "exec" in seen_threads
    assert seen_threads["setup"] != threading.get_ident(), (
        "container setup blocked the event-loop thread"
    )


@pytest.mark.asyncio
async def test_is_available_caller_can_stay_on_loop():
    """is_available() itself stays sync (cached after first call), so an
    async caller that must not block wraps it in to_thread — the route
    does. Here we pin that a cached second call costs nothing blocking."""
    sandbox = DockerSandbox("/tmp")
    sandbox._available = True  # pre-cached
    result = await asyncio.to_thread(sandbox.is_available)
    assert result is True


def test_diagnostics_route_wraps_probe_in_to_thread():
    """Structural pin: the status route must not call is_available()
    directly."""
    from pathlib import Path

    text = Path("wisp/server/routes/diagnostics.py").read_text()
    assert "await asyncio.to_thread(sandbox.is_available)" in text, (
        "sandbox probe back on the event loop"
    )


@pytest.mark.asyncio
async def test_workspace_routers_have_isolated_docker_lifecycle(
    tmp_path, monkeypatch
):
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    router_a = SandboxRouter(str(workspace_a))
    router_b = SandboxRouter(str(workspace_a))
    docker_a = router_a.tiers[0]
    docker_b = router_b.tiers[0]
    assert isinstance(docker_a, DockerSandbox)
    assert isinstance(docker_b, DockerSandbox)

    assert docker_a.container_name != docker_b.container_name
    assert DockerSandbox(str(workspace_b)).container_name != docker_a.container_name
    assert "/" not in docker_a.container_name
    assert "\\" not in docker_a.container_name
    assert "/" not in docker_b.container_name
    assert "\\" not in docker_b.container_name

    docker_commands: list[list[str]] = []
    exec_commands: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        docker_commands.append(list(cmd))
        return type("Result", (), {"returncode": 0, "stdout": "cid\n",
                                   "stderr": ""})()

    monkeypatch.setattr("wisp.sandbox.subprocess.run", _fake_run)
    docker_a._available = True
    docker_b._available = True

    async def _fake_exec(*cmd, **kwargs):
        exec_commands.append(list(cmd))

        class Process:
            returncode = 0

            async def communicate(self):
                return b"out", b""

            def kill(self):
                pass

            async def wait(self):
                pass

        return Process()

    monkeypatch.setattr(
        "wisp.sandbox.asyncio.create_subprocess_exec", _fake_exec
    )

    assert await docker_a.run("echo a") == (0, "out", "")
    assert await docker_b.run("echo b") == (0, "out", "")
    assert docker_a._container_ready is True
    assert docker_b._container_ready is True
    assert ["docker", "run", "-d", "--name", docker_a.container_name] == (
        docker_commands[1][:5]
    )
    assert ["docker", "run", "-d", "--name", docker_b.container_name] == (
        docker_commands[3][:5]
    )
    assert exec_commands[0][4] == docker_a.container_name
    assert exec_commands[1][4] == docker_b.container_name

    docker_a.cleanup()
    assert docker_a._container_ready is False
    assert docker_b._container_ready is True
    assert docker_commands[-1] == [
        "docker", "rm", "-f", docker_a.container_name
    ]


def test_cleanup_removes_partial_start_container(tmp_path, monkeypatch):
    sandbox = DockerSandbox(str(tmp_path))
    sandbox._available = True
    docker_commands: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        docker_commands.append(list(cmd))
        if cmd[:3] == ["docker", "run", "-d"]:
            return type(
                "Result",
                (),
                {"returncode": 1, "stdout": "", "stderr": "daemon failed"},
            )()
        return type(
            "Result", (), {"returncode": 0, "stdout": "", "stderr": ""}
        )()

    monkeypatch.setattr("wisp.sandbox.subprocess.run", _fake_run)

    with pytest.raises(RuntimeError, match="container start failed"):
        sandbox._ensure_container()

    assert sandbox._container_ready is False
    sandbox.cleanup()
    assert docker_commands[-1] == [
        "docker", "rm", "-f", sandbox.container_name
    ]


def test_cleanup_handles_removal_failure(tmp_path, monkeypatch, caplog):
    sandbox = DockerSandbox(str(tmp_path))
    sandbox._container_ready = False
    docker_commands: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        docker_commands.append(list(cmd))
        return type(
            "Result",
            (),
            {"returncode": 1, "stdout": "", "stderr": "daemon unavailable"},
        )()

    monkeypatch.setattr("wisp.sandbox.subprocess.run", _fake_run)

    with caplog.at_level("WARNING", logger="wisp.sandbox"):
        sandbox.cleanup()

    assert sandbox._container_ready is False
    assert docker_commands == [["docker", "rm", "-f", sandbox.container_name]]
    assert "Failed to remove Docker container" in caplog.text


def test_cleanup_ignores_missing_container(tmp_path, monkeypatch, caplog):
    sandbox = DockerSandbox(str(tmp_path))
    sandbox._container_ready = True

    def _fake_run(cmd, **kwargs):
        return type(
            "Result",
            (),
            {
                "returncode": 1,
                "stdout": b"",
                "stderr": (
                    b"Error response from daemon: "
                    b"No such container: " + sandbox.container_name.encode()
                ),
            },
        )()

    monkeypatch.setattr("wisp.sandbox.subprocess.run", _fake_run)

    with caplog.at_level("WARNING", logger="wisp.sandbox"):
        sandbox.cleanup()

    assert sandbox._container_ready is False
    assert "Failed to remove Docker container" not in caplog.text
