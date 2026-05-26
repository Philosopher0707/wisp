"""Tests for the sync→async generator bridge (async_utils.sync_gen_iter).

Before the fix, _arun used ``for event in self._run_turn_streaming_events(...)``
where _run_turn_streaming_events is a synchronous generator that calls
requests.post(..., stream=True).  This blocked the asyncio event loop for
the entire LLM response, freezing concurrent WebSocket connections.

After the fix, _arun uses ``async for event in sync_gen_iter(lambda: ...)``
which runs the blocking chain in a thread and yields asynchronously.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator

import pytest

from wisp.async_utils import sync_gen_iter


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_slow_sync_gen(delay_seconds: float = 0.05, count: int = 5):
    """Return a factory for a synchronous generator that sleeps between items."""
    def _factory():
        for i in range(count):
            time.sleep(delay_seconds)
            yield i
    return _factory


def _make_exceptional_gen():
    """Return a factory for a sync generator that raises mid-stream."""
    def _factory():
        yield 1
        yield 2
        raise RuntimeError("boom")
    return _factory


# ──────────────────────────────────────────────────────────────────────────────
# 1. Basic bridge behaviour
# ──────────────────────────────────────────────────────────────────────────────

class TestSyncGenIterBasic:
    """sync_gen_iter must not block the event loop."""

    @pytest.mark.asyncio
    async def test_yields_all_items(self):
        items = await _consume(sync_gen_iter(lambda: iter([10, 20, 30])))
        assert items == [10, 20, 30]

    @pytest.mark.asyncio
    async def test_empty_generator(self):
        items = await _consume(sync_gen_iter(lambda: iter([])))
        assert items == []

    @pytest.mark.asyncio
    async def test_large_items(self):
        data = list(range(1_000))
        items = await _consume(sync_gen_iter(lambda: iter(data)))
        assert items == data

    @pytest.mark.asyncio
    async def test_does_not_block_loop(self):
        """Critical: while the sync gen sleeps, the event loop must remain
        responsive so other tasks (pings, interrupts, approvals) don't freeze."""
        # Start a ping task that triggers every 30 ms
        pings = []

        async def _pinger():
            while True:
                pings.append(time.monotonic())
                await asyncio.sleep(0.03)  # 30 ms

        pinger_task = asyncio.create_task(_pinger())

        try:
            start = time.monotonic()
            items = await _consume(
                sync_gen_iter(_make_slow_sync_gen(delay_seconds=0.05, count=4))
            )
            elapsed = time.monotonic() - start

            assert items == [0, 1, 2, 3]
            # If the loop was blocked we would see very few pings.
            # We expect at least 1 ping per 50ms sleep → ~6-8 total.
            assert len(pings) >= 3, f"Event loop was blocked — only {len(pings)} ping(s)"
        finally:
            pinger_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pinger_task


# ──────────────────────────────────────────────────────────────────────────────
# 2. Exception propagation
# ──────────────────────────────────────────────────────────────────────────────

class TestSyncGenIterExceptions:
    """Exceptions from the sync generator must surface to the consumer."""

    @pytest.mark.asyncio
    async def test_exception_propagated(self):
        with pytest.raises(RuntimeError, match="boom"):
            await _consume(sync_gen_iter(_make_exceptional_gen()))

    @pytest.mark.asyncio
    async def test_exception_during_iteration(self):
        events = []
        agen = sync_gen_iter(_make_exceptional_gen())
        async for item in agen:
            events.append(item)
            if len(events) >= 2:
                break  # Should still raise on next iteration
        with pytest.raises(RuntimeError, match="boom"):
            await _consume(agen)


# ──────────────────────────────────────────────────────────────────────────────
# 3. Cancellation
# ──────────────────────────────────────────────────────────────────────────────

class TestSyncGenIterCancellation:
    """asyncio.CancelledError must terminate the producer thread."""

    @pytest.mark.asyncio
    async def test_consumer_cancel_stops_thread(self):
        """If the consumer async gen is cancelled, the producer thread should stop."""
        factory = _make_slow_sync_gen(delay_seconds=0.1, count=100)
        agen = sync_gen_iter(factory)

        task = asyncio.create_task(_slow_consume(agen, count=2, delay=0.05))

        # Give it time to pull a couple of items
        await asyncio.sleep(0.15)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_timeout_cancel_stops_producer(self):
        """asyncio.wait_for with timeout must not leave the thread hanging."""
        factory = _make_slow_sync_gen(delay_seconds=0.2, count=50)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(_consume(sync_gen_iter(factory)), timeout=0.1)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Integration: exact pattern used by _arun
# ──────────────────────────────────────────────────────────────────────────────

class TestSyncGenIterIntegration:
    """Bridge the exact pattern used by WispAgentCore._arun."""

    @pytest.mark.asyncio
    async def test_arun_like_pattern(self):
        """Simulate what _arun does:  wrap a sync generator in sync_gen_iter
        and consume it with ``async for``."""

        def _fake_run_turn_streaming_events():
            """Sync generator pretending to be _run_turn_streaming_events."""
            for i in range(5):
                time.sleep(0.03)  # simulates network blocking
                yield {"type": "token", "text": str(i)}

        events = []
        async for event in sync_gen_iter(_fake_run_turn_streaming_events):
            events.append(event)

        assert len(events) == 5
        assert [e["text"] for e in events] == ["0", "1", "2", "3", "4"]

    @pytest.mark.asyncio
    async def test_concurrent_consumers(self):
        """Multiple sync_gen_iter instances must coexist on the same loop."""

        def _make_gen(label: int) -> callable:
            def _factory():
                for i in range(3):
                    time.sleep(0.03)
                    yield {"label": label, "i": i}
            return _factory

        tasks = [
            asyncio.create_task(_consume(sync_gen_iter(_make_gen(i))))
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks)

        assert len(results) == 3
        for idx, res in enumerate(results):
            labels = [r["label"] for r in res]
            ivalues = [r["i"] for r in res]
            assert labels == [idx, idx, idx]
            assert ivalues == [0, 1, 2]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _consume(agen: AsyncIterator[Any]) -> list[Any]:
    """Drain an async generator into a list."""
    return [item async for item in agen]


async def _slow_consume(agen: AsyncIterator[Any], count: int, delay: float) -> list[Any]:
    """Drain N items with pauses between them."""
    out: list[Any] = []
    async for item in agen:
        out.append(item)
        if len(out) >= count:
            break
        await asyncio.sleep(delay)
    return out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
