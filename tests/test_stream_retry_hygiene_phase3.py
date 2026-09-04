"""Phase 3.1 tests — dead retry branch removal + jitter clock fix (D11).

The retry path must behave identically (empty streams retry with positive
backoff; meaningful streams never retry) while the source drops:
  - the `... is not False` tautology no-op branch,
  - the `asyncio.get_event_loop().time()` jitter clock (deprecated,
    deterministic-ish) in favor of real randomness.
"""

from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_no_tautology_branch_in_source():
    import inspect

    import wisp.core.provider_stream as ps

    src = inspect.getsource(ps.guarded_provider_stream)
    assert "is not False" not in src


def test_no_deprecated_event_loop_clock_in_source():
    import inspect

    import wisp.core.provider_stream as ps

    src = inspect.getsource(ps.guarded_provider_stream)
    assert "get_event_loop" not in src


def test_empty_stream_still_retries_with_positive_backoff():
    from unittest.mock import patch

    from wisp.core.provider_stream import guarded_provider_stream

    async def _go():
        opens: list[str] = []

        def _empty():
            opens.append("empty")

            async def _gen():
                yield {"type": "stream_stats", "sse_lines": 1,
                       "usable_deltas": 0, "empty_choice_chunks": 1,
                       "finish_reason": "stop"}
                return

            return _gen()

        def _good():
            opens.append("good")

            async def _gen():
                yield {"type": "content", "text": "ok"}

            return _gen()

        streams = [_empty, _good]
        it = iter(streams)

        sleeps: list[float] = []
        real_sleep = asyncio.sleep

        async def _spy(s):
            sleeps.append(s)
            await real_sleep(0)

        with patch.object(asyncio, "sleep", side_effect=_spy):
            events = [e async for e in guarded_provider_stream(
                lambda: next(it)(),
                lambda e: e,
                {"stream_stats", "done"},
                first_token_deadline_s=5,
                chunk_deadline_s=5,
                max_attempts=3,
            )]
        assert any(e.get("type") == "content" for e in events)
        assert opens == ["empty", "good"], f"expected retry, got {opens}"
        assert any(s > 0 for s in sleeps), f"expected backoff sleep, got {sleeps}"

    _run(_go())


def test_jitter_uses_random_uniform():
    """Backoff jitter must come from random.uniform, not the event-loop clock."""
    from unittest.mock import patch

    from wisp.core.provider_stream import guarded_provider_stream

    async def _go():
        def _empty():
            async def _gen():
                yield {"type": "done"}

            return _gen()

        import random as _random

        calls: list[tuple[float, float]] = []
        real_uniform = _random.uniform
        real_sleep = asyncio.sleep

        async def _spy_sleep(s):
            await real_sleep(0)

        def _spy_uniform(a, b):
            calls.append((a, b))
            return real_uniform(a, b)

        with patch.object(_random, "uniform", side_effect=_spy_uniform), \
             patch.object(asyncio, "sleep", side_effect=_spy_sleep):
            [e async for e in guarded_provider_stream(
                _empty,
                lambda e: e,
                {"stream_stats", "done"},
                first_token_deadline_s=5,
                chunk_deadline_s=5,
                max_attempts=2,
            )]
        assert calls, "expected random.uniform to supply retry jitter"

    _run(_go())
