"""TDD for AgentRuntime — stateful session lifecycle manager.

Replaces: the scattered session management in WispAgentCore.
AgentRuntime owns sessions, compaction, and background runs.
WispAgentCore (stateless) owns the turn loop.
"""

import json
from unittest.mock import MagicMock

import pytest


# ── Minimal mock core for testing ──────────────────────────────────

class _MockCore:
    """A stateless core that just echoes back events."""

    def __init__(self):
        self.turns = []

    async def turn(self, session: dict, prompt: str, approval_handler=None, steering_drain=None):
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


class TestConfigFingerprint:
    """AgentRuntime must invalidate its core cache when config changes."""

    @pytest.mark.asyncio
    async def test_core_cache_invalidated_on_model_change(self, tmp_store):
        from wisp.core.runtime import AgentRuntime
        from wisp.infra.security import SecurityPolicy, PermissionMode
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.telemetry import Telemetry
        from wisp.config import WispConfig

        config = WispConfig()
        config = config.replace(model="model-a")

        rt = AgentRuntime(
            store=tmp_store,
            security=SecurityPolicy(permission_mode=PermissionMode.FULL),
            extensions=ExtensionHost(),
            telemetry=Telemetry(),
            config=config,
            core_factory=lambda: _MockCore(),
        )

        core1 = rt._get_core()
        rt.config = config.replace(model="model-b")
        core2 = rt._get_core()
        assert core1 is not core2, "core cache should invalidate when config model changes"

    @pytest.mark.asyncio
    async def test_core_cache_reused_when_config_unchanged(self, tmp_store):
        from wisp.core.runtime import AgentRuntime
        from wisp.infra.security import SecurityPolicy, PermissionMode
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.telemetry import Telemetry
        from wisp.config import WispConfig

        config = WispConfig()
        config = config.replace(model="model-a")

        rt = AgentRuntime(
            store=tmp_store,
            security=SecurityPolicy(permission_mode=PermissionMode.FULL),
            extensions=ExtensionHost(),
            telemetry=Telemetry(),
            config=config,
            core_factory=lambda: _MockCore(),
        )

        core1 = rt._get_core()
        core2 = rt._get_core()
        assert core1 is core2, "core cache should be reused when config is unchanged"


# ═══════════════════════════════════════════════════════════════════
# Delegation failure visibility: never re-answer in silence
# ═══════════════════════════════════════════════════════════════════


class _ToolCore:
    """A core that emits one tool call/result pair before finishing."""

    def __init__(self, call_event: dict | None = None):
        self.call_event = call_event or {
            "type": "tool_call",
            "name": "read_file",
            "arguments": {"path": "a.py"},
        }

    async def turn(self, session: dict, prompt: str, approval_handler=None, steering_drain=None):
        yield dict(self.call_event)
        yield {
            "type": "tool_result",
            "name": self.call_event["name"],
            "result": {"status": "ok", "data": "contents"},
            "duration_ms": 1.0,
        }
        yield {"type": "content", "text": "done"}
        yield {"type": "done"}


