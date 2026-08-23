"""Circuit breaker visibility — honest provider status in the turn loop.

The breaker must not be silent plumbing: when the circuit opens mid-turn or
blocks a new turn, callers see provider_status events (with a retry horizon)
instead of a raw CircuitOpenError stringified inside a generic stream error.
"""

import asyncio
import time

import pytest

from wisp.core.stateless import WispAgentCore
from wisp.infra.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
)


class _RaisingAsyncProvider:
    """Simulates a dead provider: every stream attempt raises."""

    def __init__(self):
        self.calls = 0

    async def generate_stream_events_async(self, system_prompt, messages, tools=None, checkpoint_every=50):
        self.calls += 1
        raise ConnectionError("connection refused")
        yield  # pragma: no cover — makes this an async generator


class _HealthyAsyncProvider:
    def __init__(self):
        self.streams = 0

    async def generate_stream_events_async(self, system_prompt, messages, tools=None, checkpoint_every=50):
        self.streams += 1
        yield {"type": "content", "text": "ok"}
        yield {"type": "done", "done_reason": "stop"}


def _make_core(breaker: CircuitBreaker) -> WispAgentCore:
    core = WispAgentCore()
    core._circuit_breaker = breaker
    return core


async def _collect_until_stop(agen):
    """Consume a stream, tolerating a terminal exception like the REPL would."""
    events, exc = [], None
    try:
        async for event in agen:
            events.append(event)
    except Exception as err:
        exc = err
    return events, exc


# ═══════════════════════════════════════════════════════════════════
# Breaker primitives added for visibility
# ═══════════════════════════════════════════════════════════════════


class TestBreakerVisibilityPrimitives:
    def test_retry_after_zero_when_closed(self):
        breaker = CircuitBreaker(CircuitBreakerConfig())
        assert breaker.retry_after() == 0.0

    def test_retry_after_positive_when_freshly_open(self):
        breaker = CircuitBreaker(CircuitBreakerConfig(recovery_timeout=30.0))
        breaker._state = CircuitState.OPEN
        breaker._last_failure_time = time.monotonic()
        retry = breaker.retry_after()
        assert 0.0 < retry <= 30.0

    def test_consume_transition_clears_record(self):
        breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=1, success_threshold=1)
        )

        async def failing():
            raise ConnectionError("down")
            yield

        async def run():
            try:
                async for _ in breaker.stream(lambda: failing()):
                    pass
            except ConnectionError:
                pass

        asyncio.run(run())
        transition = breaker.consume_transition()
        assert transition is not None
        assert transition.to_state is CircuitState.OPEN
        assert breaker.consume_transition() is None


# ═══════════════════════════════════════════════════════════════════
# Turn-loop emission
# ═══════════════════════════════════════════════════════════════════


class TestTurnLoopCircuitEvents:
    @pytest.mark.asyncio
    async def test_midstream_failure_emits_circuit_open_before_raising(self):
        provider = _RaisingAsyncProvider()
        core = _make_core(
            CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, success_threshold=1, recovery_timeout=60.0))
        )
        core.provider = provider

        events, exc = await _collect_until_stop(core._stream_events_async("sys", [], None))

        assert isinstance(exc, ConnectionError)
        statuses = [e for e in events if e.get("type") == "provider_status"]
        assert len(statuses) == 1
        assert statuses[0]["status"] == "circuit_open"
        assert isinstance(statuses[0]["retry_after"], float)
        # The error explaining the turn outcome still follows.
        assert any(e.get("type") == "error" for e in events)

    @pytest.mark.asyncio
    async def test_open_circuit_blocks_new_turn_with_status_and_error(self):
        provider = _RaisingAsyncProvider()
        core = _make_core(
            CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, success_threshold=1, recovery_timeout=60.0))
        )
        core.provider = provider

        # First turn trips the breaker.
        _, _ = await _collect_until_stop(core._stream_events_async("sys", [], None))

        # Second turn fails fast — honestly, without touching the provider.
        events, exc = await _collect_until_stop(core._stream_events_async("sys", [], None))

        assert exc is None, "fail-fast path must end the turn via events, not exceptions"
        assert provider.calls == 1, "provider must not be called while circuit is open"
        types = [e.get("type") for e in events]
        assert types[0] == "provider_status"
        assert "provider_status" in types and "error" in types
        open_event = next(e for e in events if e.get("type") == "provider_status")
        assert open_event["retry_after"] > 0.0

    @pytest.mark.asyncio
    async def test_recovery_emits_circuit_closed(self):
        breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=1, success_threshold=1, recovery_timeout=0.05)
        )
        dead = _RaisingAsyncProvider()
        core = _make_core(breaker)
        core.provider = dead

        await _collect_until_stop(core._stream_events_async("sys", [], None))
        assert breaker.state is CircuitState.OPEN

        healthy = _HealthyAsyncProvider()
        core.provider = healthy
        await asyncio.sleep(0.08)  # let the recovery window elapse

        events, exc = await _collect_until_stop(core._stream_events_async("sys", [], None))
        assert exc is None
        closed = [e for e in events if e.get("type") == "provider_status" and e.get("status") == "circuit_closed"]
        assert len(closed) == 1
        assert events[-1].get("type") == "provider_status"
        assert healthy.streams == 1
