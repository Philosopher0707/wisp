"""Tests for circuit breaker."""

import asyncio
import pytest

from wisp.infra.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitOpenError


class TestCircuitBreaker:
    """Circuit breaker state transitions."""

    @pytest.mark.asyncio
    async def test_closed_by_default(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=3))
        assert cb.state.name == "CLOSED"

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2, recovery_timeout=60))

        async def fail():
            raise ValueError("fail")

        # First failure
        with pytest.raises(ValueError):
            await cb.call(fail)
        assert cb.state.name == "CLOSED"

        # Second failure - should open
        with pytest.raises(ValueError):
            await cb.call(fail)
        assert cb.state.name == "OPEN"

    @pytest.mark.asyncio
    async def test_fails_fast_when_open(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=60))

        async def fail():
            raise ValueError("fail")

        # Trigger open
        with pytest.raises(ValueError):
            await cb.call(fail)

        # Next call should fail fast with CircuitOpenError
        with pytest.raises(CircuitOpenError):
            await cb.call(fail)

    @pytest.mark.asyncio
    async def test_half_open_after_timeout(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=0.01))

        async def fail():
            raise ValueError("fail")

        async def succeed():
            return "ok"

        # Trigger open
        with pytest.raises(ValueError):
            await cb.call(fail)
        assert cb.state.name == "OPEN"

        # Wait for recovery timeout
        await asyncio.sleep(0.02)
        assert cb.state.name == "HALF_OPEN"

    @pytest.mark.asyncio
    async def test_closes_after_successes_in_half_open(self):
        cb = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.01,
            success_threshold=2,
        ))

        async def fail():
            raise ValueError("fail")

        async def succeed():
            return "ok"

        # Trigger open
        with pytest.raises(ValueError):
            await cb.call(fail)

        # Wait for half-open
        await asyncio.sleep(0.02)
        assert cb.state.name == "HALF_OPEN"

        # First success in half-open
        result = await cb.call(succeed)
        assert result == "ok"
        assert cb.state.name == "HALF_OPEN"

        # Second success - should close
        result = await cb.call(succeed)
        assert result == "ok"
        assert cb.state.name == "CLOSED"

    @pytest.mark.asyncio
    async def test_failure_in_half_open_reopens(self):
        cb = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.01,
            success_threshold=3,
        ))

        async def fail():
            raise ValueError("fail")

        async def succeed():
            return "ok"

        # Trigger open
        with pytest.raises(ValueError):
            await cb.call(fail)

        # Wait for half-open
        await asyncio.sleep(0.02)

        # One success
        await cb.call(succeed)
        assert cb.state.name == "HALF_OPEN"

        # Failure in half-open -> back to open
        with pytest.raises(ValueError):
            await cb.call(fail)
        assert cb.state.name == "OPEN"

    @pytest.mark.asyncio
    async def test_success_resets_failure_count_in_closed(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=2))

        async def fail():
            raise ValueError("fail")

        async def succeed():
            return "ok"

        # One failure
        with pytest.raises(ValueError):
            await cb.call(fail)

        # Success resets count
        await cb.call(succeed)

        # Another failure - should NOT open (count was reset)
        with pytest.raises(ValueError):
            await cb.call(fail)
        assert cb.state.name == "CLOSED"

    @pytest.mark.asyncio
    async def test_excluded_exceptions_not_caught(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1))

        async def cancel():
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await cb.call(cancel)

        # Circuit should still be closed (excluded exception)
        assert cb.state.name == "CLOSED"

    @pytest.mark.asyncio
    async def test_stream_closes_on_first_error(self):
        cb = CircuitBreaker(CircuitBreakerConfig(failure_threshold=1, recovery_timeout=60))

        async def gen_fail():
            yield "ok"
            raise ValueError("fail")

        async def gen_ok():
            yield "ok"
            yield "ok2"

        # First stream fails - exception propagates after _on_failure
        events = []
        try:
            async for e in cb.stream(gen_fail):
                events.append(e)
        except ValueError:
            pass
        assert events == ["ok"]

        # Circuit should be open
        assert cb.state.name == "OPEN"

        # Next stream fails fast with CircuitOpenError
        with pytest.raises(CircuitOpenError):
            async for _ in cb.stream(gen_ok):
                pass


class TestCircuitBreakerConfig:
    """Circuit breaker configuration."""

    def test_defaults(self):
        config = CircuitBreakerConfig()
        assert config.failure_threshold == 5
        assert config.success_threshold == 2
        assert config.recovery_timeout == 30.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])