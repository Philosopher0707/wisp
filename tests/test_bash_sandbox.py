"""GH#13: run_bash must execute through the sandbox provider.

Today async_tool_run_bash shells out directly — get_sandbox() (Docker with
Noop fallback) exists but nothing calls it. These pins prove the wiring:
an available provider carries the command (host untouched), output keeps
the exact host-path format, and fallback is loud, never silent.
"""

from __future__ import annotations

import logging

import pytest

from wisp.tools import bash as bash_mod


class FakeSandbox:
    """Recording stand-in for a real (e.g. Docker) provider."""

    name = "fake-docker"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def is_available(self) -> bool:
        return True

    async def run(self, command: str, cwd: str = "", timeout: int = 60):
        self.calls.append((command, cwd, timeout))
        return (0, "canned-out", "canned-err")


@pytest.mark.asyncio
async def test_available_provider_carries_command(tmp_path, monkeypatch) -> None:
    """RED today: the tool ignores providers and shells out on the host."""
    fake = FakeSandbox()
    monkeypatch.setattr(bash_mod, "get_sandbox", lambda workspace: fake)
    marker = tmp_path / "host-touched.txt"
    out = await bash_mod.async_tool_run_bash(
        f"touch {marker} && echo hi", str(tmp_path), timeout=30)
    assert fake.calls, "sandbox provider was never consulted"
    assert fake.calls[0][0].startswith("touch ")
    assert not marker.exists(), "command executed on host despite provider"
    assert out == "canned-out\n--- stderr ---\ncanned-err"


@pytest.mark.asyncio
async def test_provider_timeout_maps_to_tool_error(tmp_path, monkeypatch) -> None:
    from wisp.tools._utils import ToolError

    class SlowFake(FakeSandbox):
        async def run(self, command: str, cwd: str = "", timeout: int = 60):
            return (-1, "", f"Command timed out after {timeout}s")

    monkeypatch.setattr(bash_mod, "get_sandbox", lambda workspace: SlowFake())
    with pytest.raises(ToolError, match="timed out"):
        await bash_mod.async_tool_run_bash("sleep 5", str(tmp_path), timeout=5)


@pytest.mark.asyncio
async def test_fallback_runs_host_and_warns(tmp_path, monkeypatch, caplog) -> None:
    """No provider (real get_sandbox, no Docker here): host runs + loud log."""
    from wisp import sandbox as sandbox_mod

    sandbox_mod.reset_sandbox()
    try:
        with caplog.at_level(logging.WARNING, logger="wisp.tools.bash"):
            out = await bash_mod.async_tool_run_bash("echo fallback-hi", str(tmp_path))
    finally:
        sandbox_mod.reset_sandbox()
    assert "fallback-hi" in out
    assert any("host" in r.message.lower() and "sandbox" in r.message.lower()
               for r in caplog.records), \
        "unconfined execution must be logged loudly, never silent"


def test_sandbox_off_switch(monkeypatch) -> None:
    from wisp import sandbox as sandbox_mod

    monkeypatch.setenv("WISP_SANDBOX", "off")
    sandbox_mod.reset_sandbox()
    try:
        provider = sandbox_mod.get_sandbox("/tmp")
        assert type(provider).__name__ == "NoopSandbox"
    finally:
        sandbox_mod.reset_sandbox()
        monkeypatch.undo()
