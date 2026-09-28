"""The run_bash output sink must not change where a command runs.

Every real session builds a `CompositionRoot`, which calls `agent.tools.runner.install_sink()`.
The sink used to replace `wisp.tools.bash.async_tool_run_bash` with a wrapper that started the
command itself with `asyncio.create_subprocess_shell` — on the host. So for every agent command
the sandbox tier router was skipped and the UNCONFINED warning never fired, while the unpatched
tool (the one the sandbox tests exercise) looked confined.

These pins hold the sandbox contract WITH the sink installed: the router carries the command,
a host fallback still warns, the operator's explicit `WISP_SANDBOX=off` stays quiet, a timeout is
the same `ToolError`, and the sink still writes the full output to disk.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from wisp.sandbox.router import SandboxRouter
from wisp.tools import bash as bash_mod
from wisp.tools._utils import ToolError


class FakeSandbox:
    """Recording stand-in for a real (e.g. Docker) tier."""

    name = "fake-docker"

    def __init__(self, result=(0, "canned-out", "canned-err")) -> None:
        self.calls: list[str] = []
        self.result = result

    def is_available(self) -> bool:
        return True

    async def run(self, command: str, cwd: str = "", timeout: int = 60):
        self.calls.append(command)
        return self.result


class FakeHostFallback(FakeSandbox):
    """The router's last tier when nothing confining is available."""

    name = "host"
    reason = "fallback"


@pytest.fixture
def sink(tmp_path, monkeypatch):
    """Install the sink the way a session does, with its log dir inside `tmp_path`."""
    from agent.tools.runner import install_sink, uninstall_sink

    monkeypatch.chdir(tmp_path)
    uninstall_sink()
    install_sink()
    assert getattr(bash_mod.async_tool_run_bash, "_sink_patched", False) is True
    yield tmp_path / ".agent" / "logs"
    uninstall_sink()


def _route_to(monkeypatch, provider) -> None:
    monkeypatch.delenv("WISP_SANDBOX", raising=False)
    monkeypatch.setattr(bash_mod, "get_router",
                        lambda workspace: SandboxRouter(workspace, tiers=[provider]))


def _run(command: str, workspace, timeout: int = 30) -> str:
    return asyncio.run(bash_mod.async_tool_run_bash(
        command=command, workspace=str(workspace), timeout=timeout))


def test_sink_routes_the_command_through_the_sandbox(tmp_path, monkeypatch, sink):
    fake = FakeSandbox()
    _route_to(monkeypatch, fake)
    marker = tmp_path / "host-touched.txt"

    out = _run(f"touch {marker} && echo hi", tmp_path)

    assert fake.calls, "the sandbox router never saw the command"
    assert not marker.exists(), "the command ran on the host despite a sandbox tier"
    assert "canned-out" in out and "canned-err" in out


def test_sink_keeps_the_unconfined_warning(tmp_path, monkeypatch, sink, caplog):
    _route_to(monkeypatch, FakeHostFallback())

    with caplog.at_level(logging.INFO, logger="wisp.tools.bash"):
        _run("echo hi", tmp_path)

    warns = [r for r in caplog.records
             if r.levelno >= logging.WARNING and "UNCONFINED" in r.message]
    assert len(warns) == 1


def test_sink_keeps_explicit_off_quiet(tmp_path, monkeypatch, sink, caplog):
    from wisp import sandbox as sandbox_mod

    monkeypatch.setenv("WISP_SANDBOX", "off")
    sandbox_mod.reset_sandbox()
    try:
        with caplog.at_level(logging.INFO, logger="wisp.tools.bash"):
            out = _run("echo explicit-host", tmp_path)
    finally:
        sandbox_mod.reset_sandbox()

    assert "explicit-host" in out
    assert not [r for r in caplog.records
                if r.levelno >= logging.WARNING and "UNCONFINED" in r.message]


def test_sink_writes_the_full_output_and_returns_a_bounded_preview(tmp_path, monkeypatch, sink):
    stdout = "\n".join(f"line-{i}" for i in range(60))
    _route_to(monkeypatch, FakeSandbox(result=(3, stdout, "boom-err")))

    out = _run("make everything", tmp_path)

    logged = (sink / "last_command.log").read_text()
    assert "line-0" in logged and "line-59" in logged and "boom-err" in logged
    assert "# exit: 3" in logged
    assert out.startswith("[exit code: 3]")
    assert "line-59" not in out, "the preview must stay bounded; the disk holds the rest"


def test_sink_timeout_is_the_same_tool_error(tmp_path, monkeypatch, sink):
    _route_to(monkeypatch, FakeSandbox(result=(-1, "partial-out", "Command timed out after 5s")))

    with pytest.raises(ToolError, match="timed out"):
        _run("sleep 60", tmp_path, timeout=5)

    assert "partial-out" in (sink / "last_command.log").read_text()


def test_session_root_keeps_bash_confined(tmp_path, monkeypatch):
    """The production observation point: a real `CompositionRoot` installs the sink, and the
    executor then resolves `wisp.tools.bash.async_tool_run_bash` at call time."""
    from agent.tools.runner import uninstall_sink
    from wisp.composition import CompositionRoot
    from wisp.config import WispConfig

    monkeypatch.chdir(tmp_path)
    uninstall_sink()
    fake = FakeSandbox()
    _route_to(monkeypatch, fake)
    root = CompositionRoot(config=WispConfig().replace(workspace=str(tmp_path)))
    try:
        from wisp.tools.bash import async_tool_run_bash
        assert getattr(async_tool_run_bash, "_sink_patched", False) is True
        out = asyncio.run(async_tool_run_bash(
            command="echo via-root", workspace=str(tmp_path), timeout=30))
    finally:
        root.shutdown()
    assert fake.calls == ["echo via-root"]
    assert "canned-out" in out
