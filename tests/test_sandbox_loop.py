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
