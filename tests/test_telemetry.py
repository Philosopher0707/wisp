"""TDD for Telemetry — structured observability layer.

Replaces: AgentMetrics (in-memory counters) + ad-hoc logging
with structured metrics, health checks, and tracing.
"""

import pytest
import time


@pytest.fixture
def telemetry():
    from wisp.infra.telemetry import Telemetry
    return Telemetry()


# ═══════════════════════════════════════════════════════════════════
# 1. Turn metrics
# ═══════════════════════════════════════════════════════════════════

class TestTurnMetrics:
    """Turn-level metrics: latency, tokens, count."""

    def test_record_turn_increments_counter(self, telemetry):
        telemetry.record_turn(latency_ms=1200, prompt_tokens=1000, completion_tokens=500)
        metrics = telemetry.metrics()
        assert metrics["turns_total"] == 1

    def test_record_turn_tracks_latency(self, telemetry):
        telemetry.record_turn(latency_ms=1200, prompt_tokens=1000, completion_tokens=500)
        telemetry.record_turn(latency_ms=800, prompt_tokens=500, completion_tokens=300)
        metrics = telemetry.metrics()
        assert metrics["turn_latency_ms_avg"] == 1000.0

    def test_record_turn_tracks_tokens(self, telemetry):
        telemetry.record_turn(latency_ms=1000, prompt_tokens=1000, completion_tokens=500)
        telemetry.record_turn(latency_ms=1000, prompt_tokens=500, completion_tokens=300)
        metrics = telemetry.metrics()
        assert metrics["prompt_tokens_total"] == 1500
        assert metrics["completion_tokens_total"] == 800


# ═══════════════════════════════════════════════════════════════════
# 2. Tool metrics
# ═══════════════════════════════════════════════════════════════════

class TestToolMetrics:
    """Tool-level metrics: calls, errors, duration."""

    def test_record_tool_success(self, telemetry):
        telemetry.record_tool("read_file", duration_ms=50, success=True)
        metrics = telemetry.metrics()
        assert metrics["tool_calls_total"] == 1
        assert metrics["tool_errors_total"] == 0

    def test_record_tool_failure(self, telemetry):
        telemetry.record_tool("run_bash", duration_ms=100, success=False)
        metrics = telemetry.metrics()
        assert metrics["tool_calls_total"] == 1
        assert metrics["tool_errors_total"] == 1

    def test_tool_duration_histogram(self, telemetry):
        telemetry.record_tool("read_file", duration_ms=50, success=True)
        telemetry.record_tool("read_file", duration_ms=150, success=True)
        metrics = telemetry.metrics()
        assert metrics["tool_duration_ms_avg"] == 100.0

    def test_tool_success_rate(self, telemetry):
        telemetry.record_tool("read_file", duration_ms=50, success=True)
        telemetry.record_tool("run_bash", duration_ms=100, success=False)
        metrics = telemetry.metrics()
        assert metrics["tool_success_rate"] == 50.0


# ═══════════════════════════════════════════════════════════════════
# 3. Health checks
# ═══════════════════════════════════════════════════════════════════

class TestHealthChecks:
    """Health status reflects system state."""

    def test_default_health_is_healthy(self, telemetry):
        health = telemetry.check_health()
        assert health["status"] == "healthy"

    def test_health_degrades_after_errors(self, telemetry):
        for _ in range(10):
            telemetry.record_tool("run_bash", duration_ms=100, success=False)
        health = telemetry.check_health()
        assert health["status"] == "degraded"
        assert "error_rate" in health["reason"]

    def test_health_includes_uptime(self, telemetry):
        health = telemetry.check_health()
        assert "uptime_seconds" in health
        assert health["uptime_seconds"] >= 0


# ═══════════════════════════════════════════════════════════════════
# 4. Thread safety
# ═══════════════════════════════════════════════════════════════════

class TestThreadSafety:
    """Metrics are safe under concurrent access."""

    def test_concurrent_turn_recording(self, telemetry):
        import threading

        def worker():
            for _ in range(100):
                telemetry.record_turn(latency_ms=10, prompt_tokens=10, completion_tokens=10)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        metrics = telemetry.metrics()
        assert metrics["turns_total"] == 400


# ═══════════════════════════════════════════════════════════════════
# 5. Snapshot / export
# ═══════════════════════════════════════════════════════════════════

class TestSnapshot:
    """Metrics can be exported as JSON-serializable dict."""

    def test_snapshot_is_json_serializable(self, telemetry):
        telemetry.record_turn(latency_ms=1000, prompt_tokens=500, completion_tokens=300)
        telemetry.record_tool("read_file", duration_ms=50, success=True)

        import json
        snapshot = telemetry.snapshot()
        json.dumps(snapshot)  # must not raise

    def test_snapshot_includes_all_categories(self, telemetry):
        telemetry.record_turn(latency_ms=1000, prompt_tokens=500, completion_tokens=300)
        telemetry.record_tool("read_file", duration_ms=50, success=True)

        snapshot = telemetry.snapshot()
        assert "turns" in snapshot
        assert "tools" in snapshot
        assert "health" in snapshot
