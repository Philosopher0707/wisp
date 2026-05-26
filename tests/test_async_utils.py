"""Tests for wisp.async_utils — safe sync wrappers for async generators."""

import pytest
import asyncio


class TestRunSync:

    def test_run_sync_from_sync_context(self):
        """run_sync works from a pure sync context."""
        from wisp.async_utils import run_sync

        async def gen():
            yield 1
            yield 2
            yield 3

        result = run_sync(gen())
        assert result == [1, 2, 3]

    def test_run_sync_from_async_context(self):
        """run_sync works when called from inside an async context."""
        from wisp.async_utils import run_sync

        async def gen():
            yield "a"
            yield "b"

        async def inner():
            return run_sync(gen())

        result = asyncio.run(inner())
        assert result == ["a", "b"]

    def test_run_sync_empty_generator(self):
        """run_sync handles empty async generators."""
        from wisp.async_utils import run_sync

        async def gen():
            return
            yield  # make it a generator

        result = run_sync(gen())
        assert result == []

    def test_run_sync_with_exception(self):
        """run_sync propagates exceptions from the async generator."""
        from wisp.async_utils import run_sync

        async def gen():
            yield 1
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_sync(gen())

    def test_run_sync_with_agent_core(self):
        """run_sync can consume WispAgentCore.turn() from sync context."""
        from wisp.async_utils import run_sync
        from wisp.core.engine import WispAgentCore
        from wisp.config import WispConfig
        from wisp.providers.mock import MockProvider

        config = WispConfig()
        config = config.replace(model="mock", auto_approve=True, workspace="/tmp", max_iterations=2)
        core = WispAgentCore(config=config)
        core.provider = MockProvider(responses=["Hello!"])

        events = run_sync(core.turn({"messages": []}, "hi"))
        assert len(events) > 0
        assert any(e["type"] == "content" for e in events)


class TestConsume:

    @pytest.mark.asyncio
    async def test_consume_async_generator(self):
        """_consume gathers all items from an async generator."""
        from wisp.async_utils import _consume

        async def gen():
            yield 1
            yield 2

        result = await _consume(gen())
        assert result == [1, 2]
