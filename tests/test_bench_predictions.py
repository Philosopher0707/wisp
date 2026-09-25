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


class TestBaselineNeverTouchesTheEnclosingRepository:
    """The harness must not commit the repository that contains it.

    `rev-parse --git-dir` succeeds from *inside* a repository, so a nested
    workspace used to skip `git init` and then run `git add -A` + `git
    commit` against the **enclosing** repo. Eleven `bench baseline` commits
    landed in this project's repository that way. The check that cannot be
    fooled by nesting is `--show-toplevel`.
    """

    def test_a_nested_workspace_gets_its_own_repository(self, tmp_path) -> None:
        import subprocess

        outer = tmp_path / "outer"
        outer.mkdir()
        (outer / "host.py").write_text("HOST = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=outer, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "add", "-A"], cwd=outer, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "host baseline"], cwd=outer, check=True)
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=outer,
                                capture_output=True, text=True).stdout.strip()

        nested = outer / "nested" / "ws"
        nested.mkdir(parents=True)
        (nested / "work.py").write_text("WORK = 1\n", encoding="utf-8")
        _git_baseline(nested)

        after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=outer,
                               capture_output=True, text=True).stdout.strip()
        assert after == before, "the enclosing repository was committed to"

        # The nested workspace is now a repository in its own right.
        own = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             cwd=nested, capture_output=True, text=True)
        assert own.stdout.strip().endswith("nested/ws")

    def test_an_existing_repository_root_is_reused(self, tmp_path) -> None:
        import subprocess

        (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _git_baseline(tmp_path)
        count = subprocess.run(["git", "rev-list", "--count", "HEAD"],
                               cwd=tmp_path, capture_output=True, text=True)
        assert count.stdout.strip() == "1"


class TestTheHarnessCanActuallyExecute:
    """A benchmark that cannot execute a mutation cannot measure an agent.

    `WispAgentCore._execute_tool` has a documented read-only fallback: with
    no `tool_executor`, every non-READ tool (`write_file`, `edit_file`,
    `run_bash`, `run_tests`, `spawn`) is refused with
    ``[Denied: <tool> requires a wired ToolExecutor …]``.

    The benchmark factory built a core **without** one, so every task
    reported FAIL — including tasks the model was solving correctly, whose
    writes were silently refused. The trace that found it:

        write_file(strings_util.py, <a correct shout() implementation>)
          -> [Denied: write_file requires a wired ToolExecutor …]
    """

    def test_the_benchmark_factory_wires_a_tool_executor(self) -> None:
        from wisp.config import WispConfig
        from wisp.benchmark.runner import make_ollama_core_factory

        cfg = WispConfig().replace(ollama_url="http://127.0.0.1:11434")
        core = make_ollama_core_factory(cfg)("test-model")
        assert core.tool_executor is not None, (
            "the benchmark core has no tool_executor — every mutation will be "
            "denied by the read-only fallback and the run measures nothing")

    def test_no_production_module_builds_an_unwired_core(self) -> None:
        """Tripwire: a *silent* unwired core may not exist anywhere in `wisp/`.

        A direct `WispAgentCore(config=…, provider=…)` silently produces a
        read-only agent. Scanned on the **AST**, not by text: a regex scan
        reported `wisp/__init__.py` and `wisp/metrics.py`, whose only
        mentions are inside docstring examples. A scanner that cannot tell
        code from prose is the defect `ast.AnnAssign` already taught this
        repository once.

        `acp_session.py` is exempt because its fallback is *loud* — it logs
        a warning naming exactly what is missing. That exemption is pinned by
        the next test, so it cannot be quietly widened.
        """
        offenders = _unwired_core_constructions()
        assert not offenders, (
            "these construct a WispAgentCore without a tool_executor, so "
            "every mutation is denied: " + ", ".join(offenders))

    def test_the_one_exempt_fallback_is_loud(self) -> None:
        """`acp_session.py`'s unwired core must keep warning about itself."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1]
                  / "wisp" / "acp_session.py").read_text(encoding="utf-8")
        assert "no provider or tool_executor" in source, (
            "the exempted fallback no longer warns — either restore the "
            "warning or remove the exemption in `_unwired_core_constructions`")

    def test_the_unwired_core_scanner_is_not_vacuous(self, tmp_path) -> None:
        """Control: the scanner must FIND an unwired core that is really there."""
        from wisp.config import WispConfig

        cfg = WispConfig().replace(ollama_url="http://127.0.0.1:11434")
        core = _build_core_without_executor(cfg)
        assert core.tool_executor is None, (
            "the control did not produce an unwired core, so the scan above "
            "cannot be shown to detect one")
        # And the scanner does find the real, wired constructions.
        assert _unwired_core_constructions(
            extra_source='core = WispAgentCore(config=cfg, provider=p)') == [
                "test-snippet:1"]
        assert _unwired_core_constructions(
            extra_source='core = WispAgentCore(config=cfg, provider=p, '
                         'tool_executor=ex)') == []


def _build_core_without_executor(cfg):
    """A core built the way the benchmark used to build one."""
    from wisp.core.stateless import WispAgentCore
    from wisp.providers.factory import ProviderFactory

    return WispAgentCore(config=cfg,
                         provider=ProviderFactory().from_config(cfg))


def _unwired_core_constructions(extra_source: str = "") -> list[str]:
    """Every `WispAgentCore(...)` call in `wisp/` with no `tool_executor=`.

    AST-based, so docstrings and comments are not code. `composition.py` and
    `stateless.py` are exempt: the first is the composition root that wires
    the executor, the second defines the class. `acp_session.py` is exempt
    because its fallback logs a warning naming what is missing — pinned by
    `test_the_one_exempt_fallback_is_loud`.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "wisp"
    exempt = {"composition.py", "stateless.py", "acp_session.py"}
    offenders: list[str] = []

    def _scan(source: str, label: str) -> None:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name != "WispAgentCore":
                continue
            if any(kw.arg == "tool_executor" for kw in node.keywords):
                continue
            offenders.append(f"{label}:{node.lineno}")

    if extra_source:
        _scan(extra_source, "test-snippet")
        return offenders

    for path in sorted(root.rglob("*.py")):
        if path.name in exempt:
            continue
        _scan(path.read_text(encoding="utf-8", errors="replace"),
              str(path.relative_to(root)))
    return offenders


class TestRunnerPredictions:
    async def _run(self, tmp_path):
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
        assert set(body.keys()) == {"instance_id", "model_patch", "model_name_or_path"}
        assert body["instance_id"] == "probe-instance-1"
        assert body["model_name_or_path"] == "mock-model"
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
            "instance_id", "model_patch", "model_name_or_path"}
        # tasks_by_ids import keeps linters honest about the id existing
        assert tasks_by_ids(["json-edit"])[0].id == "json-edit"
