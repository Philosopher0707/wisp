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
    ENV_UNAVAILABLE_PREFIX,
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


@pytest.fixture()
def missing_dep_instance(mini_repo: dict) -> dict:
    """Same repo/base_commit as mini_repo, but the test file imports a
    third-party package that was never installed — standing in for a real
    target repo (astropy, django, ...) whose own runtime deps aren't
    present in whatever env is running ``wisp bench`` itself. This must
    NOT be scored as a wrong model patch: the collection error happens
    before any test the model's fix could affect ever runs.
    """
    inst = dict(mini_repo)
    inst["instance_id"] = "mini__calc-missingdep"
    inst["test_patch"] = (
        "diff --git a/test_calc.py b/test_calc.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/test_calc.py\n"
        "@@ -0,0 +1,6 @@\n"
        "+import definitely_not_installed_wisp_dep_xyz\n"
        "+from calc import add\n"
        "+\n"
        "+\n"
        "+def test_add():\n"
        "+    assert add(2, 3) == 5\n"
    )
    return inst


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
        assert body["model_name_or_path"] == "mock-model"
        assert "return a + b" in body["model_patch"]


class TestApplyCheck:
    def _fixed_ws(self, tmp_path, mini_repo) -> Path:
        from wisp.benchmark.runner import _git_baseline
        from wisp.benchmark.swebench import _setup_from_instance

        ws = tmp_path / "ws"
        ws.mkdir()
        _setup_from_instance(mini_repo, ws)
        _git_baseline(ws)
        (ws / "calc.py").write_text("def add(a, b):\n    return a + b\n",
                                    encoding="utf-8")
        return ws

    def test_valid_patch_reverse_checks(self, tmp_path, mini_repo) -> None:
        from wisp.benchmark.runner import _git_diff_patch
        from wisp.benchmark.swebench import patch_applies_cleanly

        ws = self._fixed_ws(tmp_path, mini_repo)
        patch = _git_diff_patch(ws)
        assert patch
        assert patch_applies_cleanly(ws, patch) is True

    def test_garbage_patch_fails(self, tmp_path, mini_repo) -> None:
        from wisp.benchmark.swebench import _setup_from_instance, patch_applies_cleanly

        ws = tmp_path / "ws"
        ws.mkdir()
        _setup_from_instance(mini_repo, ws)
        assert patch_applies_cleanly(ws, "not a diff at all") is False
        assert patch_applies_cleanly(ws, "") is True
        assert patch_applies_cleanly(tmp_path / "nope", "diff --git x") is False

    def test_run_task_records_patch_applies(self, tmp_path, mini_repo) -> None:
        import asyncio

        from wisp.benchmark.runner import run_task

        res = asyncio.run(run_task(
            task_from_swe_instance(mini_repo), "mock-model",
            _mock_core(_FIXED), workdir=tmp_path))
        assert res.passed is True
        assert res.patch_applies is True

    def test_predictions_summary_shape(self) -> None:
        from wisp.benchmark.cli import predictions_summary
        from wisp.benchmark.runner import BenchResult

        ok = BenchResult(model="m", task_id="t", passed=True,
                         model_patch="diff --git a/f.py b/f.py\n"
                                     "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-a\n+b\n",
                         patch_applies=True)
        bad = BenchResult(model="m", task_id="t2", passed=False,
                          model_patch="", patch_applies=False)
        text = predictions_summary([ok, bad])
        assert "2 prediction(s)" in text and "1/2 patches apply cleanly" in text
        assert "1 file(s)" in text and "+1/-1" in text

    def test_bench_prints_summary(self, tmp_path, mini_repo, capsys) -> None:
        from wisp.benchmark.cli import run_bench

        f = tmp_path / "inst.jsonl"
        f.write_text(json.dumps(mini_repo) + "\n", encoding="utf-8")
        run_bench(
            ["--models", "mock-model", "--instances", str(f),
             "--workdir", str(tmp_path / "ws"),
             "--predictions", str(tmp_path / "p.jsonl")],
            core_factory=_mock_core(_FIXED))
        assert "apply cleanly" in capsys.readouterr().out


