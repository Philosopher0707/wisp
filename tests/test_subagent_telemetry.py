"""GH#10: monitor promotion — masking, forwarding, screen, bridge.

Covers the deltas on top of GH#8/GH#9 (whose pins live in
test_bg_notice_spinner.py and test_telemetry_buffer.py):
- producer-boundary secret masking,
- orchestrator TASK_* forwarding for the blocking fanout path,
- SubagentMonitorScreen pilot (bindings / cancel / live poll),
- /subagents bridge (guards, suspend buffering, ordered drain).
"""

from __future__ import annotations

import asyncio
import io
import sys
import types

import pytest

from wisp.cli.dispatcher import CommandResult, Dispatcher, ReplContext
from wisp.multi_agent.background import BackgroundAgentManager
from wisp.multi_agent.task import (
    EventKind,
    OrchestratorEvent,
    SubagentContract,
    SubagentResult,
)
from wisp.multi_agent.telemetry import SubagentTelemetryBuffer, mask_text


# ── Fakes ─────────────────────────────────────────────────────────────────


class FakeOrchestrator:
    def __init__(self, delay: float = 0.01) -> None:
        self.delay = delay

    async def _run_with_retry(self, contract: SubagentContract) -> SubagentResult:
        await asyncio.sleep(self.delay)
        return SubagentResult(
            task_id=contract.name, success=True, output="did the thing",
            files_changed=[], elapsed_seconds=self.delay, error=None,
            session_id="sess-fake",
        )


class FakeManager:
    """Stands in for BackgroundAgentManager for screen tests."""

    def __init__(self) -> None:
        self.telemetry = SubagentTelemetryBuffer()
        self.cancelled: list[str] = []

    def cancel(self, agent_id: str) -> dict:
        self.cancelled.append(agent_id)
        return {"ok": True, "agent_id": agent_id, "status": "cancelled"}


# ── Secret masking ────────────────────────────────────────────────────────


class TestMasking:
    def test_mask_text_redacts_tokens(self) -> None:
        masked = mask_text("deploy with ghp_abcdefghijklmnopqrstuvwx now")
        assert "ghp_abcdefghijklmnopqrstuvwx" not in masked
        assert "deploy with" in masked and "now" in masked

    def test_mask_text_passes_plain_text(self) -> None:
        assert mask_text("read auth.py and fix imports") == "read auth.py and fix imports"

    @pytest.mark.asyncio
    async def test_manager_masks_task_and_summary(self) -> None:
        mgr = BackgroundAgentManager(FakeOrchestrator())
        launched = await mgr.launch(SubagentContract(
            name="bg-mask", role="coder",
            task="rotate ghp_abcdefghijklmnopqrstuvwx immediately"))
        assert launched["ok"]
        await mgr.result(launched["agent_id"], wait_seconds=5.0)
        blob = " ".join(e.text for e in mgr.telemetry.transcript(launched["agent_id"]))
        assert "ghp_abcdefghijklmnopqrstuvwx" not in blob


# ── Re-registration ───────────────────────────────────────────────────────


class TestReregister:
    def test_terminal_snapshot_resets_for_new_run(self) -> None:
        buf = SubagentTelemetryBuffer()
        buf.register("fanout-0", label="w", role="coder")
        buf.append("fanout-0", "progress", "old run")
        buf.append("fanout-0", "settled", "ok: done")
        buf.register("fanout-0", label="w", role="coder")
        snap = buf.snapshot("fanout-0")
        assert snap is not None and snap.status == "running"
        assert buf.transcript("fanout-0") == []

    def test_live_worker_keeps_ring(self) -> None:
        buf = SubagentTelemetryBuffer()
        buf.register("fanout-0", label="w", role="coder")
        buf.append("fanout-0", "progress", "keep me")
        buf.register("fanout-0", label="w", role="coder")
        assert [e.text for e in buf.transcript("fanout-0")] == ["keep me"]


