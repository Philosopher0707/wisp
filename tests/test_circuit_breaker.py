"""Tests for wisp.circuit_breaker — CircuitBreaker."""

import time

import pytest

from wisp.circuit_breaker import CircuitBreaker


class TestCircuitBreakerStates:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.status("read_file") == "CLOSED"
        assert not cb.is_open("read_file")

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record("read_file", success=False, error="file not found")
        assert cb.status("read_file") == "CLOSED"  # still below threshold
        cb.record("read_file", success=False, error="timeout")
        assert cb.status("read_file") == "OPEN"
        assert cb.is_open("read_file")

    def test_threshold_is_respected(self):
        cb = CircuitBreaker(failure_threshold=5)
        for i in range(4):
            cb.record("web_search", success=False)
            assert not cb.is_open("web_search")
        cb.record("web_search", success=False)
        assert cb.is_open("web_search")

    def test_success_resets_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("x", success=False)
        cb.record("x", success=False)
        cb.record("x", success=True)
        assert cb.status("x") == "CLOSED"
        assert not cb.is_open("x")
        # Now we need 3 more failures
        for i in range(3):
            assert not cb.is_open("x")
            cb.record("x", success=False)
        assert cb.is_open("x")

    def test_half_open_after_recovery(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.5)
        cb.record("t", success=False)
        cb.record("t", success=False)  # OPEN
        assert cb.is_open("t")
        time.sleep(0.6)
        assert not cb.is_open("t")  # HALF_OPEN: one probe allowed
        assert cb.status("t") == "HALF_OPEN"

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.0)
        cb.record("t", success=False)
        cb.record("t", success=False)
        assert cb.status("t") == "OPEN"
        # Force into HALF_OPEN by checking is_open after timeout
        cb._states["t"].state = "HALF_OPEN"
        cb.record("t", success=True)
        assert cb.status("t") == "CLOSED"

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.0)
        cb.record("t", success=False)
        cb.record("t", success=False)
        cb._states["t"].state = "HALF_OPEN"
        cb.record("t", success=False, error="still broken")
        assert cb.status("t") == "OPEN"

    def test_reset_single_tool(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record("t", success=False)
        cb.record("t", success=False)
        assert cb.is_open("t")
        cb.reset("t")
        assert not cb.is_open("t")
        assert cb.status("t") == "CLOSED"

    def test_reset_all_tools(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record("a", success=False)
        cb.record("a", success=False)
        cb.record("b", success=False)
        cb.record("b", success=False)
        assert cb.is_open("a")
        assert cb.is_open("b")
        cb.reset()
        assert not cb.is_open("a")
        assert not cb.is_open("b")

    def test_lifetime_counts(self):
        cb = CircuitBreaker()
        cb.record("t", success=False)
        cb.record("t", success=False)
        cb.record("t", success=True)
        s = cb._states["t"]
        assert s.total_failures == 2
        assert s.total_successes == 1

    def test_snapshot(self):
        cb = CircuitBreaker()
        cb.record("read_file", success=True)
        cb.record("read_file", success=False)
        snap = cb.snapshot()
        assert "read_file" in snap
        assert snap["read_file"]["state"] == "CLOSED"
        assert snap["read_file"]["failures"] == 1
        assert snap["read_file"]["total_failures"] == 1
        assert snap["read_file"]["total_successes"] == 1

    def test_summary(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=999)
        cb.record("a", success=False)
        cb.record("b", success=False)
        cb.record("b", success=False)
        summary = cb.summary()
        assert "OPEN" in summary
        assert "a" in summary
        assert "b" in summary

    def test_summary_all_closed(self):
        cb = CircuitBreaker()
        cb.record("x", success=True)
        assert cb.summary() == "All circuits CLOSED"

    def test_is_open_unknown_tool(self):
        cb = CircuitBreaker()
        assert not cb.is_open("nonexistent")
