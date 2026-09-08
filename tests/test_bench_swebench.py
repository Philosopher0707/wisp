"""GH#19 P2.2: SWE-bench instance ingestion pins.

Fabricated local-git repos stand in for real instances — no network, no
Docker. A mock core plays the model (fixes / misfixes / no-ops).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wisp.benchmark.swebench import (
    task_from_swe_instance,
    tasks_from_jsonl,
)


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, timeout=30)


@pytest.fixture()
def mini_repo(tmp_path: Path) -> dict:
    """Buggy repo + test patch + base commit SHA."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n",
                                  encoding="utf-8")
    _git("add", "-A", cwd=repo)
    _git("-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "buggy", cwd=repo)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True,
                          timeout=30).stdout.strip()
    test_patch = (
        "diff --git a/test_calc.py b/test_calc.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/test_calc.py\n"
        "@@ -0,0 +1,5 @@\n"
        "+from calc import add\n"
        "+\n"
        "+\n"
        "+def test_add():\n"
        "+    assert add(2, 3) == 5\n"
    )
    return {
        "instance_id": "mini__calc-1",
        "repo": str(repo),
        "base_commit": base,
        "problem_statement": "add() subtracts; make it add.",
        "hints_text": "",
        "patch": "diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@\n-def add(a, b):\n-    return a - b\n+def add(a, b):\n+    return a + b\n",
        "test_patch": test_patch,
        "FAIL_TO_PASS": ["test_calc.py::test_add"],
        "PASS_TO_PASS": [],
    }


def _mock_core(write_fixed: str | None):
    class _Core:
        def __init__(self, model):
            self.model = model

        async def turn(self, session, prompt, approval_handler=None):
            if write_fixed is not None:
                Path(session["workspace"], "calc.py").write_text(
                    write_fixed, encoding="utf-8")
            yield {"type": "content", "text": "done"}

    return lambda model: _Core(model)


_FIXED = "def add(a, b):\n    return a + b\n"
_WRONG = "def add(a, b):\n    return a * b\n"


class TestIngestion:
    def test_missing_keys_rejected(self) -> None:
        with pytest.raises(ValueError, match="instance_id"):
            task_from_swe_instance({"repo": "x"})
        with pytest.raises(ValueError, match="invalid JSON|must be an object"):
            task_from_swe_instance([])  # type: ignore[arg-type]

    def test_task_mapping(self, mini_repo) -> None:
        task = task_from_swe_instance(mini_repo)
        assert task.id == "swe-mini__calc-1"
        assert task.instance_id == "mini__calc-1"
        assert "subtracts" in task.prompt

    def test_gold_patch_never_leaks(self, tmp_path, mini_repo) -> None:
        from wisp.benchmark.swebench import _setup_from_instance

        ws = tmp_path / "ws"
        ws.mkdir()
        _setup_from_instance(mini_repo, ws)
        blob = "".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in ws.rglob("*.py"))
        assert "return a + b" not in blob  # the answer is not in setup
        assert (ws / "test_calc.py").exists()  # but the tests are
        assert (ws / "INSTANCE.json").exists()

    def test_jsonl_loader(self, tmp_path, mini_repo) -> None:
        f = tmp_path / "inst.jsonl"
        f.write_text(json.dumps(mini_repo) + "\n\n" + json.dumps(mini_repo).replace(
            "mini__calc-1", "mini__calc-2") + "\n", encoding="utf-8")
        tasks = tasks_from_jsonl(f)
        assert [t.instance_id for t in tasks] == ["mini__calc-1", "mini__calc-2"]
        with pytest.raises(ValueError, match="no instances"):
            _empty(tmp_path)

    def test_jsonl_rejects_garbage(self, tmp_path) -> None:
        bad = tmp_path / "bad.jsonl"
        bad.write_text("{nope\n", encoding="utf-8")
        with pytest.raises(ValueError, match="invalid JSON"):
            tasks_from_jsonl(bad)


def _empty(tmp_path: Path):
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    from wisp.benchmark.swebench import tasks_from_jsonl as _load
    return _load(f)


class TestSweRun:
    def test_solver_passes_with_patch(self, tmp_path, mini_repo) -> None:
        import asyncio

        from wisp.benchmark.runner import run_task

        res = asyncio.run(run_task(
            task_from_swe_instance(mini_repo), "mock-model",
            _mock_core(_FIXED), workdir=tmp_path))
        assert res.passed is True, res.verify_detail
        assert res.instance_id == "mini__calc-1"
        assert "+    return a + b" in res.model_patch

    def test_wrong_fix_fails_with_partial_patch(self, tmp_path, mini_repo) -> None:
        import asyncio

        from wisp.benchmark.runner import run_task

        res = asyncio.run(run_task(
            task_from_swe_instance(mini_repo), "mock-model",
            _mock_core(_WRONG), workdir=tmp_path))
        assert res.passed is False
        assert "return a * b" in res.model_patch  # partial work captured

    def test_noop_fails_cleanly(self, tmp_path, mini_repo) -> None:
        import asyncio

        from wisp.benchmark.runner import run_task

        res = asyncio.run(run_task(
            task_from_swe_instance(mini_repo), "mock-model",
            _mock_core(None), workdir=tmp_path))
        assert res.passed is False

    def test_bench_instances_flag_predictions(self, tmp_path, mini_repo) -> None:
        from wisp.benchmark.cli import run_bench

        f = tmp_path / "inst.jsonl"
        f.write_text(json.dumps(mini_repo) + "\n", encoding="utf-8")
        rc = run_bench(
            ["--models", "mock-model", "--instances", str(f),
             "--workdir", str(tmp_path / "ws"),
             "--predictions", str(tmp_path / "p.jsonl")],
            core_factory=_mock_core(_FIXED))
        assert rc == 0
        body = json.loads((tmp_path / "p.jsonl").read_text(encoding="utf-8"))
        assert body["instance_id"] == "mini__calc-1"
        assert body["model_name"] == "mock-model"
        assert "return a + b" in body["model_patch"]
