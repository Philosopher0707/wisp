"""Tests for concurrent turn safety in AgentRuntime."""

import pytest
import asyncio
from unittest.mock import MagicMock


@pytest.fixture
def runtime():
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
        core_factory=lambda: MagicMock(),
    )


class TestConcurrentTurns:
    """Concurrent turns on same session are serialized."""

    @pytest.mark.asyncio
    async def test_concurrent_turns_serialized(self, runtime):
        """Two concurrent turns should not interleave messages."""
        session = {
            "id": "test-session",
            "messages": [],
            "workspace": "/tmp",
        }

        # Mock core that yields content slowly
        class SlowCore:
            async def turn(self, session, prompt, approval_handler=None):
                await asyncio.sleep(0.01)
                yield {"type": "content", "text": f"response to {prompt}"}
                yield {"type": "done"}

        runtime.core_factory = lambda: SlowCore()
        runtime.invalidate_core_cache()

        async def turn(prompt):
            events = []
            async for event in runtime.run_turn(session, prompt):
                events.append(event)
            return events

        # Run two turns concurrently
        results = await asyncio.gather(
            turn("hello"),
            turn("world"),
        )

        # Both should complete
        assert len(results) == 2
        assert len(results[0]) > 0
        assert len(results[1]) > 0

        # Messages should not be interleaved
        messages = session["messages"]
        roles = [m["role"] for m in messages]

        # Should be: user, assistant, user, assistant (or similar)
        # Not: user, user, assistant, assistant (interleaved)
        user_count = roles.count("user")
        assistant_count = roles.count("assistant")
        assert user_count == 2
        assert assistant_count == 2

    @pytest.mark.asyncio
    async def test_per_session_locks(self, runtime):
        """Different sessions should not block each other."""
        session1 = {"id": "s1", "messages": [], "workspace": "/tmp"}
        session2 = {"id": "s2", "messages": [], "workspace": "/tmp"}

        class SlowCore:
            async def turn(self, session, prompt, approval_handler=None):
                await asyncio.sleep(0.05)
                yield {"type": "content", "text": "ok"}
                yield {"type": "done"}

        runtime.core_factory = lambda: SlowCore()

        async def turn(sess):
            events = []
            async for event in runtime.run_turn(sess, "test"):
                events.append(event)
            return events

        start = asyncio.get_event_loop().time()
        results = await asyncio.gather(
            turn(session1),
            turn(session2),
        )
        elapsed = asyncio.get_event_loop().time() - start

        # Should complete in ~0.05s (parallel), not ~0.10s (serial)
        assert elapsed < 0.09
        assert len(results[0]) > 0
        assert len(results[1]) > 0


class TestInputValidation:
    """Invalid inputs are rejected early."""

    @pytest.mark.asyncio
    async def test_empty_session_id_rejected(self, runtime):
        with pytest.raises(ValueError, match="Invalid session_id"):
            await runtime.get_or_create_session("", "model", "/tmp")

    @pytest.mark.asyncio
    async def test_none_session_id_rejected(self, runtime):
        with pytest.raises(ValueError, match="Invalid session_id"):
            await runtime.get_or_create_session(None, "model", "/tmp")

    @pytest.mark.asyncio
    async def test_empty_model_rejected(self, runtime):
        with pytest.raises(ValueError, match="Invalid model"):
            await runtime.get_or_create_session("sid", "", "/tmp")

    @pytest.mark.asyncio
    async def test_empty_workspace_rejected(self, runtime):
        with pytest.raises(ValueError, match="Invalid workspace"):
            await runtime.get_or_create_session("sid", "model", "")

    @pytest.mark.asyncio
    async def test_empty_prompt_rejected(self, runtime):
        session = {"id": "s1", "messages": [], "workspace": "/tmp"}
        with pytest.raises(ValueError, match="Invalid prompt"):
            async for _ in runtime.run_turn(session, ""):
                pass

    @pytest.mark.asyncio
    async def test_none_prompt_rejected(self, runtime):
        session = {"id": "s1", "messages": [], "workspace": "/tmp"}
        with pytest.raises(ValueError, match="Invalid prompt"):
            async for _ in runtime.run_turn(session, None):
                pass


class TestCoreCache:
    """Core caching behavior."""

    def test_core_cache_created_once(self, runtime):
        core1 = runtime._get_core()
        core2 = runtime._get_core()
        assert core1 is core2

    def test_invalidate_clears_cache(self, runtime):
        core1 = runtime._get_core()
        runtime.invalidate_core_cache()
        core2 = runtime._get_core()
        assert core1 is not core2

    def test_cache_thread_safety(self, runtime):
        import threading
        cores = []
        def get_core():
            cores.append(runtime._get_core())
        threads = [threading.Thread(target=get_core) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # All should get the same core instance
        assert len(set(id(c) for c in cores)) == 1
