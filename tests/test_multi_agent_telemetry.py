"""Unit tests for Telemetry."""


from wisp.multi_agent.subagent_orchestrator import Telemetry
from wisp.multi_agent.task import SubagentResult


class TestTelemetry:

    def test_init_empty(self):
        t = Telemetry()
        assert t.get() == {}
        assert t.summary() == {}

    def test_record_single(self):
        t = Telemetry()
        r = SubagentResult(task_id="a", success=True, elapsed_seconds=1.5, tokens_used=100)
        r.model_used = "gpt-4"
        t.record("gpt-4", r)
        raw = t.get()
        assert "gpt-4" in raw
        assert len(raw["gpt-4"]) == 1
        assert raw["gpt-4"][0]["success"] is True

    def test_summary(self):
        t = Telemetry()
        for i in range(3):
            r = SubagentResult(task_id=f"a{i}", success=True, elapsed_seconds=1.0 + i, tokens_used=50)
            r.model_used = "gpt-4"
            t.record("gpt-4", r)
        summary = t.summary()
        assert "gpt-4" in summary
        s = summary["gpt-4"]
        assert s["count"] == 3
        assert s["success_rate"] == 1.0
        assert s["avg_latency"] == 2.0  # (1+2+3)/3
        assert s["max_latency"] == 3.0
        assert s["total_tokens"] == 150

    def test_aggregate(self):
        t = Telemetry()
        results = [
            SubagentResult(task_id="a", success=True, elapsed_seconds=1.0, tokens_used=50),
            SubagentResult(task_id="b", success=False, elapsed_seconds=2.0, tokens_used=100),
        ]
        for r in results:
            r.model_used = "gpt-4"
        summary = t.aggregate(results)
        assert summary["gpt-4"]["count"] == 2
        assert summary["gpt-4"]["success_rate"] == 0.5

    def test_multiple_models(self):
        t = Telemetry()
        r1 = SubagentResult(task_id="a", success=True, elapsed_seconds=1.0, tokens_used=50)
        r1.model_used = "gpt-4"
        r2 = SubagentResult(task_id="b", success=True, elapsed_seconds=0.5, tokens_used=20)
        r2.model_used = "llama3"
        t.record("gpt-4", r1)
        t.record("llama3", r2)
        summary = t.summary()
        assert "gpt-4" in summary
        assert "llama3" in summary

    def test_clear(self):
        t = Telemetry()
        r = SubagentResult(task_id="a", success=True, elapsed_seconds=1.0, tokens_used=50)
        r.model_used = "gpt-4"
        t.record("gpt-4", r)
        t.clear()
        assert t.get() == {}
        assert t.summary() == {}

    def test_no_model_used_skipped(self):
        """Results without model_used are not recorded."""
        t = Telemetry()
        r = SubagentResult(task_id="a", success=True, elapsed_seconds=1.0, tokens_used=50)
        # model_used defaults to ""
        t.aggregate([r])
        assert t.summary() == {}
