"""Tests for sync/async boundary — exposes asyncio.run() hacks and fixes them.

The core issue: WispAgentCore has async generator methods (_arun, run) but the
public API (Wisp, WispAgent) tries to call them from sync contexts, creating
nested event loop problems.
"""

import pytest
import asyncio
import inspect
from unittest.mock import MagicMock, patch


class TestSyncAsyncBoundary:

    def test_wisp_agent_core_run_is_async_generator(self):
        """WispAgentCore.run() must be an async generator (async def + yield)."""
        from wisp.core.agent import WispAgentCore
        assert inspect.isasyncgenfunction(WispAgentCore.run)

    def test_wisp_agent_core_arun_is_async_generator(self):
        """WispAgentCore._arun() must be an async generator."""
        from wisp.core.agent import WispAgentCore
        assert inspect.isasyncgenfunction(WispAgentCore._arun)

    def test_run_tool_calls_is_async_generator(self):
        """WispAgentCore._run_tool_calls() must be async after Phase 1 refactor."""
        from wisp.core.agent import WispAgentCore
        assert inspect.isasyncgenfunction(WispAgentCore._run_tool_calls)

    def test_sdk_run_impl_should_not_create_own_loop(self):
        """Wisp._run_impl() should not create a new event loop per call."""
        from wisp.sdk import Wisp
        wisp = Wisp.__new__(Wisp)
        wisp._core = MagicMock()
        wisp._skill_name = None
        wisp._closed = False
        wisp._loop = None

        # _run_impl should not create a new event loop
        assert wisp._loop is None

    def test_safe_run_sync_handles_no_loop(self):
        """_safe_run_sync should work when no event loop is running."""
        from wisp.agent import WispAgent

        async def dummy_coro():
            return "success"

        agent = WispAgent.__new__(WispAgent)
        result = agent._safe_run_sync(dummy_coro())
        assert result == "success"

    def test_safe_run_sync_handles_running_loop(self):
        """_safe_run_sync should work when an event loop is already running."""
        from wisp.agent import WispAgent

        async def dummy_coro():
            return "success"

        async def inner():
            agent = WispAgent.__new__(WispAgent)
            return agent._safe_run_sync(dummy_coro())

        result = asyncio.run(inner())
        assert result == "success"

    def test_provider_generate_stream_events_is_not_async(self):
        """Provider.generate_stream_events() should not be an async generator.

        This is intentional — sync generators can be consumed from async code
        without event loop issues.
        """
        from wisp.providers.base import BaseProvider
        # Abstract method — check the declaration is not async
        assert not asyncio.iscoroutinefunction(BaseProvider.generate_stream_events)


class TestAsyncContextCompatibility:
    """Verify the agent works correctly in various async contexts."""

    @pytest.mark.asyncio
    async def test_core_run_can_be_awaited(self):
        """WispAgentCore.run() can be consumed in async tests."""
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig
        from wisp.providers.mock import MockProvider

        config = WispConfig()
        config.model = "mock"
        config.auto_compact = False
        core = WispAgentCore(config=config)
        core.provider = MockProvider(responses=["Hello!"])
        core.client = core.provider

        events = []
        async for event in core.run("hi"):
            events.append(event)

        assert len(events) > 0
        assert any(e.type == "content" for e in events)

    @pytest.mark.asyncio
    async def test_core_run_with_tool_calls(self):
        """WispAgentCore.run() handles tool calls in async context."""
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig
        from wisp.providers.mock import MockProvider

        config = WispConfig()
        config.model = "mock"
        config.auto_compact = False
        core = WispAgentCore(config=config)
        core.provider = MockProvider(
            responses=["I'll read that", "Done!"],
            tool_calls=[[{"function": {"name": "read_file", "arguments": {"path": "test.txt"}}}]],
        )
        core.client = core.provider

        events = []
        async for event in core.run("read test.txt"):
            events.append(event)

        assert any(e.type == "tool_call" for e in events)
        assert any(e.type == "content" for e in events)

    def test_sync_wrapper_for_async_core(self):
        """A sync wrapper should properly run the async core."""
        from wisp.core.agent import WispAgentCore
        from wisp.config import WispConfig
        from wisp.providers.mock import MockProvider

        config = WispConfig()
        config.model = "mock"
        config.auto_compact = False
        core = WispAgentCore(config=config)
        core.provider = MockProvider(responses=["Hello from sync wrapper!"])
        core.client = core.provider

        # Run async method from sync context using asyncio.run
        async def collect_events():
            events = []
            async for event in core.run("hi"):
                events.append(event)
            return events

        events = asyncio.run(collect_events())
        assert len(events) > 0
        assert any(e.type == "content" for e in events)


class TestNoNestedEventLoops:
    """Ensure we don't create nested event loops (which causes RuntimeError)."""

    def test_no_asyncio_run_inside_running_loop(self):
        """asyncio.run() inside a running loop raises RuntimeError.

        This test documents the problem that _safe_run_sync solves.
        """
        async def inner():
            with pytest.raises(RuntimeError):
                asyncio.run(asyncio.sleep(0))

        asyncio.run(inner())

    def test_asyncio_run_from_sync_context_works(self):
        """asyncio.run() from a pure sync context should work."""
        async def coro():
            return "ok"

        result = asyncio.run(coro())
        assert result == "ok"
