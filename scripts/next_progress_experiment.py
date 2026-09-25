"""Live progress-aware recovery experiment.

Drives the **real** path — `wisp.autonomous.converge_on_objective` →
`CompositionRoot` → `AgentRuntime` → `WispAgentCore` → a wired `ToolExecutor`
— against a real provider, and records the complete trajectory: the baseline
and per-attempt measurements, the failure class, the progress verdict and its
signals, the recovery rung, the directive, the verdict, and the goal state.

The harness never mutates the workspace. Every mutation in the record is the
agent's; the harness only *measures* it.

Scenarios
---------
``positive``
    A repository of three modules with unimplemented functions and a red
    suite. Substantial enough that one turn can plausibly make real progress
    and still hit its turn boundary.
``negative``
    A suite that no edit can make pass (its assertion is about a path outside
    the workspace). Progress is therefore impossible; the run must terminate
    conservatively rather than continue.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

MODULE_POOL = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta",
               "theta", "iota", "kappa", "lambda_", "mu", "nu", "xi",
               "omicron", "pi", "rho", "sigma")
MODULES = MODULE_POOL[:8]
PER_MODULE = 2

PYPROJECT = """[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""


def _module_text(module: str, implemented: int) -> str:
    parts = [f'"""{module.title()} helpers."""\n']
    for i in range(PER_MODULE):
        name = f"{module}_{i}"
        if i < implemented:
            parts.append(f"def {name}(x):\n    return x + {i}\n")
        else:
            parts.append(f'def {name}(x):\n    raise NotImplementedError("{name}")\n')
    return "\n".join(parts)


def _tests_text(module: str) -> str:
    body = [f"import {module}\n"]
    for i in range(PER_MODULE):
        body.append(f"def test_{module}_{i}():\n"
                    f"    assert {module}.{module}_{i}(10) == {10 + i}\n")
    return "\n".join(body)


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def build_positive(ws: Path) -> None:
    """The modules, each with unimplemented functions, and a red suite."""
    (ws / "tests").mkdir(parents=True, exist_ok=True)
    (ws / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    for module in MODULES:
        (ws / f"{module}.py").write_text(_module_text(module, 0), encoding="utf-8")
        (ws / "tests" / f"test_{module}.py").write_text(
            _tests_text(module), encoding="utf-8")
    (ws / "README.md").write_text(
        "# arithmetic-helpers\n\nSmall helper modules used by the service.\n"
        "The test suite is the contract; run it with `python -m pytest tests -q`.\n",
        encoding="utf-8")


def build_negative(ws: Path) -> None:
    """A suite that no edit inside the workspace can make pass."""
    (ws / "tests").mkdir(parents=True, exist_ok=True)
    (ws / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (ws / "app.py").write_text(
        '"""The service entry point."""\n\n\ndef greeting():\n'
        '    return "hello"\n', encoding="utf-8")
    (ws / "tests" / "test_contract.py").write_text(
        '"""The pinned contract."""\n\n'
        "import pathlib\n\n"
        "import app\n\n\n"
        "def test_the_deployment_marker_is_present():\n"
        "    # The marker is provisioned by the deployment, outside the\n"
        "    # workspace. Nothing inside the repository can create it.\n"
        "    assert pathlib.Path('/var/lib/wisp-deploy/marker').exists()\n\n\n"
        "def test_greeting():\n"
        '    assert app.greeting() == "hello"\n',
        encoding="utf-8")
    (ws / "README.md").write_text(
        "# service\n\nThe deployment provisions `/var/lib/wisp-deploy/marker`.\n",
        encoding="utf-8")


def _positive_objective() -> str:
    listed = ", ".join(f"{m}.py" for m in MODULES[:-1]) + f" and {MODULES[-1]}.py"
    return ("Fix the failing test suite in this repository: implement the "
            f"remaining functions in {listed} so that "
            "`python -m pytest tests/ -q` passes.")


POSITIVE_OBJECTIVE = _positive_objective()

NEGATIVE_OBJECTIVE = (
    "Fix the failing test suite in this repository so that "
    "`python -m pytest tests/ -q` passes."
)


def build(scenario: str, ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    if scenario == "positive":
        build_positive(ws)
    else:
        build_negative(ws)
    # A local git repo so the agent's own git usage stays inside the
    # experiment workspace instead of walking up into the Wisp checkout.
    _git(["init", "-q"], ws)
    _git(["-c", "user.email=exp@wisp", "-c", "user.name=exp", "add", "-A"], ws)
    _git(["-c", "user.email=exp@wisp", "-c", "user.name=exp",
          "commit", "-qm", "experiment baseline"], ws)


def objective_for(scenario: str) -> str:
    if scenario == "negative":
        return NEGATIVE_OBJECTIVE
    return _positive_objective()


def _run_pytest(ws: Path, timeout: float = 120.0) -> dict:
    argv = (sys.executable, "-m", "pytest", "tests", "-q",
            "-p", "no:cacheprovider")
    try:
        proc = subprocess.run(list(argv), cwd=ws, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"exit": None, "tail": f"timed out after {timeout}s"}
    out = (proc.stdout or "") + (proc.stderr or "")
    return {"exit": proc.returncode, "tail": out[-700:]}


def _read_workspace(ws: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(ws.rglob("*.py")):
        if ".git" in path.parts or "__pycache__" in path.parts:
            continue
        out[str(path.relative_to(ws))] = path.read_text(
            encoding="utf-8", errors="replace")
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True,
                    choices=["positive", "negative"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--permission-mode", default="full")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--modules", type=int, default=8,
                    help="How many modules the task spans (task size).")
    ap.add_argument("--per-module", type=int, default=2,
                    help="How many unimplemented functions each module holds.")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    global MODULES, PER_MODULE
    MODULES = MODULE_POOL[:max(1, min(args.modules, len(MODULE_POOL)))]
    PER_MODULE = max(1, args.per_module)

    from wisp.autonomous import converge_on_objective
    from wisp.core.convergence import (
        CommandProbe, CommandSpec, criteria_for, derive_acceptance,
        read_journal_baseline,
    )

    ws = Path(args.workspace).resolve()
    journal = ws.parent / f"{ws.name}.journal.jsonl"
    if args.rebuild:
        if ws.exists():
            shutil.rmtree(ws)
        if journal.exists():
            journal.unlink()
    if not ws.exists():
        build(args.scenario, ws)

    objective = objective_for(args.scenario)

    # The criteria the HOST derives, and the baseline they are relative to.
    # Recorded so the report can show that the acceptance conditions were not
    # written by the model and were not chosen to make the task easy.
    criteria, specs = derive_acceptance(objective, str(ws))
    probe = CommandProbe(specs)
    baseline = probe.measure(str(ws))
    criteria, _ = derive_acceptance(objective, str(ws), baseline=baseline)

    print(f"[experiment] scenario={args.scenario} model={args.model}", flush=True)
    print(f"[experiment] workspace={ws}", flush=True)
    print(f"[experiment] objective={objective}", flush=True)
    print(f"[experiment] criteria:", flush=True)
    for c in criteria:
        print(f"    required={c.required!s:<5} {c.criteria_id}: {c.description}",
              flush=True)
    print(f"[experiment] baseline measurement: {baseline.lines}", flush=True)
    print(f"[experiment] baseline pytest: {_run_pytest(ws)['tail'][-300:]}",
          flush=True)

    before_files = _read_workspace(ws)
    seen: list[dict] = []

    def on_attempt(request, observation, duration):
        seen.append({
            "attempt": request.attempt, "rung": request.rung,
            "directive": request.directive,
            "session_id": observation.session_id,
            "turn_succeeded": observation.turn_succeeded,
            "terminal_outcome": observation.terminal_outcome,
            "failure_code": observation.failure_code,
            "failure_message": observation.failure_message[:200],
            "tool_calls": observation.tool_calls,
            "changed_files": list(observation.changed_files),
            "duration_s": duration,
        })
        print(f"[attempt {request.attempt}] rung={request.rung} "
              f"turn_succeeded={observation.turn_succeeded} "
              f"code={observation.failure_code} tools={observation.tool_calls} "
              f"changed={list(observation.changed_files)} in {duration}s",
              flush=True)

    started = time.monotonic()
    result = await converge_on_objective(
        objective, str(ws),
        model=args.model,
        permission_mode=args.permission_mode,
        max_attempts=args.max_attempts,
        criteria=criteria,
        specs=specs,
        journal_path=journal,
        on_attempt=on_attempt,
    )
    wall = round(time.monotonic() - started, 1)

    after_files = _read_workspace(ws)
    final_pytest = _run_pytest(ws)
    payload = {
        "scenario": args.scenario,
        "model": args.model,
        "workspace": str(ws),
        "objective": objective,
        "wall_clock_s": wall,
        "criteria": [{"criteria_id": c.criteria_id, "required": c.required,
                      "description": c.description} for c in criteria],
        "baseline": {"lines": list(baseline.lines),
                     "observations": baseline.observations},
        "journal_baseline": (
            (lambda b: {"lines": list(b.lines),
                        "observations": b.observations})(read_journal_baseline(journal))
            if read_journal_baseline(journal) else None),
        "goal_state": str(result.goal_state),
        "converged": result.converged,
        "reason": result.reason,
        "escalation": result.escalation.to_dict() if result.escalation else None,
        "attempts": [a.to_dict() for a in result.attempts],
        "on_attempt": seen,
        "final_pytest": final_pytest,
        "changed_files_overall": sorted(
            k for k in set(before_files) | set(after_files)
            if before_files.get(k) != after_files.get(k)),
        "agent_written_files": {k: v for k, v in after_files.items()
                                if before_files.get(k) != v},
    }
    if args.out:
        Path(args.out).write_text(json.dumps(payload, indent=2,
                                             ensure_ascii=False),
                                  encoding="utf-8")
    print(f"[experiment] goal_state={result.goal_state} "
          f"converged={result.converged} wall={wall}s", flush=True)
    print(f"[experiment] reason={result.reason}", flush=True)
    print(f"[experiment] final pytest: {final_pytest['tail'][-400:]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
