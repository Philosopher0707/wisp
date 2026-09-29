"""Host-vs-sandbox fallback warnings: deterministic expectations.

Contract (independent of whether the test host has Docker):
  WISP_SANDBOX=off          → Noop(explicit)  + info,    never a warning
  auto + Docker available   → Docker          + info,    never a warning
  auto + Docker unavailable → Noop(fallback)  + 1 warning naming the cause
Tool layer: the UNCONFINED warning fires only for fallback-host, never for
an explicit host choice and never for a real sandbox. Docker availability
is faked; no test here touches a daemon.
"""

import logging

import pytest


@pytest.fixture(autouse=True)
def _the_real_bash_tool():
    """Test `wisp.tools.bash.async_tool_run_bash` itself, not a patch another test left behind.

    Building a `CompositionRoot` calls `agent.tools.runner.install_sink()`, which replaces
    `async_tool_run_bash` process-wide with a disk-sink wrapper. The wrapper once started the
    command itself on the host, skipping the tier router and the UNCONFINED warning, so whether
    this module saw the real tool depended on test order. The wrapper now runs through
    `run_bash_confined` (pinned in `test_sink_keeps_sandbox.py`); this keeps the module on the
    unwrapped tool so each file pins exactly one layer.
    """
    try:
        from agent.tools.runner import uninstall_sink
    except ImportError:
        yield
        return
    uninstall_sink()
    yield


def _no_docker(monkeypatch):
    from wisp.sandbox import DockerSandbox

    monkeypatch.setattr(DockerSandbox, "is_available", lambda self: False)


def _yes_docker(monkeypatch):
    from wisp.sandbox import DockerSandbox

    monkeypatch.setattr(DockerSandbox, "is_available", lambda self: True)


@pytest.mark.asyncio
async def test_explicit_off_is_info_not_warning(tmp_path, monkeypatch, caplog):
    from wisp.tools import bash as bash_mod
    from wisp import sandbox as sandbox_mod

    monkeypatch.setenv("WISP_SANDBOX", "off")
    sandbox_mod.reset_sandbox()
    try:
        with caplog.at_level(logging.INFO, logger="wisp.tools.bash"):
            out = await bash_mod.async_tool_run_bash("echo hi", str(tmp_path))
        assert "hi" in out
        warns = [r for r in caplog.records if r.levelno >= logging.WARNING
                 and "UNCONFINED" in r.message]
        assert warns == []
    finally:
        sandbox_mod.reset_sandbox()


@pytest.mark.asyncio
async def test_fallback_host_warns_at_tool_layer(tmp_path, monkeypatch, caplog):
    from wisp.tools import bash as bash_mod
    from wisp import sandbox as sandbox_mod
    from wisp.sandbox.router import reset_router

    monkeypatch.delenv("WISP_SANDBOX", raising=False)
    _no_docker(monkeypatch)
    reset_router()
    sandbox_mod.reset_sandbox()
    try:
        with caplog.at_level(logging.INFO, logger="wisp.tools.bash"):
            out = await bash_mod.async_tool_run_bash("echo hi", str(tmp_path))
        assert "hi" in out
        warns = [r for r in caplog.records if r.levelno >= logging.WARNING
                 and "UNCONFINED" in r.message]
        assert len(warns) == 1
    finally:
        sandbox_mod.reset_sandbox()
        reset_router()


def test_decision_matrix(tmp_path, monkeypatch, caplog):
    from wisp import sandbox as sandbox_mod

    cases = [
        # (env, docker_available, provider_type, warns_expected)
        ("off", False, "NoopSandbox", False),
        ("off", True, "NoopSandbox", False),
        ("unset", True, "DockerSandbox", False),
        ("unset", False, "NoopSandbox", True),
    ]
    for env_mode, docker_ok, want_type, want_warn in cases:
        if env_mode == "unset":
            monkeypatch.delenv("WISP_SANDBOX", raising=False)
        else:
            monkeypatch.setenv("WISP_SANDBOX", env_mode)
        (_yes_docker if docker_ok else _no_docker)(monkeypatch)
        sandbox_mod.reset_sandbox()
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="wisp.sandbox"):
            s = sandbox_mod.get_sandbox(str(tmp_path))
        assert type(s).__name__ == want_type, (env_mode, docker_ok)
        assert getattr(s, "reason", "fallback") == (
            "explicit" if env_mode == "off" else "fallback")
        warns = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert (len(warns) == 1) == want_warn, (env_mode, docker_ok)
        sandbox_mod.reset_sandbox()


def test_reset_sandbox_does_not_touch_router_cache(tmp_path, monkeypatch):
    from wisp import sandbox as sandbox_mod
    from wisp.sandbox.router import get_router, reset_router

    class CleanupTier:
        name = "cleanup"

        def __init__(self):
            self.cleaned = 0

        def cleanup(self):
            self.cleaned += 1

    reset_router()
    monkeypatch.setenv("WISP_SANDBOX", "off")
    first = get_router(str(tmp_path))
    tier = CleanupTier()
    first.tiers = [tier]
    sandbox_mod.get_sandbox(str(tmp_path))

    sandbox_mod.reset_sandbox()

    assert get_router(str(tmp_path)) is first
    assert tier.cleaned == 0
    reset_router()


def test_reset_router_cleans_discarded_tiers(tmp_path):
    from wisp.sandbox.router import get_router, reset_router

    class CleanupTier:
        name = "cleanup"

        def __init__(self):
            self.cleaned = 0

        def is_available(self):
            return True

        def cleanup(self):
            assert get_router(str(tmp_path)) is router
            self.cleaned += 1

    router = get_router(str(tmp_path))
    tier = CleanupTier()
    router.tiers = [tier]

    reset_router(str(tmp_path))

    assert tier.cleaned == 1
    assert get_router(str(tmp_path)) is not router
    reset_router()
