"""Tests for wisp.circuit_breaker — CircuitBreaker."""

import time

import pytest

from wisp.circuit_breaker import CircuitBreaker


class TestCircuitBreakerInit:
    def test_defaults(self):
        cb = CircuitBreaker()
        assert cb.failure_threshold == 3
        assert cb.recovery_timeout == 60.0
        assert cb.half_open_successes == 1

    def test_custom_values(self):
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0, half_open_successes=2)
        assert cb.failure_threshold == 5
        assert cb.recovery_timeout == 30.0
        assert cb.half_open_successes == 2

    def test_minimums(self):
        cb = CircuitBreaker(failure_threshold=0, recovery_timeout=1.0, half_open_successes=0)
        assert cb.failure_threshold == 1
        assert cb.recovery_timeout == 5.0
        assert cb.half_open_successes == 1


class TestCircuitClosed:
    def test_single_failure(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False, error="timeout")
        assert not cb.is_open("web_search")
        assert cb.status("web_search") == "CLOSED"

    def test_at_threshold_minus_one(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        assert not cb.is_open("web_search")
        assert cb.status("web_search") == "CLOSED"

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=True)  # reset
        cb.record("web_search", success=False)
        assert not cb.is_open("web_search")  # only 1 consecutive failure again


class TestCircuitOpens:
    def test_exact_threshold_opens(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        assert cb.is_open("web_search")
        assert cb.status("web_search") == "OPEN"

    def test_over_threshold_stays_open(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        assert cb.is_open("web_search")
        assert cb.status("web_search") == "OPEN"

    def test_different_tools_independent(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("run_bash", success=False)
        assert cb.is_open("web_search")
        assert not cb.is_open("run_bash")


class TestCircuitHalfOpen:
    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.01)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        assert cb.is_open("web_search")
        time.sleep(0.02)  # wait for recovery timeout
        assert not cb.is_open("web_search")  # enters HALF_OPEN
        assert cb.status("web_search") == "HALF_OPEN"

    def test_half_open_probe_success_closes(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.01)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        time.sleep(0.02)
        assert not cb.is_open("web_search")  # HALF_OPEN probe allowed
        cb.record("web_search", success=True)
        assert cb.status("web_search") == "CLOSED"
        assert not cb.is_open("web_search")

    def test_half_open_probe_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.01)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        time.sleep(0.02)
        assert not cb.is_open("web_search")
        cb.record("web_search", success=False)
        assert cb.status("web_search") == "OPEN"
        assert cb.is_open("web_search")


class TestCircuitReset:
    def test_reset_specific(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        assert cb.is_open("web_search")
        cb.reset("web_search")
        assert not cb.is_open("web_search")
        assert cb.status("web_search") == "CLOSED"

    def test_reset_all(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("run_bash", success=False)
        cb.record("run_bash", success=False)
        cb.record("run_bash", success=False)
        assert cb.is_open("web_search")
        assert cb.is_open("run_bash")
        cb.reset()
        assert not cb.is_open("web_search")
        assert not cb.is_open("run_bash")


class TestCircuitSnapshot:
    def test_snapshot(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False, error="timeout")
        cb.record("web_search", success=False, error="network")
        cb.record("web_search", success=True)
        snap = cb.snapshot()
        assert "web_search" in snap
        s = snap["web_search"]
        assert s["state"] == "CLOSED"
        assert s["failures"] == 0  # reset by success
        assert s["total_failures"] == 2
        assert s["total_successes"] == 1
        assert s["last_error"] == ""

    def test_summary_with_open_circuits(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        cb.record("web_search", success=False)
        summary = cb.summary()
        assert "OPEN: web_search" in summary

    def test_summary_all_closed(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.summary() == "All circuits CLOSED"


class TestCircuitUnknownTool:
    def test_unknown_tool_not_open(self):
        cb = CircuitBreaker()
        assert not cb.is_open("nonexistent_tool")
        assert cb.status("nonexistent_tool") == "CLOSED"