class TestToolCallPersistence:
    """Assistant tool_calls are persisted as JSON strings with stable ids."""

    async def _run_tool_turn(self, runtime, call_event: dict | None = None) -> dict:
        runtime.core_factory = lambda: _ToolCore(call_event)
        session = await runtime.get_or_create_session("sess-tc", "qwen", "/tmp")
        async for _ in runtime.run_turn(session, "read a.py"):
            pass
        return session

    @pytest.mark.asyncio
    async def test_persists_arguments_as_json_string(self, runtime):
        session = await self._run_tool_turn(runtime)

        assistant = next(m for m in session["messages"] if m.get("tool_calls"))
        arguments = assistant["tool_calls"][0]["function"]["arguments"]
        assert isinstance(arguments, str)
        assert json.loads(arguments) == {"path": "a.py"}

    @pytest.mark.asyncio
    async def test_string_arguments_pass_through_unchanged(self, runtime):
        call_event = {
            "type": "tool_call",
            "name": "read_file",
            "arguments": '{"path": "a.py"}',
        }
        session = await self._run_tool_turn(runtime, call_event)

        assistant = next(m for m in session["messages"] if m.get("tool_calls"))
        arguments = assistant["tool_calls"][0]["function"]["arguments"]
        assert arguments == '{"path": "a.py"}'

    @pytest.mark.asyncio
    async def test_provider_call_id_preserved_and_threaded_to_result(self, runtime):
        call_event = {
            "type": "tool_call",
            "name": "read_file",
            "arguments": {"path": "a.py"},
            "id": "call_from_provider",
        }
        session = await self._run_tool_turn(runtime, call_event)

        assistant = next(m for m in session["messages"] if m.get("tool_calls"))
        assert assistant["tool_calls"][0]["id"] == "call_from_provider"

        tool_msg = next(m for m in session["messages"] if m.get("role") == "tool")
        assert tool_msg["tool_call_id"] == "call_from_provider"

    @pytest.mark.asyncio
    async def test_generated_ids_are_unique_and_matched_to_results(self, runtime):
        first = _ToolCore().call_event
        second = dict(first, name="list_files", arguments={"dir": "."})

        class _TwoCallCore(_ToolCore):
            async def turn(self, session: dict, prompt: str, approval_handler=None, steering_drain=None):
                yield dict(first)
                yield {
                    "type": "tool_result",
                    "name": "read_file",
                    "result": {"status": "ok", "data": "one"},
                    "duration_ms": 1.0,
                }
                yield dict(second)
                yield {
                    "type": "tool_result",
                    "name": "list_files",
                    "result": {"status": "ok", "data": "two"},
                    "duration_ms": 1.0,
                }
                yield {"type": "content", "text": "done"}
                yield {"type": "done"}

        runtime.core_factory = lambda: _TwoCallCore(first)
        session = await runtime.get_or_create_session("sess-tc2", "qwen", "/tmp")
        async for _ in runtime.run_turn(session, "read then list"):
            pass

        assistants = [m for m in session["messages"] if m.get("tool_calls")]
        ids = [m["tool_calls"][0]["id"] for m in assistants]
        assert len(ids) == 2
        assert len(set(ids)) == 2, f"generated ids must be unique: {ids}"
        assert all(i.startswith("call_") for i in ids)

        results = [m for m in session["messages"] if m.get("role") == "tool"]
        assert [m["tool_call_id"] for m in results] == ids

    @pytest.mark.asyncio
    async def test_loaded_session_heals_dict_arguments(self, runtime, tmp_store):
        session = await runtime.get_or_create_session("sess-heal", "qwen", "/tmp")
        session["messages"].append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_old",
                "type": "function",
                "function": {"name": "read_file", "arguments": {"path": "a.py"}},
            }],
        })
        tmp_store.save_session(session)

        loaded = await runtime.get_or_create_session("sess-heal", "qwen", "/tmp")

        tc = loaded["messages"][-1]["tool_calls"][0]
        assert isinstance(tc["function"]["arguments"], str)
        assert json.loads(tc["function"]["arguments"]) == {"path": "a.py"}
        assert tc["id"] == "call_old"




