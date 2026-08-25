"""TDD for AgentRuntime — stateful session lifecycle manager.

Replaces: the scattered session management in WispAgentCore.
AgentRuntime owns sessions, compaction, and background runs.
WispAgentCore (stateless) owns the turn loop.
"""

import json

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


class TestDelegationFailureVisibility:
    """C4/C5: total subagent failure must be visible, not silent."""

    def _runtime_with_orchestrator(self, runtime, orchestrator):
        runtime.orchestrator = orchestrator
        return runtime

    def test_all_failed_returns_marker_not_none(self, runtime):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from wisp.multi_agent.task import SubagentResult

        signal = MagicMock()
        signal.should_delegate = True
        signal.confidence = 0.9
        signal.reason = "research request"
        signal.suggested_contracts = [{"name": "r", "task": "look it up"}]

        analyzer = MagicMock()
        analyzer.analyze_with_llm = AsyncMock(return_value=signal)

        failed = SubagentResult(
            task_id="t1", success=False, output="", error="child blew up",
            files_changed=[], iterations_used=0,
        )
        orch = MagicMock()
        orch.run_parallel = AsyncMock(return_value=[failed])
        runtime.orchestrator = orch

        cfg = MagicMock()
        cfg.delegation_threshold = 0.18
        with patch(
            "wisp.multi_agent.delegation.get_delegation_analyzer",
            return_value=analyzer,
        ):
            result = asyncio.run(
                runtime._maybe_delegate(
                    "research something thoroughly please",
                    {"id": "s1", "messages": []}, cfg,
                )
            )
        assert result is not None, "failure must flow as marker, not None"
        assert result.startswith("[DELEGATION FAILED]")
        assert "child blew up" in result

    def test_no_contracts_still_returns_none(self, runtime):
        import asyncio
        from unittest.mock import MagicMock

        orch = MagicMock()
        orch.analyze = MagicMock(return_value=[])
        runtime.orchestrator = orch

        result = asyncio.run(
            runtime._maybe_delegate(
                "hello", {"id": "s1", "messages": []}, MagicMock()
            )
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════
# Live delegation streaming: child lifecycle renders DURING delegation
# ═══════════════════════════════════════════════════════════════════


class TestDelegationLiveStreaming:
    """Auto-delegated children must be visible while they run."""

    def test_lifecycle_events_stream_before_engine_turn(self, runtime):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from wisp.multi_agent.task import SubagentResult

        signal = MagicMock()
        signal.should_delegate = True
        signal.confidence = 0.9
        signal.reason = "research request"
        signal.suggested_contracts = [{"name": "r", "task": "look it up"}]

        analyzer = MagicMock()
        analyzer.analyze_with_llm = AsyncMock(return_value=signal)

        captured_callbacks: list = []

        async def fake_run_parallel(contracts, max_concurrent=3):
            # Fire the real lifecycle the runner would emit.
            import asyncio as _aio
            for c in contracts:
                assert c.progress_callback is not None, (
                    "delegation must wire progress callbacks for streaming"
                )
                captured_callbacks.append(c.progress_callback)
                c.progress_callback(_orch_event("task_started", c.name))
                await _aio.sleep(0.05)
                c.progress_callback(_orch_event("task_completed", c.name))
            return [SubagentResult(
                task_id="r", success=True, output="found it",
                files_changed=[], iterations_used=1,
            )]

        def _orch_event(kind, name):
            from wisp.multi_agent.task import OrchestratorEvent
            payload = {"role": "researcher"}
            if kind == "task_completed":
                payload["elapsed"] = 1.2
            return OrchestratorEvent(
                task_id=name, event_type=kind, payload=payload,
            )

        orch = MagicMock()
        orch.run_parallel = fake_run_parallel
        runtime.orchestrator = orch

        class _Core:
            config = MagicMock()
            config.delegation_threshold = 0.18
            async def turn(self, session, prompt, approval_handler=None, steering_drain=None):
                yield {"type": "content", "text": "answer"}
                yield {"type": "done"}

        with patch.object(runtime, "_get_core", return_value=_Core()), \
             patch(
                 "wisp.multi_agent.delegation.get_delegation_analyzer",
                 return_value=analyzer,
             ):
            events = []
            async def _drive():
                async for ev in runtime.run_turn({"id": "s", "messages": []}, "research something thoroughly please"):
                    events.append(ev)
            asyncio.run(_drive())

        types = [e.get("type") for e in events]
        assert "subagent" in types, f"no subagent events in stream: {types}"

        # Announce precedes lifecycle; engine output follows.
        assert types.index("system") < types.index("subagent")
        first_sys = next(e for e in events if e.get("type") == "system")
        assert "Auto-delegating" in first_sys["message"]

        started = [e for e in events if e.get("type") == "subagent"]
        kinds = [e["kind"] for e in started]
        assert "task_started" in kinds and "task_completed" in kinds
        assert started[0]["role"] == "researcher"
        assert types[-1] == "done"

    def test_failure_still_announces_then_fails(self, runtime):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from wisp.multi_agent.task import SubagentResult

        signal = MagicMock()
        signal.should_delegate = True
        signal.confidence = 0.9
        signal.reason = "research request"
        signal.suggested_contracts = [{"name": "r", "task": "x"}]

        analyzer = MagicMock()
        analyzer.analyze_with_llm = AsyncMock(return_value=signal)

        failed = SubagentResult(
            task_id="r", success=False, output="", error="stalled",
            files_changed=[], iterations_used=0,
        )
        orch = MagicMock()
        async def _fail_parallel(contracts, max_concurrent=3):
            return [failed]
        orch.run_parallel = _fail_parallel
        runtime.orchestrator = orch

        class _Core:
            config = MagicMock()
            config.delegation_threshold = 0.18
            async def turn(self, session, prompt, approval_handler=None, steering_drain=None):
                yield {"type": "content", "text": "direct answer"}
                yield {"type": "done"}

        with patch.object(runtime, "_get_core", return_value=_Core()), \
             patch(
                 "wisp.multi_agent.delegation.get_delegation_analyzer",
                 return_value=analyzer,
             ):
            events = []
            async def _drive():
                async for ev in runtime.run_turn({"id": "s", "messages": []}, "research something thoroughly please"):
                    events.append(ev)
            asyncio.run(_drive())

        sys_msgs = [e["message"] for e in events if e.get("type") == "system"]
        assert any("Auto-delegating" in m for m in sys_msgs)
        assert any("delegation failed" in m for m in sys_msgs)


class TestDelegationClassifyBounded:
    """A stalled classifier must not hang the turn before it starts."""

    def test_hanging_classify_skips_delegation_quickly(self, runtime):
        import asyncio
        import time as _time
        from unittest.mock import MagicMock, patch

        analyzer = MagicMock()

        async def _hang(prompt_fn):
            # The real analyze_with_llm awaits prompt_fn() for the LLM call.
            await asyncio.sleep(30)
            s = MagicMock()
            s.should_delegate = False
            return s

        async def fake_analyze(prompt, llm_classify):
            text = await llm_classify("classify me")
            return await _hang(text)

        analyzer.analyze_with_llm = fake_analyze

        class _HangingProvider:
            def generate_stream_events(self, messages=None, **kw):
                import time as t
                t.sleep(30)
                yield {"type": "done"}

        class _Core:
            config = MagicMock()
            config.delegation_threshold = 0.45

            class provider:
                pass

        cfg = MagicMock()
        cfg.delegation_threshold = 0.45
        runtime._get_core = lambda: _Core()
        _Core.provider = type("P", (), {})()
        _Core.provider.generate_stream_events = lambda self, messages=None: iter([])

        with patch(
            "wisp.multi_agent.delegation.get_delegation_analyzer",
            return_value=analyzer,
        ):
            t0 = _time.monotonic()
            result = asyncio.run(runtime._maybe_delegate(
                "research something thoroughly please",
                {"id": "s", "messages": []}, cfg,
            ))
            elapsed = _time.monotonic() - t0

        assert result is None, "timeout must skip delegation"
        assert elapsed < 5, f"classify timeout not applied: {elapsed:.1f}s"


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


# ═══════════════════════════════════════════════════════════════════
# Tool-call persistence: history must survive a persist/reload round
# trip without breaking OpenAI-style providers.
# ═══════════════════════════════════════════════════════════════════


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


class TestDelegationWaitIndicator:
    """Slow delegation analysis must render something within ~1s."""

    def test_slow_classify_emits_analyzing_event(self, runtime):
        import asyncio
        import time as _time
        from unittest.mock import MagicMock, patch

        signal = MagicMock()
        signal.should_delegate = False

        async def slow_analyze(prompt, llm_classify):
            await asyncio.sleep(1.6)  # past the 1s indicator threshold
            return signal

        analyzer = MagicMock()
        analyzer.analyze_with_llm = slow_analyze
        runtime.orchestrator = None  # gate: orchestrator must exist; use dummy
        runtime.orchestrator = MagicMock()

        class _Core:
            config = MagicMock()
            config.delegation_threshold = 0.45

        with patch.object(runtime, "_get_core", return_value=_Core()), \
             patch(
                 "wisp.multi_agent.delegation.get_delegation_analyzer",
                 return_value=analyzer,
             ):
            events = []
            t0 = _time.monotonic()

            async def _drive():
                async for ev in runtime.run_turn(
                    {"id": "s", "messages": []},
                    "research something thoroughly please",
                ):
                    events.append(ev)

            asyncio.run(_drive())
            elapsed = _time.monotonic() - t0

        sys_msgs = [e for e in events if e.get("type") == "system"]
        assert any(
            "Analyzing" in str(e.get("message", "")) for e in sys_msgs
        ), f"no analyzing indicator: {sys_msgs}"
        assert elapsed < 5
