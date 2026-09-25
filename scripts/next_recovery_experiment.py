#!/usr/bin/env python3
"""Live recovery-to-success experiment.

Runs a real coding objective through the real convergence path
(`wisp.autonomous.converge_on_objective` → `CompositionRoot` → `AgentRuntime`),
on a workspace built to make the first attempt *incomplete for a discoverable
reason*, and records the whole trajectory.

The harness never performs the successful mutation. It builds the initial
repository, states the objective, and measures. Everything between is the
agent.

Scenarios
---------
positive   a task whose contract has three parts; a first attempt that does
           only the obvious one leaves the suite red, and the failure is
           discoverable by running the suite.
negative   a task with no satisfiable constant — used to show bounded
           exhaustion rather than an unbounded retry loop.
resume     the same workspace and journal as `positive`, run in two phases
           with the process stopping between attempt 0 and attempt 1.

Usage
-----
    env -u PYTHONPATH WISP_OLLAMA_URL=... WISP_MAX_TOKENS=65536 \
      .venv/bin/python scripts/next_recovery_experiment.py \
        --scenario positive --model nemotron-3-ultra:cloud \
        --workspace .workbuddy-ai/memory/next/exp/positive \
        --max-attempts 3 --out .workbuddy-ai/memory/next/exp/positive.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── The workspaces ──────────────────────────────────────────────────────

SHAPES = '''"""Shape geometry — the project's public surface."""

__all__ = ["area_rect"]

#: The project's declared public API. Every public callable in this module
#: must appear here; `tests/test_conventions.py` enforces it.
PUBLIC_API = ("area_rect",)


def area_rect(width, height):
    """Area of a rectangle.

    Dimensions must be non-negative; a negative dimension is a caller error
    and this project reports it rather than computing a nonsense area.
    """
    if width < 0 or height < 0:
        raise ValueError("dimensions must be non-negative")
    return width * height
'''

TEST_SHAPES = '''"""The behaviour the objective asks for."""

import shapes


def test_area_square():
    assert shapes.area_square(3) == 9
'''

#: The trap is SEMANTIC, not clerical. A first attempt that copies the shape
#: of `area_rect` (compute the product, list it in `__all__`) still fails,
#: because the project's convention is that every public function VALIDATES
#: its dimensions — a rule stated in `area_rect`'s docstring and enforced
#: here. Copying an assertion is easy; inferring a convention is the work.
TEST_CONVENTIONS = '''"""Project conventions. These are not about the new feature."""

import pytest

import shapes


def test_public_api_tuple_matches():
    assert set(shapes.PUBLIC_API) == {"area_rect", "area_square"}, (
        "PUBLIC_API must list every public callable")


def test_every_public_callable_is_exported():
    public = {
        name for name, obj in vars(shapes).items()
        if callable(obj)
        and not name.startswith("_")
        and getattr(obj, "__module__", "") == "shapes"
    }
    missing = public - set(shapes.__all__)
    assert not missing, f"public callables missing from __all__: {sorted(missing)}"


def test_every_public_function_validates_its_dimensions():
    with pytest.raises(ValueError):
        shapes.area_rect(-1, 2)
    with pytest.raises(ValueError):
        shapes.area_square(-1)
'''

PYPROJECT = '''[project]
name = "shapes-demo"
version = "0.1.0"

[tool.pytest.ini_options]
testpaths = ["tests"]
'''

#: The project's declared API documentation. The test suite does NOT check
#: it, and the objective does not name the file — it says only that the
#: project's declared API documentation must stay in sync. So this is the
#: part of the acceptance an agent can only satisfy by *expanding context*:
#: nothing in `tests/` points at it, and the only signal is the harness's
#: measurement naming the unmet criterion.
API_DOC = '''# Shape API

The public surface of the `shapes` package. Every public callable must appear
in this table.

