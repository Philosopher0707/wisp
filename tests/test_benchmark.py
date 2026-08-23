"""Benchmark harness regression tests.

Runs the full matrix against mock cores — no live model needed. The
verifiers execute real subprocesses against fixture workspaces.
"""

import asyncio
import json
from pathlib import Path

import pytest

from wisp.benchmark.report import render_full_report, render_result_line, render_scoreboard
from wisp.benchmark.runner import BenchResult, aggregate, run_benchmark, run_task
from wisp.benchmark.scoring import ModelScorecard, score_events
from wisp.benchmark.tasks import CREATE_FUNCTION, FIX_BUG, JSON_EDIT, tasks_by_ids


# ═══════════════════════════════════════════════════════════════════
# Task verifiers — real code executed in real workspaces
# ═══════════════════════════════════════════════════════════════════


class TestVerifiers:
    def test_create_function_passes_on_correct_solution(self, tmp_path):
        CREATE_FUNCTION.setup(tmp_path)
        (tmp_path / "strings_util.py").write_text(
            'def greet(name):\n    return "hello"\n\n\n'
            'def shout(name):\n    return name.upper() + "!"\n',
            encoding="utf-8",
        )
        ok, detail = CREATE_FUNCTION.verify(tmp_path)
        assert ok, detail

    def test_create_function_fails_without_shout(self, tmp_path):
        CREATE_FUNCTION.setup(tmp_path)
        ok, detail = CREATE_FUNCTION.verify(tmp_path)
        assert not ok
        assert "shout" in detail

    def test_fix_bug_passes_on_fixed_file(self, tmp_path):
        FIX_BUG.setup(tmp_path)
        (tmp_path / "totals.py").write_text(
            "def sum_to(n):\n"
            '    """Sum integers 1..n inclusive."""\n'
            "    total = 0\n"
            "    for i in range(1, n + 1):\n"
            "        total += i\n"
            "    return total\n",
            encoding="utf-8",
        )
        ok, detail = FIX_BUG.verify(tmp_path)
        assert ok, detail

    def test_fix_bug_fails_on_original_broken_code(self, tmp_path):
        FIX_BUG.setup(tmp_path)
        ok, detail = FIX_BUG.verify(tmp_path)
        assert not ok

    def test_json_edit_passes_on_correct_edit(self, tmp_path):
        JSON_EDIT.setup(tmp_path)
        cfg = json.loads((tmp_path / "settings.json").read_text())
        cfg["retries"] = 7
        (tmp_path / "settings.json").write_text(json.dumps(cfg))
        assert JSON_EDIT.verify(tmp_path)[0] is True

    def test_json_edit_fails_when_other_keys_change(self, tmp_path):
        JSON_EDIT.setup(tmp_path)
        cfg = {"name": "renamed", "retries": 7, "verbose": False}
        (tmp_path / "settings.json").write_text(json.dumps(cfg))
        assert JSON_EDIT.verify(tmp_path)[0] is False

    def test_tasks_by_ids_rejects_unknown(self):
        with pytest.raises(ValueError, match="nope"):
            tasks_by_ids(["create-function", "nope"])

    def test_tasks_by_ids_none_gives_full_suite(self):
        assert len(tasks_by_ids(None)) == 3


# ═══════════════════════════════════════════════════════════════════
# Event-stream scoring
# ═══════════════════════════════════════════════════════════════════


class TestScoring:
    def test_counts_tools_errors_and_content(self):
        events = [
            {"type": "thinking"},
            {"type": "tool_call", "name": "read_file"},
            {"type": "tool_result", "result": {"status": "ok"}},
            {"type": "tool_call", "name": "edit_file"},
            {"type": "tool_result", "result": {"status": "error", "data": "boom"}},
            {"type": "content", "text": "done!"},
            {"type": "done"},
        ]
        stats = score_events(events)
        assert stats.tool_calls == 2
        assert stats.tool_errors == 1
        assert stats.tool_health == 0.5
        assert stats.content_chars == 5
        assert stats.thinking_events == 1
        assert not stats.errored

    def test_error_event_flags_errored(self):
        stats = score_events([{"type": "error", "message": "model unreachable"}])
        assert stats.errored
        assert "unreachable" in stats.error_message

    def test_zero_tools_means_perfect_health(self):
        assert score_events([]).tool_health == 1.0


# ═══════════════════════════════════════════════════════════════════
# Runner with injected mock cores
# ═══════════════════════════════════════════════════════════════════


def _core_factory_solving(solve):
    """Build a core whose 'turn' runs solve(workspace) as a tool call."""

    class _Core:
        def __init__(self, model):
            self.model = model

        async def turn(self, session, prompt, approval_handler=None):
            ws = Path(session["workspace"])
            yield {"type": "tool_call", "name": "write_file"}
            try:
                solve(ws)
                result = {"status": "ok", "data": "wrote it"}
            except Exception as exc:
                result = {"status": "error", "data": str(exc)}
            yield {"type": "tool_result", "name": "write_file", "result": result}
            yield {"type": "content", "text": "task done"}

    return lambda model: _Core(model)


