"""GH#17 P2.1: SWE-bench-compatible predictions (patch capture + JSONL)."""

from __future__ import annotations

import json
from pathlib import Path

from wisp.benchmark.runner import (
    _git_baseline,
    _git_diff_patch,
    run_task,
)
from wisp.benchmark.tasks import BenchmarkTask


def _solve(ws: Path) -> None:
    (ws / "answer.py").write_text("VALUE = 42\n", encoding="utf-8")


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        id="swe-probe",
        title="probe",
        prompt="write answer",
        instance_id="probe-instance-1",
        setup=lambda ws: (ws / "base.py").write_text("BASE = 1\n", encoding="utf-8"),
        verify=lambda ws: (True, ""),
    )


def _mock_factory(solve):
    class _Core:
        def __init__(self, model):
            self.model = model

        async def turn(self, session, prompt, approval_handler=None):
            solve(Path(session["workspace"]))
            yield {"type": "content", "text": "done"}

    return lambda model: _Core(model)


class TestPatchCapture:
    def test_diff_contains_modification_and_new_file(self, tmp_path) -> None:
        (tmp_path / "base.py").write_text("BASE = 1\n", encoding="utf-8")
        _git_baseline(tmp_path)
        (tmp_path / "base.py").write_text("BASE = 2\n", encoding="utf-8")
        (tmp_path / "new.py").write_text("NEW = True\n", encoding="utf-8")
        patch = _git_diff_patch(tmp_path)
        assert "-BASE = 1" in patch and "+BASE = 2" in patch
        assert "new.py" in patch and "+NEW = True" in patch
        assert patch.startswith("diff --git")

    def test_no_change_means_empty_patch(self, tmp_path) -> None:
        (tmp_path / "base.py").write_text("BASE = 1\n", encoding="utf-8")
        _git_baseline(tmp_path)
        assert _git_diff_patch(tmp_path) == ""

    def test_baseline_never_raises(self, tmp_path) -> None:
        _git_baseline(tmp_path / "missing-dir" / "ws")  # nonexistent dir
        assert _git_diff_patch(tmp_path / "missing-dir" / "ws") == ""


class TestRunnerPredictions:
    async def _run(self, tmp_path):
        import asyncio

        return await run_task(_task(), "mock-model",
                              _mock_factory(_solve), workdir=tmp_path)

    def test_run_task_sets_instance_and_patch(self, tmp_path) -> None:
        import asyncio

        res = asyncio.run(self._run(tmp_path))
        assert res.passed is True
        assert res.instance_id == "probe-instance-1"
        assert "answer.py" in res.model_patch
        assert "+VALUE = 42" in res.model_patch

    def test_write_predictions_jsonl_schema(self, tmp_path) -> None:
        import asyncio

        from wisp.benchmark.cli import write_predictions_jsonl

        res = asyncio.run(self._run(tmp_path))
        out = write_predictions_jsonl(tmp_path / "preds.jsonl", ["mock-model"], [res])
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        body = json.loads(lines[0])
        assert set(body.keys()) == {"instance_id", "model_patch", "model_name"}
        assert body["instance_id"] == "probe-instance-1"
        assert body["model_name"] == "mock-model"
        assert "answer.py" in body["model_patch"]

    def test_run_bench_predictions_flag(self, tmp_path, capsys) -> None:
        from wisp.benchmark.cli import run_bench
        from wisp.benchmark.tasks import tasks_by_ids

        rc = run_bench(
            ["--models", "mock-model", "--tasks", "json-edit",
             "--workdir", str(tmp_path / "ws"),
             "--predictions", str(tmp_path / "p.jsonl")],
            core_factory=_mock_factory(
                lambda ws: (ws / "out.json").write_text("{}", encoding="utf-8")),
        )
        assert rc in (0, 1)  # pass/fail is task business; wiring is ours
        lines = (tmp_path / "p.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert set(json.loads(lines[0]).keys()) == {
            "instance_id", "model_patch", "model_name"}
        # tasks_by_ids import keeps linters honest about the id existing
        assert tasks_by_ids(["json-edit"])[0].id == "json-edit"