| Function | Description |
|---|---|
| `area_rect(width, height)` | Area of a rectangle |
'''

DOCS_SPEC = (
    "verify:api_docs",
    ("python", "-c",
     "import sys, pathlib; "
     "text = pathlib.Path('docs/api.md').read_text(); "
     "sys.exit(0 if 'area_square' in text else 1)"),
    "docs/api.md documents every public callable",
)

NEG_APP = '''"""Small app."""


def greet(name):
    return f"hello {name}"
'''

#: A genuinely broader objective: two interacting functions, a pinned
#: contract with error cases, a project convention, and a documented API.
#: The acceptance conditions are DISCLOSED (ADR-0045 R13 states them on every
#: attempt), so this is not a context trap — it is a breadth test. A
#: one-pass implementation that misses any one requirement fails, and the
#: failure is real rather than manufactured.
HARD_APP = '''"""Duration parsing and formatting."""

__all__ = []

#: The project's declared public API.
PUBLIC_API = ()


def _require(condition, message):
    if not condition:
        raise ValueError(message)
'''

HARD_TESTS = '''"""The pinned contract for the duration helpers."""

import pytest

import app


def test_minutes():
    assert app.parse_duration("5m") == 300


def test_hours_and_minutes():
    assert app.parse_duration("1h30m") == 5400


def test_seconds():
    assert app.parse_duration("45s") == 45


def test_combined():
    assert app.parse_duration("2h5m10s") == 7510


def test_repeated_units_accumulate():
    assert app.parse_duration("1h1h") == 7200
    assert app.parse_duration("30s30s") == 60


def test_plain_number_is_seconds():
    assert app.parse_duration("90") == 90


@pytest.mark.parametrize("bad", ["", "5x", "m5", "-5m", "5", "1h30", "h"])
def test_rejects_malformed(bad):
    with pytest.raises(ValueError):
        app.parse_duration(bad)


@pytest.mark.parametrize("bad", ["", "5x", "1h30"])
def test_format_rejects_malformed(bad):
    with pytest.raises(ValueError):
        app.format_duration(bad)


def test_format_round_trips():
    assert app.format_duration(300) == "5m"
    assert app.format_duration(5400) == "1h30m"
    assert app.format_duration(45) == "45s"
    assert app.format_duration(7510) == "2h5m10s"
    assert app.format_duration(90) == "1m30s"


def test_format_rejects_non_positive():
    for bad in (0, -1, "0"):
        with pytest.raises(ValueError):
            app.format_duration(bad)
'''

HARD_CONVENTIONS = '''"""Project conventions."""

import pytest

import app


def test_public_api_tuple_matches():
    assert set(app.PUBLIC_API) == {"parse_duration", "format_duration"}, (
        "PUBLIC_API must list every public callable")


def test_every_public_callable_is_exported():
    public = {
        name for name, obj in vars(app).items()
        if callable(obj)
        and not name.startswith("_")
        and getattr(obj, "__module__", "") == "app"
    }
    missing = public - set(app.__all__)
    assert not missing, f"public callables missing from __all__: {sorted(missing)}"


def test_public_functions_raise_value_error_on_bad_input():
    with pytest.raises(ValueError):
        app.parse_duration("nonsense")
    with pytest.raises(ValueError):
        app.format_duration(0)
'''

HARD_DOC = '''# Duration API

Every public callable must appear in this table.

| Function | Description |
|---|---|
| `greet(name)` | Greets a name (legacy) |
'''

HARD_OBJECTIVE = (
    "Fix the failing test suite in this repository: implement `parse_duration(text)` "
    "and `format_duration(seconds)` in `app.py` so that `python -m pytest tests/ -q` "
    "passes, and keep the project's declared API documentation in sync with the "
    "public surface."
)

NEG_TEST = '''"""The pinned contract — and it is genuinely unsatisfiable.

The previous negative case asserted a 64-character constant, which a model
simply *implemented* (a function returning that constant satisfies it). That
made the "negative" case pass, which is the falsification F2 shape in
reverse: the task was satisfiable and the agent satisfied it.

This one cannot be satisfied by any edit inside the workspace: it asserts
the existence of a path outside it, and the file is read-only so the
assertion cannot be relaxed. The purpose is to prove BOUNDEDNESS — that
repeated failure terminates in an escalation rather than an unbounded retry
loop — not to pretend a real task is impossible.
"""

