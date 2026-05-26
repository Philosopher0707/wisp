"""Tests for wisp.metrics — AgentMetrics."""

from wisp.metrics import AgentMetrics


class TestAgentMetrics:
    def test_default_counters_are_zero(self):
        m = AgentMetrics()
        assert m.turns == 0
        assert m.total_tokens == 0
        assert m.tool_calls_total == 0
        assert m.tool_errors_total == 0

    def test_record_turn(self):
        m = AgentMetrics()
        m.record_turn(latency_s=0.5, prompt_chars=800, completion_chars=400)
        assert m.turns == 1
        assert m.prompt_tokens == 200  # 800 / 4
        assert m.completion_tokens == 100
        assert m.total_tokens == 300
        assert m.latency_ms_total == 500.0

    def test_record_turn_with_custom_chars_per_token(self):
        m = AgentMetrics()
        m.record_turn(latency_s=1.0, prompt_chars=300, completion_chars=150, chars_per_token=3)
        assert m.prompt_tokens == 100  # 300 / 3
        assert m.completion_tokens == 50
        assert m.total_tokens == 150

    def test_record_tool_success(self):
        m = AgentMetrics()
        m.record_tool("read_file", duration_ms=12.0, success=True)
        assert m.tool_calls_total == 1
        assert m.tool_errors_total == 0
        assert "read_file" in m.tool_durations_ms
        assert m.tool_durations_ms["read_file"] == [12.0]

    def test_record_tool_failure(self):
        m = AgentMetrics()
        m.record_tool("web_search", duration_ms=5000.0, success=False)
        assert m.tool_calls_total == 1
        assert m.tool_errors_total == 1
        assert m.tool_durations_ms["web_search"] == [5000.0]

    def test_record_multiple_tools(self):
        m = AgentMetrics()
        m.record_tool("read_file", 10.0, True)
        m.record_tool("read_file", 20.0, True)
        m.record_tool("run_bash", 100.0, False)
        assert m.tool_calls_total == 3
        assert m.tool_errors_total == 1
        assert m.tool_durations_ms["read_file"] == [10.0, 20.0]
        assert m.tool_durations_ms["run_bash"] == [100.0]

    def test_snapshot(self):
        m = AgentMetrics()
        m.record_turn(latency_s=1.0, prompt_chars=400, completion_chars=400)
        m.record_tool("read_file", 10.0, True)
        m.record_tool("run_bash", 200.0, False)
        snap = m.snapshot()
        assert snap["turns"] == 1
        assert snap["total_tokens"] == 200
        assert snap["tool_calls"] == 2
        assert snap["tool_errors"] == 1
        assert snap["tool_success_rate"] == 50.0
        assert snap["avg_tool_duration_ms"]["read_file"] == 10.0
        assert snap["avg_tool_duration_ms"]["run_bash"] == 200.0
        assert snap["avg_latency_ms"] == 1000.0

    def test_snapshot_with_no_data(self):
        m = AgentMetrics()
        snap = m.snapshot()
        assert snap["avg_latency_ms"] == 0.0
        assert snap["tool_success_rate"] == 100.0
        assert snap["avg_tool_duration_ms"] == {}

    def test_reset(self):
        m = AgentMetrics()
        m.record_turn(1.0, 100, 100)
        m.record_tool("x", 10.0, True)
        m.record_compaction()
        m.record_interruption()
        m.record_tool_block()
        m.record_tool_approval(approved=True)

        m.reset()
        assert m.turns == 0
        assert m.total_tokens == 0
        assert m.tool_calls_total == 0
        assert m.tool_errors_total == 0
        assert m.tool_durations_ms == {}
        assert m.interruptions == 0
        assert m.compactions == 0
        assert m.tool_blocks == 0
        assert m.tool_approvals == 0

    def test_record_tool_block(self):
        m = AgentMetrics()
        m.record_tool_block()
        m.record_tool_block()
        assert m.tool_blocks == 2

    def test_record_approval(self):
        m = AgentMetrics()
        m.record_tool_approval(approved=True)
        m.record_tool_approval(approved=False)
        assert m.tool_approvals == 1  # only approved counted

    def test_repr(self):
        m = AgentMetrics()
        m.record_turn(1.5, 1000, 500)
        m.record_tool("read_file", 12.0, True)
        r = repr(m)
        assert "turns=1" in r
        assert "tokens=375" in r  # 1000/4 + 500/4 = 375
        assert "tools=1" in r
