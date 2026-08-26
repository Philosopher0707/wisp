"""Tests for live subagent event streaming into the REPL.

Covers the OrchestratorEvent → AgentEvent conversion, mode-aware
rendering, the executor's queue-interleaved spawn stream, and /spawn's
wired progress + composition-preferred orchestrator.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from wisp.config import WispConfig
from wisp.core.events import EventType, subagent
from wisp.multi_agent.task import EventKind, OrchestratorEvent
from wisp.tool_executor import ToolExecutor, orchestrator_event_to_agent_event
from wisp.transport.renderer import render_subagent_status
from wisp.terminal_width import OutputMode, get_output_mode, set_output_mode


# ═══════════════════════════════════════════════════════════════════
# Event factory
# ═══════════════════════════════════════════════════════════════════


class TestSubagentFactory:
    def test_basic_fields(self):
        ev = subagent(kind="task_started", name="spawn-coder", role="coder", detail="fix tests")
        assert ev.type == EventType.SUBAGENT
        assert ev.data["kind"] == "task_started"
        assert ev.data["name"] == "spawn-coder"
        assert ev.data["role"] == "coder"
        assert ev.data["detail"] == "fix tests"

    def test_extra_keys_ride_along(self):
        ev = subagent(kind="task_completed", elapsed=3.2)
        assert ev.data["elapsed"] == 3.2

    def test_describe_event_type(self):
        from wisp.core.events import describe_event_type
        assert describe_event_type(EventType.SUBAGENT) == "Subagent lifecycle update"


# ═══════════════════════════════════════════════════════════════════
# OrchestratorEvent conversion
# ═══════════════════════════════════════════════════════════════════


class TestConversion:
    def test_started_carries_role_and_task(self):
        orch = OrchestratorEvent(
            task_id="spawn-coder",
            event_type=EventKind.TASK_STARTED,
            payload={"role": "coder", "description": "fix failing tests"},
        )
        ev = orchestrator_event_to_agent_event(orch)
        assert ev.type == EventType.SUBAGENT
        assert ev.data["kind"] == "task_started"
        assert ev.data["name"] == "spawn-coder"
        assert ev.data["role"] == "coder"
        assert ev.data["detail"] == "fix failing tests"

    def test_completed_formats_elapsed_and_files(self):
        orch = OrchestratorEvent(
            task_id="t",
            event_type=EventKind.TASK_COMPLETED,
            payload={"elapsed": 4.25, "files_changed": ["a.py", "b.py"], "output": "done"},
        )
        ev = orchestrator_event_to_agent_event(orch)
        assert "4.2s" in ev.data["detail"]
        assert "2 files" in ev.data["detail"]
        assert ev.data["elapsed"] == 4.25

    def test_failed_maps_error(self):
        orch = OrchestratorEvent(
            task_id="t",
            event_type=EventKind.TASK_FAILED,
            payload={"error": "model unreachable"},
        )
        ev = orchestrator_event_to_agent_event(orch)
        assert ev.data["kind"] == "task_failed"
        assert ev.data["detail"] == "model unreachable"
        assert ev.data["error"] == "model unreachable"

    def test_retry_formats_attempt_and_backoff(self):
        orch = OrchestratorEvent(
            task_id="t",
            event_type=EventKind.TASK_RETRY,
            payload={"retry": 2, "backoff_seconds": 4.0},
        )
        ev = orchestrator_event_to_agent_event(orch)
        assert "retry #2" in ev.data["detail"]
        assert "4s" in ev.data["detail"]

    def test_survives_missing_payload(self):
        orch = OrchestratorEvent(task_id="t", event_type=EventKind.TASK_PROGRESS)
        ev = orchestrator_event_to_agent_event(orch)
        assert ev.type == EventType.SUBAGENT
        assert ev.data["detail"] == ""

    def test_non_dict_payload_does_not_crash(self):
        orch = OrchestratorEvent(task_id="t")
        orch.payload = None
        ev = orchestrator_event_to_agent_event(orch)
        assert ev.type == EventType.SUBAGENT


# ═══════════════════════════════════════════════════════════════════
# Mode-aware rendering
# ═══════════════════════════════════════════════════════════════════


class TestRendering:
    @pytest.fixture
    def started(self):
        return subagent(kind="task_started", name="s", role="coder", detail="fix tests")

    @pytest.fixture
    def completed(self):
        return subagent(kind="task_completed", role="coder", detail="1.0s")

    @pytest.fixture
    def restore_mode(self):
        old = get_output_mode()
        yield
        set_output_mode(old)

    def test_minimal_silent(self, started, restore_mode):
        set_output_mode(OutputMode.MINIMAL)
        assert render_subagent_status(started) == ""

    def test_accessible_prefixed(self, started, restore_mode):
        set_output_mode(OutputMode.ACCESSIBLE)
        out = render_subagent_status(started)
        assert "[SUBAGENT]" in out and "Started" in out

    def test_unicode_symbols(self, started, completed, restore_mode):
        set_output_mode(OutputMode.UNICODE)
        assert "🧬" in render_subagent_status(started)
        assert "✓" in render_subagent_status(completed)

    def test_ascii_symbols(self, started, completed, restore_mode):
        set_output_mode(OutputMode.ASCII)
        assert ">" in render_subagent_status(started)
        assert "+" in render_subagent_status(completed)

    def test_unknown_kind_returns_none(self):
        assert render_subagent_status(subagent(kind="mystery")) is None


# ═══════════════════════════════════════════════════════════════════
# Executor streams subagent events during spawn
# ═══════════════════════════════════════════════════════════════════


class _StubOrchestrator:
    """Replays scripted progress events, then returns a canned result."""

    def __init__(self, events=None, delay: float = 0.0, result: dict | None = None):
        self._events = events or []
        self._delay = delay
        self._result = result

    async def _run_with_retry(self, contract):
        for ev in self._events:
            if contract.progress_callback is not None:
                await asyncio.sleep(self._delay)
                contract.progress_callback(ev)
        r = dict(self._result or {})
        return SimpleNamespace(**r)


def _fake_result() -> dict:
    return {
        "task_id": "spawn-coder",
        "success": True,
        "output": "did the thing",
        "tool_calls": [],
        "files_changed": ["x.py"],
        "elapsed_seconds": 1.5,
        "tokens_used": 10,
        "timed_out": False,
        "iterations_used": 2,
        "error": None,
    }


class TestExecutorStreaming:
    @staticmethod
    async def _collect(executor, name, args):
        events = []
        async for ev in executor.execute(name, args, "/tmp"):
            etype = getattr(ev, "type", None)
            if etype is None and isinstance(ev, dict):
                etype = ev.get("type", "")
            elif not isinstance(etype, str):
                etype = str(getattr(etype, "value", etype))
            events.append((str(etype), ev))
        return events

    @pytest.mark.asyncio
    async def test_spawn_stream_yields_lifecycle_then_result(self):
        orch = _StubOrchestrator(
            events=[
                OrchestratorEvent("spawn-coder", EventKind.TASK_STARTED, {"role": "coder"}),
                OrchestratorEvent("spawn-coder", EventKind.TASK_COMPLETED,
                                  {"elapsed": 1.5, "files_changed": ["x.py"]}),
            ],
            result=_fake_result(),
        )
        executor = ToolExecutor(config=WispConfig(), subagent_orchestrator=orch)

        stream = await self._collect(executor, "spawn", {"task": "do it"})
        kinds = [etype for etype, _ in stream]

        assert sum(1 for k in kinds if k == EventType.SUBAGENT.value) == 2
        assert EventType.TOOL_RESULT.value in kinds
        first_sub = kinds.index(EventType.SUBAGENT.value)
        tool_res = kinds.index(EventType.TOOL_RESULT.value)
        assert first_sub < tool_res, "lifecycle events must precede the final result"

    @pytest.mark.asyncio
    async def test_result_payload_survives_streaming(self):
        orch = _StubOrchestrator(result=_fake_result())
        executor = ToolExecutor(config=WispConfig(), subagent_orchestrator=orch)

        stream = await self._collect(executor, "spawn", {"task": "x"})
        tool_results = [ev for etype, ev in stream if etype == EventType.TOOL_RESULT.value]
        assert len(tool_results) == 1
        data = getattr(tool_results[0], "data", {})
        payload = data.get("result") or data
        text = json.dumps(payload) if not isinstance(payload, str) else payload
        assert "did the thing" in text

    @pytest.mark.asyncio
    async def test_fanout_wires_callbacks_to_all_contracts(self):
        captured: list[list] = []

        class _ParallelOrch:
            async def run_parallel(self, contracts, max_concurrent=4):
                captured.append([c.progress_callback for c in contracts])
                return [SimpleNamespace(**_fake_result()) for _ in contracts]

        executor = ToolExecutor(config=WispConfig(), subagent_orchestrator=_ParallelOrch())
        args = {"tasks": [{"task": "a", "role": "coder"}, {"task": "b", "role": "tester"}],
                "mode": "blocking"}
        stream = await self._collect(executor, "fanout", args)

        assert captured and all(cb is not None for cb in captured[0]), (
            "every fanout contract must carry a progress callback"
        )
        assert any(etype == EventType.TOOL_RESULT.value for etype, _ in stream)


# ═══════════════════════════════════════════════════════════════════
# /spawn command wiring
# ═══════════════════════════════════════════════════════════════════


class TestSpawnCommandWiring:
    def test_prefers_runtime_orchestrator(self):
        import wisp.commands as commands_module

        wired = MagicMock()
        agent = MagicMock()
        agent.runtime.orchestrator = wired

        assert commands_module._get_orchestrator(agent) is wired

    @pytest.mark.asyncio
    async def test_falls_back_to_fresh_orchestrator(self, tmp_path):
        from wisp.commands import _get_orchestrator
        from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator

        agent = MagicMock(spec=["config"])
        agent.config.workspace = str(tmp_path)
        agent.config.subagent_pool_size = 4

        orch = _get_orchestrator(agent)
        assert isinstance(orch, SubagentOrchestrator)

    def test_spawn_prints_rendered_progress(self, monkeypatch, capsys, tmp_path):
        from wisp.commands import cmd_spawn
        from wisp.multi_agent.task import SubagentContract

        received: list[SubagentContract] = []

        class _CmdOrch:
            async def run(self, contract):
                received.append(contract)
                if contract.progress_callback is not None:
                    contract.progress_callback(
                        OrchestratorEvent("spawn", EventKind.TASK_STARTED, {"role": "generalist"})
                    )
                return SimpleNamespace(
                    success=True, timed_out=False, elapsed_seconds=0.4,
                    iterations_used=1, output="all done",
                )

        agent = MagicMock(spec=["runtime", "messages", "session", "config"])
        agent.runtime.orchestrator = _CmdOrch()

        cmd_spawn(agent, "write a haiku about queues")

        out = capsys.readouterr().out
        assert received and received[0].progress_callback is not None, (
            "/spawn must wire a progress callback onto the contract"
        )
        assert "🧬" in out or "[SUBAGENT]" in out or "Started" in out, out
        assert "all done" in out