class TestEnvUnavailable:
    """A missing target-repo dependency must read as 'couldn't check', not
    as a wrong model patch — the false-negative found running real
    astropy instances (wisp's own venv never has the target repo's deps).
    """

    def test_verify_detects_missing_dependency(
            self, tmp_path, missing_dep_instance) -> None:
        from wisp.benchmark.swebench import _setup_from_instance, _verify_from_instance

        ws = tmp_path / "ws"
        ws.mkdir()
        _setup_from_instance(missing_dep_instance, ws)
        ok, detail = _verify_from_instance(missing_dep_instance, ws)
        assert ok is False
        assert detail.startswith(ENV_UNAVAILABLE_PREFIX)
        assert "definitely_not_installed_wisp_dep_xyz" in detail

    def test_genuine_wrong_patch_is_not_env_unavailable(
            self, tmp_path, mini_repo) -> None:
        # Regression guard: a real assertion failure (pytest exit 1) must
        # never be misclassified as an environment problem.
        from wisp.benchmark.swebench import _setup_from_instance, _verify_from_instance

        ws = tmp_path / "ws"
        ws.mkdir()
        _setup_from_instance(mini_repo, ws)
        (ws / "calc.py").write_text(_WRONG, encoding="utf-8")
        ok, detail = _verify_from_instance(mini_repo, ws)
        assert ok is False
        assert not detail.startswith(ENV_UNAVAILABLE_PREFIX)

    def test_run_task_marks_result_env_unavailable(
            self, tmp_path, missing_dep_instance) -> None:
        import asyncio

        from wisp.benchmark.runner import run_task

        res = asyncio.run(run_task(
            task_from_swe_instance(missing_dep_instance), "mock-model",
            _mock_core(_FIXED), workdir=tmp_path))
        assert res.passed is False
        assert res.env_unavailable is True
        assert res.status() == "ENV_UNAVAILABLE"
        # The model's (correct) patch is still captured for manual review —
        # an unscorable outcome is not "no data".
        assert "+    return a + b" in res.model_patch

    def test_aggregate_excludes_env_unavailable_from_pass_rate(
            self, tmp_path, missing_dep_instance) -> None:
        import asyncio

        from wisp.benchmark.runner import aggregate, run_task

        res = asyncio.run(run_task(
            task_from_swe_instance(missing_dep_instance), "mock-model",
            _mock_core(_FIXED), workdir=tmp_path))
        cards = aggregate(["mock-model"], [res])
        card = cards[0]
        assert card.env_unavailable == 1
        assert card.passed == 0
        assert card.failed == 0
        assert card.total == 0  # unscorable, not a 0/1 fail
        assert card.pass_rate == 0.0


class TestDiffExcludesWispDir:
    """wisp's own scratch dir under the workspace (.wisp/audit.jsonl,
    .wisp/repo_map.json, ...) must never leak into the captured patch —
    it inflated real SWE-bench-Lite predictions and isn't the model's work.
    """

    def test_wisp_scratch_dir_excluded_from_patch(self, tmp_path) -> None:
        from wisp.benchmark.runner import _git_baseline, _git_diff_patch

        ws = tmp_path / "ws"
        ws.mkdir()
        (ws / "real_fix.py").write_text("x = 1\n", encoding="utf-8")
        _git_baseline(ws)

        # Simulate what the runtime writes into the workspace mid-turn.
        wisp_dir = ws / ".wisp"
        wisp_dir.mkdir()
        (wisp_dir / "audit.jsonl").write_text(
            '{"event": "tool_call"}\n' * 50, encoding="utf-8")
        (wisp_dir / "repo_map.json").write_text(
            json.dumps({"files": list(range(500))}), encoding="utf-8")
        (ws / "real_fix.py").write_text("x = 2\n", encoding="utf-8")

        patch = _git_diff_patch(ws)
        assert "real_fix.py" in patch
        assert "x = 2" in patch
        assert ".wisp" not in patch
        assert "audit.jsonl" not in patch
        assert "repo_map.json" not in patch
