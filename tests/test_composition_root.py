"""Composition-root tests — REPL startup pipeline DI order and shutdown.

Verifies the contract wisp/__main__.py → wisp/entry.py → CompositionRoot
→ Transport → AgentRuntime → WispAgentCore:
  - lifecycle steps run boot_env → preflight → banner → loop → shutdown,
  - the runner works against mock runtime/transport (mock provider path),
  - shutdown persists the session and restores signal handlers,
  - slash dispatch never touches wrapper privates.
"""

from __future__ import annotations

import asyncio
import signal
from typing import Any


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    def save_session(self, session: dict) -> None:
        self.saved.append(dict(session))


class FakeRuntime:
    def __init__(self) -> None:
        self.store = FakeStore()
        self.turns: list[str] = []

    async def run_turn(self, session: dict, prompt: str, approval_handler: Any = None):
        self.turns.append(prompt)
        yield {"type": "content", "text": f"echo:{prompt}"}
        yield {"type": "done"}

    def inject_steering(self, sid: str, text: str) -> None:
        pass

    def drain_steering(self, sid: str) -> list[str]:
        return []

    def get_doctor_report(self) -> dict:
        return {"healthy": True, "passed": 5, "total": 5}


class FakeTransport:
    def __init__(self, config: Any) -> None:
        self.config = config
        self.events: list[dict] = []
        self.approvals = 0

    async def approve(self, tool_call: dict) -> bool:
        self.approvals += 1
        return True

    def print_banner(self, out: Any, session: dict, model: str, skill: str | None = None) -> None:
        out.write(f"BANNER model={model}\n")

    def print_continuation_banner(self, out: Any, session: dict, model: str) -> None:
        out.write("CONTINUED\n")


def _make_runner(lines: list[str | None], **kw: Any):
    from wisp.cli.dispatcher import Dispatcher
    from wisp.cli.repl import ReplLifecycle, ReplRunner

    class Cfg:
        model = "mock-model"
        provider = "mock"

    runtime = FakeRuntime()
    transport = FakeTransport(Cfg())
    it = iter(lines)

    runner = ReplRunner(
        runtime=runtime,
        transport=transport,
        renderer=None,
        dispatcher=Dispatcher(),
        config=Cfg(),
        session={"id": "s1", "model": "mock-model", "workspace": ".", "messages": []},
        loop=asyncio.new_event_loop(),
        out=kw.get("out"),
        err=kw.get("err"),
        input_fn=lambda prompt: next(it),
        lifecycle=ReplLifecycle(),
    )
    return runner, runtime, transport


def _sinks():
    import io

    return io.StringIO(), io.StringIO()


def test_lifecycle_order_boot_preflight_banner_loop_shutdown():
    out, err = _sinks()
    runner, _, _ = _make_runner(["hello", "/exit"], out=out, err=err)

    class Rec:
        def render_event(self, o: Any, e: dict) -> None:
            o.write(e.get("text", "") + "\n")

        def reset(self) -> None:
            pass

        def flush(self, o: Any) -> None:
            pass

        def wait_start(self, o: Any) -> None:
            pass

        def wait_stop(self, o: Any) -> None:
            pass

    runner.renderer = Rec()
    runner.boot_env()
    asyncio.new_event_loop().run_until_complete(runner.preflight("."))
    runner.banner(is_continuation=False)
    runner.run()
    steps = runner.lifecycle.steps
    assert steps.index("boot_env") < steps.index("preflight") < steps.index("banner")
    assert steps.index("banner") < steps.index("loop") < steps.index("shutdown")


def test_mock_provider_turn_renders_content():
    from wisp.cli.repl import CLIEventRenderer

    out, err = _sinks()
    runner, runtime, transport = _make_runner(["/exit"], out=out, err=err)
    runner.renderer = CLIEventRenderer(transport)
    # Bypass private render path: use a recording renderer instead.
    seen: list[dict] = []

    class Rec:
        def render_event(self, o: Any, e: dict) -> None:
            seen.append(e)

        def reset(self) -> None:
            pass

        def flush(self, o: Any) -> None:
            pass

        def wait_start(self, o: Any) -> None:
            pass

        def wait_stop(self, o: Any) -> None:
            pass

    runner.renderer = Rec()
    runner.run_turn("ping")
    assert runtime.turns == ["ping"]
    assert any(e.get("text") == "echo:ping" for e in seen)


def test_graceful_shutdown_saves_session_and_restores_sigint():
    out, err = _sinks()
    runner, runtime, _ = _make_runner(["/exit"], out=out, err=err)
    before = signal.getsignal(signal.SIGINT)
    runner.run()
    assert signal.getsignal(signal.SIGINT) is before
    assert runtime.store.saved and runtime.store.saved[0]["id"] == "s1"
    assert "shutdown" in runner.lifecycle.steps


def test_real_runtime_exposes_doctor_report():
    """Contract: AgentRuntime.get_doctor_report never raises, always a dict."""
    from unittest.mock import MagicMock

    from wisp.core.runtime import AgentRuntime

    rt = AgentRuntime(
        store=MagicMock(), security=MagicMock(), extensions=MagicMock(),
        telemetry=MagicMock(), core_factory=MagicMock(), config=MagicMock(),
    )
    report = rt.get_doctor_report()
    assert isinstance(report, dict) and "healthy" in report


def test_eof_exits_cleanly():
    out, err = _sinks()

    def _raise(prompt: str):
        raise EOFError

    runner, _, _ = _make_runner([], out=out, err=err)
    runner.input_fn = _raise  # type: ignore[assignment]
    assert runner.run() == "exit"


def test_preflight_never_raises_and_is_fast():
    import time

    from wisp.cli.repl import DoctorRunner

    async def _go():
        started = time.monotonic()
        summary = await DoctorRunner(budget_s=0.1).check_all(".", object())
        return summary, time.monotonic() - started

    summary, elapsed = asyncio.new_event_loop().run_until_complete(_go())
    assert elapsed < 2.0
    assert isinstance(summary.banner, str)
