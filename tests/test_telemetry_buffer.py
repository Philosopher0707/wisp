"""GH#9: SubagentTelemetryBuffer + child-event wiring.

The buffer is a per-agent ring store (bounded in events AND bytes) with
replay-then-live cursors. The manager must publish lifecycle events —
including progress — so a monitor can attach without pausing the worker.
"""

from __future__ import annotations

import asyncio

import pytest

from wisp.multi_agent.background import BackgroundAgentManager
from wisp.multi_agent.task import (
    EventKind,
    OrchestratorEvent,
    SubagentContract,
    SubagentResult,
)
from wisp.multi_agent.telemetry import SubagentTelemetryBuffer


# ── Fakes ─────────────────────────────────────────────────────────────────


class FakeOrchestrator:
    def __init__(self, delay: float = 0.01, success: bool = True,
                 fire_retry: bool = False) -> None:
        self.delay = delay
        self.success = success
        self.fire_retry = fire_retry

    async def _run_with_retry(self, contract: SubagentContract) -> SubagentResult:
        if self.fire_retry and contract.progress_callback is not None:
            await contract.progress_callback(OrchestratorEvent(
                task_id=contract.name,
                event_type=EventKind.TASK_RETRY,
                payload={"attempt": 2},
            ))
        await asyncio.sleep(self.delay)
        return SubagentResult(
            task_id=contract.name,
            success=self.success,
            output="did the thing" if self.success else "",
            files_changed=[],
            elapsed_seconds=self.delay,
            error=None if self.success else "mock failure",
            session_id="sess-fake",
        )


def _contract(**overrides) -> SubagentContract:
    return SubagentContract(name="bg-tel", role="coder", task="do it", **overrides)


# ── Buffer units ──────────────────────────────────────────────────────────


class TestTelemetryBuffer:
    def test_register_append_snapshot(self) -> None:
        buf = SubagentTelemetryBuffer()
        buf.register("a1", label="w1", role="coder")
        buf.append("a1", "tool_call", "read_file x")
        buf.append("a1", "tool_result", "ok")
        snap = buf.snapshot("a1")
        assert snap is not None
        assert snap.status == "running"
        assert snap.tool_calls == 1
        assert snap.events == 2
        assert snap.elapsed >= 0

    def test_evicts_oldest_first_by_count(self) -> None:
        buf = SubagentTelemetryBuffer(max_events=3)
        buf.register("a1", label="w", role="r")
        for i in range(5):
            buf.append("a1", "progress", f"tick {i}")
        texts = [e.text for e in buf.transcript("a1")]
        assert texts == ["tick 2", "tick 3", "tick 4"]

    def test_evicts_oldest_first_by_bytes(self) -> None:
        buf = SubagentTelemetryBuffer(max_events=1000, max_bytes=100)
        buf.register("a1", label="w", role="r")
        buf.append("a1", "progress", "x" * 60)
        buf.append("a1", "progress", "y" * 60)
        texts = [e.text for e in buf.transcript("a1")]
        assert texts == ["y" * 60]

    def test_transcript_replays_last_n(self) -> None:
        buf = SubagentTelemetryBuffer()
        buf.register("a1", label="w", role="r")
        for i in range(10):
            buf.append("a1", "progress", f"tick {i}")
        assert [e.text for e in buf.transcript("a1", last_n=3)] == [
            "tick 7", "tick 8", "tick 9"]

    def test_poll_returns_only_after_cursor(self) -> None:
        buf = SubagentTelemetryBuffer()
        buf.register("a1", label="w", role="r")
        e1 = buf.append("a1", "started", "go")
        buf.append("a1", "progress", "tick")
        fresh = buf.poll("a1", after_seq=e1.seq)
        assert [e.text for e in fresh] == ["tick"]
        assert buf.poll("a1", after_seq=10**9) == []

    def test_settled_maps_status(self) -> None:
        buf = SubagentTelemetryBuffer()
        buf.register("a1", label="w", role="r")
        buf.append("a1", "settled", "ok: done")
        assert buf.snapshot("a1").status == "settled-ok"
        buf.register("a2", label="w", role="r")
        buf.append("a2", "settled", "failed: boom")
        assert buf.snapshot("a2").status == "settled-fail"

    def test_unknown_agent_is_empty(self) -> None:
        buf = SubagentTelemetryBuffer()
        assert buf.snapshot("nope") is None
        assert buf.transcript("nope") == []
        assert buf.poll("nope", after_seq=-1) == []

    def test_drop_forgets_worker(self) -> None:
        buf = SubagentTelemetryBuffer()
        buf.register("a1", label="w", role="r")
        buf.append("a1", "progress", "tick")
        buf.drop("a1")
        assert buf.snapshot("a1") is None
        assert buf.transcript("a1") == []
        assert "a1" not in buf.agents()