# ── Orchestrator forwarding (blocking fanout path) ────────────────────────


class TestForwarding:
    def test_forward_task_event_mapping(self) -> None:
        from wisp.multi_agent.subagent_orchestrator import _forward_task_event

        buf = SubagentTelemetryBuffer()
        _forward_task_event(buf, OrchestratorEvent(
            task_id="fanout-3", event_type=EventKind.TASK_STARTED,
            payload={"role": "coder", "description": "harden api"}))
        _forward_task_event(buf, OrchestratorEvent(
            task_id="fanout-3", event_type=EventKind.TASK_RETRY,
            payload={"attempt": 2}))
        _forward_task_event(buf, OrchestratorEvent(
            task_id="fanout-3", event_type=EventKind.TASK_COMPLETED,
            payload={"role": "coder", "output": "hardened"}))
        kinds = [e.kind for e in buf.transcript("fanout-3")]
        assert kinds == ["started", "progress", "settled"]
        assert buf.snapshot("fanout-3").status == "settled-ok"
        assert buf.snapshot("fanout-3").role == "coder"

    def test_forward_failed_and_never_raises(self) -> None:
        from wisp.multi_agent.subagent_orchestrator import _forward_task_event

        buf = SubagentTelemetryBuffer()
        _forward_task_event(buf, OrchestratorEvent(
            task_id="x", event_type=EventKind.TASK_FAILED,
            payload={"error": "boom"}))
        assert buf.snapshot("x").status == "settled-fail"
        # Garbage in, no exception out — telemetry never breaks the run.
        _forward_task_event(buf, OrchestratorEvent())
        _forward_task_event(None, OrchestratorEvent())  # type: ignore[arg-type]

    def test_orchestrator_stores_telemetry_kwarg(self) -> None:
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        buf = SubagentTelemetryBuffer()
        orch = SubagentOrchestrator(workspace=None, telemetry=buf)
        assert orch.worker_telemetry is buf
        plain = SubagentOrchestrator(workspace=None)
        assert plain.worker_telemetry is None


# ── Monitor screen (Textual pilot) ────────────────────────────────────────


def _fill(manager: FakeManager) -> None:
    manager.telemetry.register("bg-1", label="api", role="coder")
    manager.telemetry.append("bg-1", "started", "api claimed: harden")
    manager.telemetry.append("bg-1", "tool_call", "read_file auth.py")
    manager.telemetry.register("bg-2", label="docs", role="writer")
    manager.telemetry.append("bg-2", "started", "docs claimed: readme")


class TestMonitorScreen:
    @pytest.mark.asyncio
    async def test_roster_and_cycle_and_quit(self) -> None:
        from textual.widgets import DataTable

        from wisp.tui.screens.subagents import SubagentMonitorApp

        mgr = FakeManager()
        _fill(mgr)
        app = SubagentMonitorApp(mgr.telemetry, manager=mgr)
        async with app.run_test() as pilot:
            await pilot.pause(0.6)
            screen = app.screen
            assert screen.query_one("#roster", DataTable).row_count == 2
            first = screen._order[screen._sel]
            await pilot.press("]")
            assert screen._order[screen._sel] != first
            await pilot.press("[")
            assert screen._order[screen._sel] == first
            await pilot.press("q")
        assert mgr.cancelled == []  # quit cancels nothing

    @pytest.mark.asyncio
    async def test_cancel_key_calls_manager(self) -> None:
        from wisp.tui.screens.subagents import SubagentMonitorApp

        mgr = FakeManager()
        _fill(mgr)
        app = SubagentMonitorApp(mgr.telemetry, manager=mgr)
        async with app.run_test() as pilot:
            await pilot.pause(0.6)
            wid = app.screen._order[app.screen._sel]
            await pilot.press("c")
            await pilot.pause(0.2)
            await pilot.press("q")
        assert mgr.cancelled == [wid]

    @pytest.mark.asyncio
    async def test_live_tail_appends_without_manager(self) -> None:
        from textual.widgets import RichLog

        from wisp.tui.screens.subagents import SubagentMonitorApp

        buf = SubagentTelemetryBuffer()
        buf.register("solo", label="s", role="coder")
        app = SubagentMonitorApp(buf)
        async with app.run_test() as pilot:
            await pilot.pause(0.6)
            detail = app.screen.query_one("#detail", RichLog)
            n0 = len(detail.lines)
            buf.append("solo", "progress", "still going")
            await pilot.pause(0.8)
            n1 = len(app.screen.query_one("#detail", RichLog).lines)
            await pilot.press("q")
        assert n1 > n0