import pathlib

#: A path the workspace cannot create and the agent cannot reach.
PINNED_ARTIFACT = pathlib.Path("/wisp-fixture/definitely-not-present")


def test_the_pinned_external_artifact_is_present():
    assert PINNED_ARTIFACT.exists(), (
        "the pinned external artifact is missing")
'''

POSITIVE_OBJECTIVE = (
    "Fix the failing test suite in this repository: add `area_square(side)` to "
    "`shapes.py` so that `python -m pytest tests/ -q` passes, and keep the "
    "project's declared API documentation in sync with the public surface."
)

NEGATIVE_OBJECTIVE = (
    "Fix the failing test suite in this repository so that "
    "`python -m pytest tests/ -q` passes."
)


def build(scenario: str, ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    tests = ws / "tests"
    tests.mkdir(exist_ok=True)
    if scenario == "hard":
        (ws / "app.py").write_text(HARD_APP, encoding="utf-8")
        (tests / "test_duration.py").write_text(HARD_TESTS, encoding="utf-8")
        (tests / "test_conventions.py").write_text(HARD_CONVENTIONS,
                                                   encoding="utf-8")
        docs = ws / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "api.md").write_text(HARD_DOC, encoding="utf-8")
    elif scenario in ("positive", "resume"):
        (ws / "shapes.py").write_text(SHAPES, encoding="utf-8")
        (tests / "test_shapes.py").write_text(TEST_SHAPES, encoding="utf-8")
        (tests / "test_conventions.py").write_text(TEST_CONVENTIONS,
                                                   encoding="utf-8")
        docs = ws / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "api.md").write_text(API_DOC, encoding="utf-8")
    else:
        (ws / "app.py").write_text(NEG_APP, encoding="utf-8")
        test_file = tests / "test_contract.py"
        test_file.write_text(NEG_TEST, encoding="utf-8")
        # The pinned contract is READ-ONLY. Without this the negative case is
        # not a negative case at all: the cheapest way to make
        # `pytest tests/ -q` exit 0 is to edit the assertion, and a command
        # criterion cannot tell that from a fix. A read-only fixture is a
        # legitimate environmental condition (and a realistic one); it is not
        # the harness performing the work.
        test_file.chmod(0o444)


def baseline(ws: Path) -> dict:
    """What the workspace looks like before the agent touches it."""
    import subprocess

    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"],
                          cwd=ws, capture_output=True, text=True, timeout=120)
    out = (proc.stdout or "") + (proc.stderr or "")
    return {"exit": proc.returncode, "tail": out[-700:]}


def objective_for(scenario: str) -> str:
    if scenario == "negative":
        return NEGATIVE_OBJECTIVE
    if scenario == "hard":
        return HARD_OBJECTIVE
    return POSITIVE_OBJECTIVE


# ── The run ─────────────────────────────────────────────────────────────


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="next_recovery_experiment")
    ap.add_argument("--scenario", required=True,
                    choices=["positive", "negative", "resume", "hard"])
    ap.add_argument("--model", "-m", required=True)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--resume", action="store_true",
                    help="Continue a previous run from the journal")
    ap.add_argument("--journal", default=None)
    ap.add_argument("--rebuild", action="store_true",
                    help="Recreate the workspace even if it exists")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    from wisp.autonomous import converge_on_objective
    from wisp.config import WispConfig
    from wisp.core.convergence import CommandSpec, criteria_for, derive_acceptance

    ws = Path(args.workspace).resolve()
    journal = Path(args.journal).resolve() if args.journal else ws.parent / f"{ws.name}.journal.jsonl"

    if args.rebuild or not ws.exists():
        if ws.exists():
            import shutil
            shutil.rmtree(ws)
        build(args.scenario, ws)
        if journal.exists():
            journal.unlink()

    objective = objective_for(args.scenario)
    before = baseline(ws)

    # The criteria the HOST derives, recorded so the report can show that the
    # acceptance conditions were not written by the model and were not chosen
    # to make the task easy. The project's own verification command is
    # auto-detected; the documentation criterion is declared by the host
    # because no test encodes it — which is precisely what makes it the part
    # of the objective an agent must expand context to satisfy.
    _, detected = derive_acceptance(objective, str(ws))
    specs = tuple(detected)
    if args.scenario in ("positive", "resume", "hard"):
        cid, argv, desc = DOCS_SPEC
        specs = specs + (CommandSpec(criteria_id=cid, argv=tuple(argv),
                                     description=desc),)

    from wisp.core.convergence import CommandProbe
    probe = CommandProbe(specs)
    baseline_measurement = probe.measure(str(ws))
    criteria = criteria_for(specs, baseline=baseline_measurement,
                            promote_absolute=True)

    cfg = WispConfig()
    from wisp.provider_select import resolve_key
    _key = resolve_key(cfg.provider) or ""
    print(f"[experiment] scenario={args.scenario} model={args.model} "
          f"workspace={ws}", flush=True)
    print(f"[experiment] provider={cfg.provider} "
          f"key={(_key[:12] + '…') if _key else '(none)'} "
          f"api_base={cfg.api_base or '(default)'} "
          f"max_tokens={cfg.max_tokens}", flush=True)
    print(f"[experiment] baseline: exit={before['exit']} "
          f"measurement={baseline_measurement.lines}", flush=True)
    print("[experiment] derived criteria: "
          + "; ".join(f"{c.criteria_id}(required={c.required})"
                      for c in criteria), flush=True)

    def on_attempt(request, observation, duration):
        state = "succeeded" if observation.turn_succeeded else "FAILED"
        print(f"  attempt {request.attempt + 1} [{request.rung}]: turn {state} "
              f"in {duration}s, {observation.tool_calls} tool calls, "
              f"changed={list(observation.changed_files)}", flush=True)
        if request.directive:
            print(f"    directive: {request.directive[:150]}", flush=True)
        if request.evidence:
            print(f"    shown: {list(request.evidence)}", flush=True)

    result = asyncio.run(converge_on_objective(
        objective, str(ws),
        model=args.model,
        permission_mode="full",
        max_attempts=args.max_attempts,
        criteria=criteria,
        specs=tuple(specs),
        journal_path=journal,
        resume=args.resume,
        on_attempt=on_attempt,
    ))

    after = baseline(ws)
    payload = result.to_dict()
    payload.update({
        "scenario": args.scenario,
        "model": args.model,
        "workspace": str(ws),
        "objective": objective,
        "journal": str(journal),
        "baseline": before,
        "baseline_measurement": {
            "observations": baseline_measurement.observations,
            "lines": list(baseline_measurement.lines)},
        "derived_criteria": [
            {"id": c.criteria_id, "required": c.required,
             "description": c.description} for c in criteria],
        "final_state": after,
        "files": sorted(str(p.relative_to(ws)) for p in ws.rglob("*")
                        if p.is_file() and ".git" not in p.parts
                        and ".wisp" not in p.parts and "__pycache__" not in p.parts),
    })
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                   encoding="utf-8")

    print()
    print(f"goal_state={payload['goal_state']} converged={payload['converged']}")
    print(f"reason={payload['reason']}")
    for a in payload["attempts"]:
        print(f"  [{a['index']}] rung={a['rung']:<14} verdict={a['verdict']:<12} "
              f"failure={a['failure_class'] or '-':<16} "
              f"session={a['session_id'] or '-'} "
              f"changed={a['changed_files']}")
    print(f"final: exit={after['exit']}")
    print(f"wrote {out}")
    return 0 if payload["converged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
