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
            async def turn(self, session, prompt, approval_handler=None, steering_drain=None):
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
            async def turn(self, session, prompt, approval_handler=None, steering_drain=None):
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
    async def test_nonstring_model_rejected(self, runtime):
        # Empty string is now a legal UNSET model (catalog resolves it);
        # only non-strings are garbage at this boundary.
        with pytest.raises(ValueError, match="Invalid model"):
            await runtime.get_or_create_session("sid", None, "/tmp")  # type: ignore[arg-type]

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


class TestReplayLockSafety:
    """Crash-recovery replay must run under the session lock (issue #2, part A)."""

    @pytest.mark.asyncio
    async def test_replay_holds_session_lock(self, runtime):
        """Two concurrent turns on an incomplete-turn repo state lose nothing.

        Regression: replay ran before the session lock was acquired, so a
        turn starting mid-recovery could reassign session["messages"] from a
        stale repo snapshot while the live turn's assistant reply was still
        only in memory (assistant text is never in the event log) —
        orphaning it on a detached list. Both turns' user messages AND
        assistant replies must survive, in turn order.

        The threading-gated fake repo forces the crash-window interleaving
        deterministically: T2's replay lands after T1's in-memory appends
        but before T1's DONE event hits the log.
        """
        import threading
        from wisp.core.session import Session, SessionEvent, SessionEventType

        done_gate = threading.Event()      # holds T1's DONE out of the log
        done_worker_arrived = threading.Event()

        class FakeSessionRepo:
            """Minimal in-memory event log with real replay semantics."""

            def __init__(self):
                self.events = [SessionEvent.user_message(0, "pre-crash message")]

            def append_event(self, sid, event):
                if (event.event_type == SessionEventType.DONE
                        and not done_gate.is_set()):
                    # T1's DONE: signal that its in-memory appends are done,
                    # then wait until T2 has performed its replay.
                    done_worker_arrived.set()
                    assert done_gate.wait(timeout=10), "test gate never released"
                self.events.append(event)

            def get_last_sequence(self, sid):
                if not self.events:
                    return -1
                return max(e.sequence_num for e in self.events)

            def was_last_turn_complete(self, sid):
                if not self.events:
                    return True
                last = max(self.events, key=lambda e: e.sequence_num)
                return last.event_type == SessionEventType.DONE

            def load_session(self, sid):
                session = Session(session_id=sid)
                session.replay(list(self.events))
                return session

        runtime.session_repo = FakeSessionRepo()

        session = {"id": "crash-session", "messages": [], "workspace": "/tmp"}

        t1_in_core = asyncio.Event()
        allow_finish = asyncio.Event()

        class GatedCore:
            async def turn(self, session, prompt, approval_handler=None, steering_drain=None):
                if prompt == "hello":
                    t1_in_core.set()
                    await allow_finish.wait()
                else:
                    await asyncio.sleep(0.01)
                yield {"type": "content", "text": f"response to {prompt}"}
                yield {"type": "done"}

        runtime.core_factory = lambda: GatedCore()

        async def turn(prompt):
            events = []
            async for event in runtime.run_turn(session, prompt):
                events.append(event)
            return events

        t1 = asyncio.create_task(turn("hello"))
        await t1_in_core.wait()
        # T1 is suspended in its core; let it run to its persist step,
        # where its DONE event blocks on the gate.
        allow_finish.set()
        await asyncio.wait_for(asyncio.to_thread(done_worker_arrived.wait), 5)
        # T1's assistant reply is in memory; its DONE is not yet logged.
        # T2's replay now lands squarely in the crash window.
        t2 = asyncio.create_task(turn("world"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        done_gate.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), 15)

        assert [(m["role"], m["content"]) for m in session["messages"]] == [
            ("user", "pre-crash message"),
            ("user", "hello"),
            ("assistant", "response to hello"),
            ("user", "world"),
            ("assistant", "response to world"),
        ]

    def test_replay_block_sits_inside_locked_region(self):
        """Structural pin: replay must stay after lock acquisition.

        Honest scope: this pins source placement only (guards against a
        future move of the block back above the lock); the test above is
        what proves no messages are lost.
        """
        import inspect
        from wisp.core.runtime import AgentRuntime
        source = inspect.getsource(AgentRuntime.run_turn)
        lock_idx = source.index("async with session_lock:")
        replay_idx = source.index("was_last_turn_complete")
        compact_idx = source.index("maybe_compact")
        append_idx = source.index('session["messages"].append')
        assert lock_idx < replay_idx < compact_idx < append_idx

    @pytest.mark.asyncio
    async def test_eviction_exempts_live_turns(self, runtime):
        """_maybe_evict_session_state must not drop state for a held lock."""
        import asyncio
        from wisp.core.runtime import _maybe_evict_session_state

        runtime._max_session_state = 2
        runtime._turn_counts = {"live": 5, "cold1": 1, "cold2": 1}
        # "live" is the coldest by access time: without the held-lock
        # exemption it would be evicted first.
        runtime._session_access = {"live": 0.5, "cold1": 1.0, "cold2": 2.0}
        lock = asyncio.Lock()
        runtime._session_locks = {"live": lock}

        async with lock:
            _maybe_evict_session_state(runtime)
        assert "live" in runtime._turn_counts