class TestRunner:
    @pytest.mark.asyncio
    async def test_passing_model_scores_pass(self, tmp_path):
        def solve(ws):
            (ws / "totals.py").write_text(
                "def sum_to(n):\n"
                "    total = 0\n"
                "    for i in range(1, n + 1):\n"
                "        total += i\n"
                "    return total\n",
                encoding="utf-8",
            )

        res = await run_task(
            FIX_BUG, "mock-model", _core_factory_solving(solve),
            workdir=tmp_path,
        )
        assert res.passed is True
        assert res.status() == "PASS"
        assert res.stats.tool_calls == 1
        assert res.stats.tool_health == 1.0

    @pytest.mark.asyncio
    async def test_wrong_solution_fails_verification(self, tmp_path):
        def solve(ws):
            (ws / "totals.py").write_text("def sum_to(n):\n    return 0\n")

        res = await run_task(
            FIX_BUG, "mock-model", _core_factory_solving(solve),
            workdir=tmp_path,
        )
        assert res.passed is False
        assert "sum_to(1)" in res.verify_detail or "assert" in res.verify_detail

    @pytest.mark.asyncio
    async def test_tool_errors_reflect_in_stats(self, tmp_path):
        def solve(ws):
            raise RuntimeError("disk exploded")

        res = await run_task(
            FIX_BUG, "mock-model", _core_factory_solving(solve),
            workdir=tmp_path,
        )
        assert res.stats.tool_errors == 1
        assert res.passed is False

    @pytest.mark.asyncio
    async def test_timeout_marks_result(self, tmp_path):
        class _Hanging:
            def __init__(self, model):
                pass

            async def turn(self, session, prompt, approval_handler=None):
                await asyncio.sleep(30)
                yield {"type": "done"}

        res = await run_task(
            FIX_BUG, "slow-model", lambda m: _Hanging(m),
            timeout_s=0.2, workdir=tmp_path,
        )
        assert res.timed_out is True
        assert res.status() == "TIMEOUT"

    @pytest.mark.asyncio
    async def test_run_benchmark_aggregates_across_models(self, tmp_path):
        def good(ws):
            (ws / "settings.json").write_text(json.dumps({
                "name": "demo", "retries": 7, "verbose": False}))

        def bad(ws):
            pass  # model does nothing

        calls = {"n": 0}

        def factory(model):
            calls["n"] += 1
            return _core_factory_solving(good if model == "good-model" else bad)(model)

        results = await run_benchmark(
            models=["good-model", "bad-model"],
            tasks=[JSON_EDIT],
            core_factory=factory,
            workdir=tmp_path,
        )
        assert len(results) == 2
        cards = aggregate(["good-model", "bad-model"], results)
        by_model = {c.model: c for c in cards}
        assert by_model["good-model"].passed == 1
        assert by_model["bad-model"].failed == 1
        assert calls["n"] == 2, "one core per (model) construction"


# ═══════════════════════════════════════════════════════════════════
# Scoreboard rendering
# ═══════════════════════════════════════════════════════════════════


class TestReport:
    def _results(self):
        good = BenchResult(model="m1", task_id="t1", passed=True, duration_s=2.0)
        bad = BenchResult(model="m2", task_id="t1", passed=False,
                          verify_detail="assert failed", duration_s=9.0)
        return [good, bad]

    def test_scoreboard_rows_and_best(self):
        cards = [
            ModelScorecard(model="m1", passed=3, failed=0, timed_out=0,
                           total_duration_s=6.0),
            ModelScorecard(model="m2", passed=1, failed=1, timed_out=1,
                           total_duration_s=20.0),
        ]
        out = render_scoreboard(cards)
        assert "m1" in out and "m2" in out
        assert "100%" in out
        assert "Best: m1" in out

    def test_full_report_includes_progress_and_summary(self):

        results = self._results()
        out = render_full_report(results, ["m1", "m2"])
        assert "PASS" in out and "FAIL" in out
        assert "Benchmark results:" in out
        assert "Best:" in out

    def test_render_result_line_truncates_long_detail(self):
        res = BenchResult(model="m", task_id="t", passed=False,
                          verify_detail="x" * 300, duration_s=1.0)
        line = render_result_line(res, width=60)
        assert len(line) <= 60
        assert line.endswith("…")

    def test_aggregate_preserves_model_order(self):
        results = list(reversed(self._results()))
        cards = aggregate(["m2", "m1"], results)
        assert [c.model for c in cards] == ["m2", "m1"]
