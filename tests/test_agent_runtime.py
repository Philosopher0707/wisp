"""TDD for AgentRuntime — stateful session lifecycle manager.

Replaces: the scattered session management in WispAgentCore.
AgentRuntime owns sessions, compaction, and background runs.
WispAgentCore (stateless) owns the turn loop.
"""

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Minimal mock core for testing ──────────────────────────────────

class _MockCore:
    """A stateless core that just echoes back events."""

    def __init__(self):
        self.turns = []

    async def turn(self, session: dict, prompt: str):
        self.turns.append((session["id"], prompt))
        yield {"type": "content", "text": f"echo: {prompt}"}
        yield {"type": "done"}


# ── Test fixtures ──────────────────────────────────────────────────

@pytest.fixture
def tmp_store(tmp_path):
    from wisp.infra.store import UnifiedStore
    return UnifiedStore(tmp_path / "test.db")


@pytest.fixture
def runtime(tmp_store):
    from wisp.core.runtime import AgentRuntime
    from wisp.infra.security import SecurityPolicy, PermissionMode
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.telemetry import Telemetry

    return AgentRuntime(
        store=tmp_store,
        security=SecurityPolicy(permission_mode=PermissionMode.FULL),
        extensions=ExtensionHost(),
        telemetry=Telemetry(),
        core_factory=lambda: _MockCore(),
    )


# ═══════════════════════════════════════════════════════════════════
# 1. Session lifecycle
# ═══════════════════════════════════════════════════════════════════

class TestSessionLifecycle:
    """AgentRuntime owns session creation, loading, and saving."""

    @pytest.mark.asyncio
    async def test_creates_session_if_none_exists(self, runtime, tmp_store):
        session = await runtime.get_or_create_session(
            session_id="new-sess",
            model="qwen2.5-coder",
            workspace="/tmp/test",
        )
        assert session["id"] == "new-sess"
        assert session["model"] == "qwen2.5-coder"

    @pytest.mark.asyncio
    async def test_loads_existing_session(self, runtime, tmp_store):
        await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        loaded = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        assert loaded["id"] == "sess-1"

    @pytest.mark.asyncio
    async def test_saves_session_after_turn(self, runtime, tmp_store):
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        async for _ in runtime.run_turn(session, "hello"):
            pass

        loaded = tmp_store.load_session("sess-1")
        assert loaded is not None
        assert len(loaded["messages"]) > 0


# ═══════════════════════════════════════════════════════════════════
# 2. Turn execution
# ═══════════════════════════════════════════════════════════════════

class TestTurnExecution:
    """Turns delegate to the stateless core."""

    @pytest.mark.asyncio
    async def test_turn_yields_events(self, runtime):
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        events = []
        async for event in runtime.run_turn(session, "hello"):
            events.append(event)

        assert len(events) == 2
        assert events[0]["text"] == "echo: hello"
        assert events[1]["type"] == "done"

    @pytest.mark.asyncio
    async def test_turn_adds_messages_to_session(self, runtime):
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        async for _ in runtime.run_turn(session, "hello"):
            pass

        assert len(session["messages"]) == 2  # user + assistant
        assert session["messages"][0]["role"] == "user"
        assert session["messages"][1]["role"] == "assistant"


# ═══════════════════════════════════════════════════════════════════
# 3. Compaction
# ═══════════════════════════════════════════════════════════════════

class TestCompaction:
    """Sessions are compacted when they grow too long."""

    @pytest.mark.asyncio
    async def test_compaction_reduces_message_count(self, runtime):
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        # Add many messages
        for i in range(20):
            session["messages"].append({"role": "user", "content": f"msg{i}"})
            session["messages"].append({"role": "assistant", "content": f"reply{i}"})

        await runtime.maybe_compact(session, max_messages=10)

        assert len(session["messages"]) <= 10
        assert len(session["compaction_history"]) == 1

    @pytest.mark.asyncio
    async def test_no_compaction_when_under_limit(self, runtime):
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        session["messages"].append({"role": "user", "content": "hi"})

        await runtime.maybe_compact(session, max_messages=100)

        assert len(session["messages"]) == 1
        assert len(session["compaction_history"]) == 0


# ═══════════════════════════════════════════════════════════════════
# 4. Background runs
# ═══════════════════════════════════════════════════════════════════

class TestBackgroundRuns:
    """Background runs are persisted and trackable."""

    @pytest.mark.asyncio
    async def test_start_background_run_creates_run(self, runtime, tmp_store):
        run_id = await runtime.start_background_run(
            session_id="sess-1",
            prompt="refactor auth",
            model="qwen",
        )
        assert run_id.startswith("bg-")

        loaded = tmp_store.load_run(run_id)
        assert loaded is not None
        assert loaded["prompt"] == "refactor auth"
        assert loaded["status"] == "pending"

    @pytest.mark.asyncio
    async def test_background_run_updates_status(self, runtime, tmp_store):
        run_id = await runtime.start_background_run("sess-1", "test", "qwen")
        await runtime.update_run_status(run_id, "running")

        loaded = tmp_store.load_run(run_id)
        assert loaded["status"] == "running"

    @pytest.mark.asyncio
    async def test_list_background_runs(self, runtime, tmp_store):
        await runtime.start_background_run("sess-1", "task1", "qwen")
        await runtime.start_background_run("sess-1", "task2", "qwen")

        runs = await runtime.list_background_runs("sess-1")
        assert len(runs) == 2


# ═══════════════════════════════════════════════════════════════════
# 5. Telemetry integration
# ═══════════════════════════════════════════════════════════════════

class TestTelemetryIntegration:
    """Runtime records metrics via injected Telemetry."""

    @pytest.mark.asyncio
    async def test_turn_records_latency(self, runtime):
        session = await runtime.get_or_create_session("sess-1", "qwen", "/tmp")
        async for _ in runtime.run_turn(session, "hello"):
            pass

        metrics = runtime.telemetry.metrics()
        assert metrics["turns_total"] == 1
        assert metrics["turn_latency_ms_avg"] >= 0