# ── Manager wiring ────────────────────────────────────────────────────────


class TestTelemetryWiring:
    @pytest.mark.asyncio
    async def test_launch_and_settle_publish(self) -> None:
        mgr = BackgroundAgentManager(FakeOrchestrator())
        launched = await mgr.launch(_contract())
        assert launched["ok"]
        agent_id = launched["agent_id"]
        assert any(e.kind == "started" for e in mgr.telemetry.transcript(agent_id))
        await mgr.result(agent_id, wait_seconds=5.0)
        kinds = [e.kind for e in mgr.telemetry.transcript(agent_id)]
        assert "settled" in kinds
        assert mgr.telemetry.snapshot(agent_id).status == "settled-ok"

    @pytest.mark.asyncio
    async def test_failed_settle_publishes(self) -> None:
        mgr = BackgroundAgentManager(FakeOrchestrator(success=False))
        launched = await mgr.launch(_contract())
        await mgr.result(launched["agent_id"], wait_seconds=5.0)
        assert mgr.telemetry.snapshot(launched["agent_id"]).status == "settled-fail"

    @pytest.mark.asyncio
    async def test_orchestrator_task_events_forwarded_and_callback_chained(self) -> None:
        seen: list = []

        async def prior(event: OrchestratorEvent) -> None:
            seen.append(event)

        mgr = BackgroundAgentManager(FakeOrchestrator(fire_retry=True))
        launched = await mgr.launch(_contract(progress_callback=prior))
        await mgr.result(launched["agent_id"], wait_seconds=5.0)
        # Pre-existing callback still fires (chain, not clobber).
        assert any(e.event_type == EventKind.TASK_RETRY for e in seen)
        # …and the retry lands in the buffer as progress.
        kinds = [e.kind for e in mgr.telemetry.transcript(launched["agent_id"])]
        assert "progress" in kinds

    @pytest.mark.asyncio
    async def test_send_continuation_publishes_progress(self) -> None:
        mgr = BackgroundAgentManager(FakeOrchestrator())
        launched = await mgr.launch(_contract())
        agent_id = launched["agent_id"]
        await mgr.result(agent_id, wait_seconds=5.0)
        sent = await mgr.send(agent_id, "and another thing")
        assert sent["ok"]
        await mgr.result(agent_id, wait_seconds=5.0)
        kinds = [e.kind for e in mgr.telemetry.transcript(agent_id)]
        assert "progress" in kinds

    @pytest.mark.asyncio
    async def test_prune_drops_telemetry(self) -> None:
        mgr = BackgroundAgentManager(FakeOrchestrator(), max_finished=1)
        first = await mgr.launch(_contract())
        await mgr.result(first["agent_id"], wait_seconds=5.0)
        second = await mgr.launch(_contract())
        await mgr.result(second["agent_id"], wait_seconds=5.0)
        assert mgr.prune() == 1
        assert mgr.telemetry.snapshot(first["agent_id"]) is None
        assert mgr.telemetry.snapshot(second["agent_id"]) is not None