class TestSessionScopedCores:
    """One shared core meant one shared CircuitBreaker: session A's failing
    provider opened the circuit for every other session for the whole
    recovery window. Cores must be per-session."""

    def _mk(self, factory):
        from wisp.core.runtime import AgentRuntime
        from wisp.infra.security import SecurityPolicy, PermissionMode
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.telemetry import Telemetry

        store = MagicMock()
        store.load_session.return_value = None
        return AgentRuntime(
            store=store,
            security=SecurityPolicy(permission_mode=PermissionMode.FULL),
            extensions=ExtensionHost(),
            telemetry=Telemetry(),
            core_factory=factory,
        )

    @pytest.mark.asyncio
    async def test_distinct_sessions_get_distinct_cores(self):
        calls = []

        def factory():
            core = MagicMock()
            core.side_effect = None
            calls.append(core)
            return core

        rt = self._mk(factory)
        a = rt._get_core("sess-a")
        b = rt._get_core("sess-b")
        assert a is not b, "sessions shared one core (one shared breaker)"
        again = rt._get_core("sess-a")
        assert again is a, "same session should reuse its warm core"
        # introspection slot stays separate from any session
        assert rt.get_core_provider.__self__ is rt

    @pytest.mark.asyncio
    async def test_breaker_failure_in_one_session_does_not_lock_others(self):
        """Real cores: A's provider fails past the breaker threshold; B's
        turn still executes through its own closed breaker."""
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import PermissionMode, SecurityPolicy

        class _OutageProvider:
            # One global failure (call #1), then healthy — models an outage
            # window that session A walks into and B misses.
            total_calls = 0

            def generate_stream_events(self, system_prompt, messages,
                                       tools=None, checkpoint_every=50):
                type(self).total_calls += 1
                if type(self).total_calls == 1:
                    raise RuntimeError("outage")
                yield {"type": "content", "text": "ok"}
                yield {"type": "done"}

        config = WispConfig().replace(
            circuit_breaker_failure_threshold=1,
            circuit_breaker_success_threshold=1,
            circuit_breaker_recovery_timeout=60.0,
        )

        def factory():
            return WispAgentCore(
                config=config,
                provider=_OutageProvider(),
                security=SecurityPolicy(permission_mode=PermissionMode.FULL),
                extensions=ExtensionHost(),
            )

        rt = self._mk(factory)

        async def collect(session_id):
            session = {"id": session_id, "messages": [], "workspace": "/tmp"}
            events = []
            async for ev in rt.run_turn(session, "hi"):
                events.append(ev)
            return [e.get("type") for e in events]

        # Session A: first call raises → breaker records failure.
        # Threshold=1 means A's circuit is now OPEN for 60s.
        types_a = await collect("brk-a")
        assert "error" in types_a, f"A should surface the failure: {types_a}"

        # Session B shares nothing: its own core/breaker is fresh-closed,
        # and its first provider call succeeds anyway.
        core_a = rt._get_core("brk-a")
        core_b = rt._get_core("brk-b")
        assert core_a is not core_b
        assert core_a._circuit_breaker is not core_b._circuit_breaker

        from wisp.infra.circuit_breaker import CircuitState
        assert core_a._circuit_breaker.state == CircuitState.OPEN, (
            "A's breaker should be open after its failure"
        )
        assert core_b._circuit_breaker.state == CircuitState.CLOSED

        types_b = await collect("brk-b")
        assert "content" in types_b, (
            f"session B was locked out by A's failures: {types_b}"
        )
        assert "error" not in types_b, types_b

    @pytest.mark.asyncio
    async def test_fingerprint_change_invalidates_all_session_slots(self):
        from wisp.config import WispConfig

        made = []
        rt = self._mk(lambda: made.append(1) or MagicMock())
        cfg = WispConfig()
        rt.config = cfg.replace(model="m1")
        c1a = rt._get_core("s1")
        rt._get_core("s2")
        rt.config = cfg.replace(model="m2")
        c2a = rt._get_core("s1")
        assert c2a is not c1a, "fingerprint change must rebuild session core"
        assert len(rt._session_cores) == 3  # s1@m1, s2@m1, s1@m2

    @pytest.mark.asyncio
    async def test_core_map_stays_bounded(self):
        rt = self._mk(MagicMock)
        rt.MAX_SESSION_CORES = 4
        for i in range(10):
            rt._get_core(f"burst-{i}")
        assert len(rt._session_cores) <= 4