# ── Bridge ────────────────────────────────────────────────────────────────


def _transport_with_manager():
    from wisp.transport.cli import CLITransport

    transport = CLITransport(runtime=object())
    transport._stdout = io.StringIO()
    transport.background_agents = BackgroundAgentManager(FakeOrchestrator())
    return transport


class TestBridge:
    def test_no_manager_reports_plainly(self) -> None:
        from wisp.transport.cli import CLITransport

        transport = CLITransport(runtime=object())
        transport.background_agents = None
        assert transport.open_subagent_monitor() == "No background agents in this session."

    def test_non_tty_refuses(self) -> None:
        # pytest stdin is not a tty — deterministic guard pin.
        assert not sys.stdin.isatty()
        assert _transport_with_manager().open_subagent_monitor() == (
            "The worker monitor needs an interactive terminal.")

    def test_suspend_buffers_and_drain_replays_in_order(self, monkeypatch) -> None:
        import wisp.tui.screens.subagents as screens_mod
        from wisp.transport.cli import CLITransport

        transport = _transport_with_manager()

        async def drive(payload: dict) -> None:
            queue: asyncio.Queue = asyncio.Queue()
            transport.background_agents = types.SimpleNamespace(
                subscribe=lambda: queue, telemetry=transport.background_agents.telemetry)
            task = asyncio.create_task(
                CLITransport._watch_background(transport))
            try:
                await queue.put(payload)
                for _ in range(200):
                    if transport._pending_bg_lines or "settled" in transport._stdout.getvalue():
                        break
                    await asyncio.sleep(0.01)
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        class StubApp:
            def __init__(self, telemetry: object, manager: object = None) -> None:
                self.telemetry = telemetry

            def run(self) -> None:
                # A settle notice lands mid-monitor: must buffer, not print.
                asyncio.run(drive({
                    "type": "agent_settled", "agent_id": "a9", "label": "mid",
                    "ok": True, "summary": "done mid-monitor"}))
                assert "mid" not in transport._stdout.getvalue()
                assert len(transport._pending_bg_lines) == 1

        monkeypatch.setattr(screens_mod, "SubagentMonitorApp", StubApp)
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        status = transport.open_subagent_monitor()
        assert "Monitor closed (1 buffered notice(s) replayed)." in status
        assert "mid" in transport._stdout.getvalue()

    def test_subagents_command_registered(self) -> None:
        dispatcher = Dispatcher()
        assert "subagents" in dispatcher.names()

    def test_subagents_command_without_opener(self) -> None:
        dispatcher = Dispatcher()
        ctx = ReplContext(runtime=object(), transport=object(),
                          session={}, config={})
        result = dispatcher.dispatch(ctx, "/subagents")
        assert result is CommandResult.CONSUMED
        assert ctx.out and "unavailable" in ctx.out[0]

    def test_subagents_command_delegates(self) -> None:
        dispatcher = Dispatcher()
        transport = types.SimpleNamespace(
            open_subagent_monitor=lambda: "Monitor closed (0 buffered notice(s) replayed).")
        ctx = ReplContext(runtime=object(), transport=transport,
                          session={}, config={})
        assert dispatcher.dispatch(ctx, "/subagents") is CommandResult.CONSUMED
        assert "Monitor closed" in ctx.out[0]
