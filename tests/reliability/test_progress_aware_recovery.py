"""Progress-aware recovery: the adversarial matrix (progress-aware recovery mission).

The mission asks one empirical question — can a *timeout that made real
progress* be told apart from a *timeout that made none*, and routed to a
continuation rather than to `DIAGNOSTIC`? — and then asks to be shown the ways
that could be faked.

These tests are the falsification attempt, written as properties:

* **P1/P7** a real partial improvement after a timeout is `MEANINGFUL`, and the
  next attempt is a *continuation*, not the generic repair.
* **P2** a timeout with nothing moved stays conservative.
* **P3** file churn — including an identical rewrite of a file — is **not**
  progress. This is the test that pins §3: `files_changed > 0` is activity.
* **P4** tampering with a declared verification input is **not** progress, and
  still is not convergence. The F2 hole must not reopen through this door.
* **P5** a vacuous green (`exit 0`, nothing collected) is not progress.
* **P6** a regression is not progress, even when something else improved.
* **P8** progress can never override acceptance: `GOAL_MET` still needs the
  verdict and the turn to agree.
* **P9** a provider-shaped timeout (no tool calls, no mutation) is an
  environment failure, never coding progress.
* **P10** interrupted-then-resumed == uninterrupted, for both the progress
  verdict and the rung.
* **P11** repeated progress-producing failures are still bounded.
* **P12** the continuation is not the failed strategy.

**The fixture is the point.** Progress has to come from editing the
*implementation*, never the tests — because editing the tests is tampering, and
a test suite that "improves" by rewriting its own assertions would make every
one of these properties vacuous. So the project here is `calc.py` with N
functions, some implemented and some raising, and a `tests/` tree that is
declared as the verification command's input and is never touched by a
legitimate step.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from wisp.core.acceptance import Verdict
from wisp.core.convergence import (
    INITIAL_RUNG,
    AttemptRequest,
    CommandProbe,
    CommandSpec,
    ConvergenceController,
    Measurement,
    Objective,
    TurnObservation,
    criteria_for,
    directive_for,
    read_journal_baseline,
)
from wisp.core.goal import GoalState
from wisp.core.progress import (
    ProgressReport,
    ProgressVerdict,
    evaluate_progress,
)
from wisp.core.recovery import (
    FORBIDDEN_RUNGS,
    PROGRESS_CONTINUATION_RUNGS,
    FailureClass,
    RecoveryLadder,
    RecoveryRung,
    is_legal_rung,
)

MEANINGFUL = ProgressVerdict.MEANINGFUL_PROGRESS
NO_PROGRESS = ProgressVerdict.NO_PROGRESS
UNDETERMINABLE = ProgressVerdict.PROGRESS_UNDETERMINABLE

PYTEST = (sys.executable, "-m", "pytest", "tests", "-q",
          "-p", "no:cacheprovider")

PYPROJECT = """[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""


# ── A real project whose *implementation* is what moves ─────────────────


def _project(root: Path, *, implemented: int, total: int) -> None:
    """Write `calc.py`: the first `implemented` functions work, the rest raise.

    Only `calc.py` is rewritten after the first call, so a legitimate
    "improvement" step never touches the declared verification inputs.
    """
    (root / "tests").mkdir(parents=True, exist_ok=True)
    bodies = []
    for i in range(total):
        if i < implemented:
            bodies.append(f"def f{i}():\n    return {i}\n")
        else:
            bodies.append(f'def f{i}():\n    raise NotImplementedError("f{i}")\n')
    (root / "calc.py").write_text("\n\n".join(bodies) + "\n", encoding="utf-8")

    test_file = root / "tests" / "test_calc.py"
    if not test_file.exists():
        test_file.write_text(
            "import calc\n\n" + "\n\n".join(
                f"def test_f{i}():\n    assert calc.f{i}() == {i}\n"
                for i in range(total)), encoding="utf-8")
        (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")


def _implement(root: Path, *, implemented: int, total: int) -> None:
    """The legitimate progress step: rewrite the implementation, nothing else."""
    bodies = []
    for i in range(total):
        if i < implemented:
            bodies.append(f"def f{i}():\n    return {i}\n")
        else:
            bodies.append(f'def f{i}():\n    raise NotImplementedError("f{i}")\n')
    (root / "calc.py").write_text("\n\n".join(bodies) + "\n", encoding="utf-8")


def _spec(**kw) -> CommandSpec:
    base = dict(criteria_id="verify:cmd0", argv=PYTEST, require_collected=True,
                inputs=("tests", "pyproject.toml", "conftest.py"))
    base.update(kw)
    return CommandSpec(**base)


def _measure(ws: Path, spec: CommandSpec) -> Measurement:
    return CommandProbe([spec]).measure(str(ws))


def _failed(measurement: Measurement, criteria_id: str = "verify:cmd0") -> int:
    return int(measurement.observations[criteria_id].get("failed") or 0)


def _timeout(**kw) -> TurnObservation:
    """The observation a timed-out turn produces. Real code, real shape."""
    base = dict(turn_succeeded=False, terminal_outcome="failed",
                failure_code="E1101",
                failure_message="Turn timed out after 5s",
                failure_recoverable=False)
    base.update(kw)
    return TurnObservation(**base)


def _ok(**kw) -> TurnObservation:
    base = dict(turn_succeeded=True, terminal_outcome="succeeded")
    base.update(kw)
    return TurnObservation(**base)


class ScriptedTurn:
    """A `run_turn` that applies a scripted mutation and reports a scripted
    observation. Running out of script raises, so a controller that makes one
    attempt too many fails loudly instead of silently."""

    def __init__(self, workspace: Path, script):
        self.workspace = workspace
        self.script = list(script)
        self.calls: list[AttemptRequest] = []

    async def __call__(self, request: AttemptRequest) -> TurnObservation:
        self.calls.append(request)
        if not self.script:
            raise AssertionError(
                f"attempt {request.attempt + 1} ran with no script left — "
                f"the controller is not bounded by the script")
        step = self.script.pop(0)
        return step(self.workspace) or _ok()


def _controller(ws: Path, script, *, spec=None, max_attempts=3,
                baseline=None, journal=None):
    spec = spec or _spec()
    turn = ScriptedTurn(ws, script)
    ctl = ConvergenceController(
        run_turn=turn, probe=CommandProbe([spec]), max_attempts=max_attempts,
        baseline=baseline, journal_path=journal)
    return ctl, turn, spec


def _run(coro):
    return asyncio.run(coro)


def _objective(ws: Path, criteria) -> Objective:
    return Objective(goal="implement the remaining functions so the suite passes",
                     workspace=str(ws), criteria=criteria)


# ── The unit contract of `evaluate_progress` ────────────────────────────


def test_no_prior_measurement_is_undeterminable_not_no_progress():
    """Absence of a comparison is not a claim of stagnation."""
    report = evaluate_progress(None, {"verify:cmd0": {"exit": 1, "failed": 3}})
    assert report.verdict is UNDETERMINABLE
    assert not report.meaningful


def test_an_identical_measurement_is_no_progress():
    same = {"verify:cmd0": {"exit": 1, "collected": 3, "failed": 3,
                            "inputs_digest": "abc"}}
    report = evaluate_progress(same, dict(same))
    assert report.verdict is NO_PROGRESS
    assert report.authoritative == ()


def test_a_decreased_failure_count_is_authoritative():
    before = {"verify:cmd0": {"exit": 1, "collected": 10, "failed": 10}}
    after = {"verify:cmd0": {"exit": 1, "collected": 10, "failed": 5}}
    report = evaluate_progress(before, after)
    assert report.verdict is MEANINGFUL
    assert any("10→5" in s for s in report.authoritative)


def test_a_symbol_that_became_defined_is_authoritative():
    before = {"symbol:shout": {"defined": False, "file_present": True}}
    after = {"symbol:shout": {"defined": True, "file_present": True}}
    assert evaluate_progress(before, after).verdict is MEANINGFUL


def test_a_symbol_that_disappeared_is_a_regression():
    before = {"symbol:shout": {"defined": True, "file_present": True}}
    after = {"symbol:shout": {"defined": False, "file_present": True}}
    report = evaluate_progress(before, after)
    assert report.verdict is NO_PROGRESS
    assert report.regressions


def test_changed_files_alone_can_never_be_meaningful_progress():
    """§3: `files_changed > 0` is activity. This is the property that pins it."""
    same = {"verify:cmd0": {"exit": 1, "collected": 3, "failed": 3}}
    report = evaluate_progress(same, dict(same),
                               changed_files=("calc.py", "src/a.py", "README.md"))
    assert report.verdict is NO_PROGRESS
    assert report.supporting, "the activity must still be recorded for review"
    assert not report.authoritative


def test_a_vacuous_green_is_not_progress():
    before = {"verify:cmd0": {"exit": 1, "collected": 0, "failed": 0}}
    after = {"verify:cmd0": {"exit": 0, "collected": 0, "failed": 0}}
    report = evaluate_progress(before, after)
    assert report.verdict is NO_PROGRESS
    assert any("vacuous" in s for s in report.supporting)


def test_a_moved_verification_input_disqualifies_the_measurement():
    before = {"verify:cmd0": {"exit": 1, "collected": 3, "failed": 3,
                              "inputs_digest": "aaaa"}}
    after = {"verify:cmd0": {"exit": 0, "collected": 3, "failed": 0,
                             "inputs_digest": "bbbb"}}
    report = evaluate_progress(before, after)
    assert report.verdict is UNDETERMINABLE, (
        "a measurement whose inputs changed is not evidence of anything — "
        "least of all of progress")
    assert report.unsafe
    assert not report.authoritative


def test_a_rising_pass_count_is_authoritative_when_failures_are_flat():
    """The `pytest -x` shape: `failed` stays 1 while the pass count rises.

    `environment.py` detects `python -m pytest tests/ -x -q`, and `-x` stops at
    the first failure — so on a red suite `failed` is always 1 and the only
    thing that moves is how many tests now pass. Reading only `failed` would
    report "no progress" for a project that had just implemented half its
    functions.
    """
    before = {"verify:cmd0": {"exit": 1, "collected": 1, "failed": 1}}
    after = {"verify:cmd0": {"exit": 1, "collected": 4, "failed": 1}}
    report = evaluate_progress(before, after)
    assert report.verdict is MEANINGFUL
    assert any("passing checks 1→4" in s for s in report.authoritative)


def test_a_falling_pass_count_is_a_regression():
    before = {"verify:cmd0": {"exit": 1, "collected": 4, "failed": 1}}
    after = {"verify:cmd0": {"exit": 1, "collected": 1, "failed": 1}}
    report = evaluate_progress(before, after)
    assert report.verdict is NO_PROGRESS
    assert any("passing checks 4→1" in s for s in report.regressions)


def test_the_report_round_trips_through_json():
    report = evaluate_progress(
        {"verify:cmd0": {"exit": 1, "collected": 4, "failed": 4}},
        {"verify:cmd0": {"exit": 1, "collected": 4, "failed": 2}})
    again = ProgressReport.from_dict(json.loads(json.dumps(report.to_dict())))
    assert again.verdict is report.verdict
    assert again.authoritative == report.authoritative


# ── The rung table: progress widens, and only where it may ──────────────


def test_the_continuation_table_is_total():
    assert set(PROGRESS_CONTINUATION_RUNGS) == set(FailureClass)


@pytest.mark.parametrize("fc", [FailureClass.SECURITY, FailureClass.REPEATED,
                                FailureClass.STAGNATION])
def test_progress_can_never_widen_a_class_that_forbids(fc):
    """The no-retry rule and the no-repeat rule are not negotiable.

    Asserted as *equality of the candidate sets*, not as "no rung is legal":
    `DIAGNOSTIC` is legitimately legal for `REPEATED` and `STAGNATION` without
    any progress at all, so a per-rung assertion would fail for the wrong
    reason. What must hold is that progress adds **nothing**.
    """
    assert PROGRESS_CONTINUATION_RUNGS[fc] == frozenset()
    ladder = RecoveryLadder()
    assert (ladder.legal_rungs(fc, progress=MEANINGFUL)
            == ladder.legal_rungs(fc))
    for rung in RecoveryRung:
        if rung is RecoveryRung.HUMAN:
            continue
        assert not (is_legal_rung(fc, rung, progress=MEANINGFUL)
                    and not is_legal_rung(fc, rung)), (
            f"{fc.value} gained {rung.name} from a progress report")


def test_forbidden_wins_over_a_progress_widening_for_every_class():
    for fc in FailureClass:
        for rung in FORBIDDEN_RUNGS[fc]:
            assert not is_legal_rung(fc, rung, progress=MEANINGFUL)


def test_only_meaningful_progress_widens():
    for progress in (None, NO_PROGRESS, UNDETERMINABLE, "not-a-verdict"):
        assert not is_legal_rung(FailureClass.ENVIRONMENT, RecoveryRung.REPAIR,
                                 progress=progress)


def test_progress_none_is_identical_to_the_legacy_behaviour():
    """Every existing caller must be unaffected. The sets are unchanged."""
    for fc in FailureClass:
        ladder = RecoveryLadder()
        assert ladder.legal_rungs(fc) == ladder.legal_rungs(fc, progress=None)


def test_the_environment_class_gains_exactly_the_continuation_rung():
    ladder = RecoveryLadder()
    assert ladder.legal_rungs(FailureClass.ENVIRONMENT) == [RecoveryRung.DIAGNOSTIC]
    assert ladder.legal_rungs(FailureClass.ENVIRONMENT, progress=MEANINGFUL) == [
        RecoveryRung.REPAIR, RecoveryRung.DIAGNOSTIC]


# ── P1 / P7: real progress after a timeout selects a continuation ───────


def test_p1_a_timeout_with_measurable_progress_selects_continuation(tmp_path):
    """The decisive positive property, deterministically.

    A red project, a turn that implements part of it and times out, a second
    turn that finishes. Attempt 0 must be classified `environment` (it *is*
    one) and must still route to `REPAIR` — because the objective measurably
    moved, and `REPAIR` is the rung the class withholds.
    """
    _project(tmp_path, implemented=1, total=4)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    assert _failed(baseline) == 3
    criteria = criteria_for([spec], baseline=baseline)

    def partial(ws):
        _implement(ws, implemented=3, total=4)      # 3 failed → 1 failed
        return _timeout(tool_calls=12, changed_files=("calc.py",))

    def finish(ws):
        _implement(ws, implemented=4, total=4)      # 1 failed → 0
        return _ok(tool_calls=4, changed_files=("calc.py",))

    ctl, turn, _ = _controller(tmp_path, [partial, finish], spec=spec,
                               baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))

    first, second = result.attempts
    # Attempt 0: a genuine timeout, correctly classified as an environment fault.
    assert first.failure_class == FailureClass.ENVIRONMENT.value
    assert first.observation.terminal_outcome == "failed"
    # …and it measurably advanced the objective.
    assert first.progress == MEANINGFUL.value
    assert any("3→1" in s for s in first.progress_signals)
    # The recovery is the continuation, and it is a *different* strategy.
    assert first.rung == INITIAL_RUNG
    assert second.rung == "REPAIR"
    assert second.directive != turn.calls[0].directive
    assert "measurably advanced this objective" in second.directive
    assert "Finish the job" in second.directive
    # It converged, on evidence the harness produced.
    assert result.converged
    assert result.goal_state is GoalState.GOAL_MET
    assert "verify:cmd0:inputs_unchanged" not in first.unmet


def test_p7_a_five_of_ten_improvement_is_meaningful(tmp_path):
    _project(tmp_path, implemented=0, total=10)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    assert _failed(baseline) == 10
    criteria = criteria_for([spec], baseline=baseline)

    def half(ws):
        _implement(ws, implemented=5, total=10)
        return _timeout(tool_calls=20, changed_files=("calc.py",))

    ctl, _, _ = _controller(tmp_path, [half, lambda ws: _ok()], spec=spec,
                            baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    assert result.attempts[0].progress == MEANINGFUL.value
    assert result.attempts[1].rung == "REPAIR"


# ── P2: no progress stays conservative ──────────────────────────────────


def test_p2_a_timeout_with_no_progress_stays_conservative(tmp_path):
    _project(tmp_path, implemented=1, total=4)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def nothing(_ws):
        return _timeout(tool_calls=9)          # ran, changed nothing

    ctl, _, _ = _controller(tmp_path, [nothing, lambda ws: _ok()], spec=spec,
                            baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    assert result.attempts[0].progress == NO_PROGRESS.value
    assert result.attempts[1].rung == "DIAGNOSTIC", (
        "a timeout that moved nothing must not open the continuation door")
    assert result.attempts[1].directive == directive_for("DIAGNOSTIC")


# ── P3: churn is not progress ───────────────────────────────────────────


def test_p3_file_churn_is_not_meaningful_progress(tmp_path):
    """Formatting, an identical rewrite, and a new README. None is progress."""
    _project(tmp_path, implemented=1, total=4)
    original_tests = (tmp_path / "tests" / "test_calc.py").read_bytes()
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def churn(ws):
        # Real byte changes to the implementation that change nothing about
        # what it does, plus an identical rewrite of the declared input (same
        # bytes ⇒ same digest ⇒ not tampering).
        text = (ws / "calc.py").read_text(encoding="utf-8")
        (ws / "calc.py").write_text(
            "\n".join(line + "  # reformatted" for line in text.splitlines())
            + "\n", encoding="utf-8")
        (ws / "README.md").write_text("docs\n", encoding="utf-8")
        (ws / "tests" / "test_calc.py").write_bytes(original_tests)
        return _timeout(tool_calls=14, changed_files=("calc.py", "README.md",
                                                     "tests/test_calc.py"))

    ctl, _, _ = _controller(tmp_path, [churn, lambda ws: _ok()], spec=spec,
                            baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    first = result.attempts[0]
    assert first.progress == NO_PROGRESS.value
    assert first.progress_signals, "the activity must still be recorded"
    assert result.attempts[1].rung == "DIAGNOSTIC"


# ── P4: tampering is not progress and still is not convergence ──────────


def test_p4_editing_the_declared_input_is_not_progress_and_not_success(tmp_path):
    """The F2 hole must not reopen through the progress door."""
    _project(tmp_path, implemented=0, total=3)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def tamper(ws):
        # Make the suite pass by rewriting the tests — the live falsification.
        (ws / "tests" / "test_calc.py").write_text(
            "import calc\n\n\ndef test_trivial():\n    assert True\n",
            encoding="utf-8")
        return _ok(tool_calls=3, changed_files=("tests/test_calc.py",))

    ctl, _, _ = _controller(tmp_path, [tamper, lambda ws: _ok()], spec=spec,
                            baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    first = result.attempts[0]
    assert first.progress != MEANINGFUL.value
    assert first.verdict == Verdict.FAIL.value
    assert "verify:cmd0:inputs_unchanged" in first.unmet
    assert first.goal_state != GoalState.GOAL_MET.value
    assert not result.converged


# ── P5: a vacuous green is not progress ─────────────────────────────────


def test_p5_a_green_with_zero_collected_is_not_progress(tmp_path):
    spec = CommandSpec(
        criteria_id="verify:cmd0", require_collected=True,
        argv=(sys.executable, "-c",
              "import sys,pathlib;"
              "sys.exit(0 if pathlib.Path('state.txt').read_text().strip()"
              "=='go' else 1)"))
    (tmp_path / "state.txt").write_text("stop", encoding="utf-8")
    baseline = _measure(tmp_path, spec)
    assert baseline.observations["verify:cmd0"]["exit"] == 1
    criteria = criteria_for([spec], baseline=baseline)

    def go(ws):
        (ws / "state.txt").write_text("go", encoding="utf-8")
        return _timeout(tool_calls=2, changed_files=("state.txt",))

    ctl, _, _ = _controller(tmp_path, [go, lambda ws: _ok()], spec=spec,
                            baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    assert result.attempts[0].progress == NO_PROGRESS.value
    assert result.attempts[1].rung == "DIAGNOSTIC"


# ── P6: a regression is not progress ────────────────────────────────────


def test_p6_a_regression_does_not_open_the_continuation_door(tmp_path):
    """A timeout that broke a passing test is worse than one that did nothing."""
    _project(tmp_path, implemented=2, total=2)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    assert baseline.observations["verify:cmd0"]["exit"] == 0
    criteria = criteria_for([spec], baseline=baseline)

    def break_it(ws):
        _implement(ws, implemented=1, total=2)      # one passing test now fails
        return _timeout(tool_calls=8, changed_files=("calc.py",))

    ctl, _, _ = _controller(tmp_path, [break_it, lambda ws: _ok()], spec=spec,
                            baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    first = result.attempts[0]
    assert first.progress == NO_PROGRESS.value
    assert any("failing checks 0→1" in s for s in first.progress_signals)
    assert result.attempts[1].rung == "DIAGNOSTIC"


# ── P8: progress never overrides acceptance ─────────────────────────────


def test_p8_progress_cannot_override_acceptance(tmp_path):
    """A meaningful improvement that still misses the criteria is not `GOAL_MET`.

    The objective here *states* that the suite must pass, so the absolute
    criterion is required rather than baseline-relative (`promote_absolute`).
    Without that the partial fix would legitimately satisfy "no regression" —
    which is the ADR-0045 F51 rule working, not a hole — and this test would be
    measuring the wrong thing.
    """
    _project(tmp_path, implemented=0, total=4)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline, promote_absolute=True)

    def partial(ws):
        _implement(ws, implemented=2, total=4)
        return _ok(tool_calls=6, changed_files=("calc.py",))

    ctl, _, _ = _controller(tmp_path, [partial, partial], spec=spec,
                            baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    assert result.attempts[0].progress == MEANINGFUL.value
    assert result.attempts[0].verdict == Verdict.FAIL.value
    assert not result.converged
    assert result.goal_state is not GoalState.GOAL_MET


def test_p8b_a_clean_finish_is_goal_met(tmp_path):
    _project(tmp_path, implemented=0, total=2)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def finish(ws):
        _implement(ws, implemented=2, total=2)
        return _ok(tool_calls=5, changed_files=("calc.py",))

    ctl, _, _ = _controller(tmp_path, [finish], spec=spec, baseline=baseline,
                            max_attempts=1)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    assert result.converged
    assert result.attempts[0].progress == MEANINGFUL.value


# ── P9: a provider-shaped timeout is not coding progress ────────────────


def test_p9_a_provider_only_timeout_is_an_environment_failure(tmp_path):
    """Zero tool calls, zero mutation, zero movement: not agent progress."""
    _project(tmp_path, implemented=1, total=3)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def provider_stall(_ws):
        return _timeout(tool_calls=0, changed_files=(),
                        failure_message="provider stream stalled")

    ctl, _, _ = _controller(tmp_path, [provider_stall, lambda ws: _ok()],
                            spec=spec, baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    first = result.attempts[0]
    assert first.failure_class == FailureClass.ENVIRONMENT.value
    assert first.progress == NO_PROGRESS.value
    assert result.attempts[1].rung == "DIAGNOSTIC"


# ── P10: resume reproduces the classification and the decision ──────────


def test_p10_resume_reproduces_progress_and_rung(tmp_path):
    """Interrupted + resumed == uninterrupted, for progress AND for the rung."""
    spec = _spec()

    def partial(ws):
        _implement(ws, implemented=3, total=4)      # 3 failed → 1 failed
        return _timeout(tool_calls=11, changed_files=("calc.py",))

    # (a) Uninterrupted: both attempts in one process.
    _project(tmp_path, implemented=1, total=4)
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)
    assert _failed(baseline) == 3
    whole_ctl, _, _ = _controller(tmp_path, [partial, lambda ws: _ok()],
                                  spec=spec, baseline=baseline, max_attempts=2)
    whole = _run(whole_ctl.converge(_objective(tmp_path, criteria)))

    # (b) Interrupted after attempt 0, resumed in a NEW controller that is told
    # nothing except where the journal is.
    _project(tmp_path, implemented=1, total=4)      # rebuild the start state
    journal = tmp_path / "run.jsonl"
    first_pass, _, _ = _controller(tmp_path, [partial], spec=spec,
                                   baseline=baseline, max_attempts=1,
                                   journal=journal)
    _run(first_pass.converge(_objective(tmp_path, criteria)))

    # The baseline is durable, and it is the ORIGINAL one — not a re-measurement
    # of the already-mutated workspace.
    recovered = read_journal_baseline(journal)
    assert recovered is not None
    assert _failed(recovered) == 3

    resumed, turn2, _ = _controller(tmp_path, [lambda ws: _ok()], spec=spec,
                                    baseline=recovered, max_attempts=2,
                                    journal=journal)
    after = _run(resumed.converge(_objective(tmp_path, criteria), resume=True))

    assert len(after.attempts) == 2
    assert len(turn2.calls) == 1, "the completed attempt must not be re-run"
    assert (after.attempts[0].progress
            == whole.attempts[0].progress
            == MEANINGFUL.value)
    assert after.attempts[1].rung == whole.attempts[1].rung == "REPAIR"
    assert after.attempts[1].directive == whole.attempts[1].directive
    assert after.goal_state is whole.goal_state is GoalState.GOAL_MET


def test_p10b_the_resume_equivalence_is_not_vacuous(tmp_path):
    """Non-vacuity for P10: the equivalence would be trivially true if the
    progress verdict did not change the rung. It does — so a resumed run that
    lost the verdict would choose differently."""
    with_progress = RecoveryLadder()
    assert with_progress.legal_rungs(
        FailureClass.ENVIRONMENT, progress=MEANINGFUL)[0] is RecoveryRung.REPAIR
    without_progress = RecoveryLadder()
    assert without_progress.legal_rungs(
        FailureClass.ENVIRONMENT)[0] is RecoveryRung.DIAGNOSTIC


def test_p10c_a_torn_journal_still_resumes(tmp_path):
    """The new `kind` discriminator must not break the torn-line tolerance."""
    _project(tmp_path, implemented=1, total=4)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)
    journal = tmp_path / "run.jsonl"

    ctl, _, _ = _controller(
        tmp_path, [lambda ws: _timeout(tool_calls=1)], spec=spec,
        baseline=baseline, max_attempts=1, journal=journal)
    _run(ctl.converge(_objective(tmp_path, criteria)))
    with journal.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "attempt", "index": 1, "ru')   # torn write

    resumed, turn2, _ = _controller(tmp_path, [lambda ws: _ok()], spec=spec,
                                    baseline=baseline, max_attempts=2,
                                    journal=journal)
    _run(resumed.converge(_objective(tmp_path, criteria), resume=True))
    # The torn line is discarded, the completed attempt is reloaded, and the
    # resumed run makes exactly one NEW attempt — at the next index.
    assert [a.index for a in resumed.attempts] == [0, 1]
    assert len(turn2.calls) == 1
    assert turn2.calls[0].attempt == 1


# ── P11: bounded, even when every failure made progress ─────────────────


def test_p11_progress_does_not_make_recovery_unbounded(tmp_path):
    """Four progress-producing timeouts must still terminate honestly."""
    _project(tmp_path, implemented=0, total=8)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def make(step):
        def _step(ws):
            _implement(ws, implemented=step, total=8)
            return _timeout(tool_calls=5, changed_files=("calc.py",))
        return _step

    ctl, turn, _ = _controller(tmp_path, [make(s) for s in (1, 2, 3, 4)],
                               spec=spec, baseline=baseline, max_attempts=4)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    assert len(result.attempts) <= 4, "the attempt budget must hold"
    assert len(turn.calls) <= 4
    assert not result.converged
    assert result.goal_state in (GoalState.ESCALATED_TO_HUMAN,
                                 GoalState.GOAL_FAILED,
                                 GoalState.GOAL_UNVERIFIED)
    rungs = [a.rung for a in result.attempts]
    assert len(rungs) == len(set(rungs)), f"a rung repeated: {rungs}"
    assert all(a.progress == MEANINGFUL.value for a in result.attempts), (
        "every attempt made progress — and it still terminated")


# ── P12: the continuation is not the failed strategy ────────────────────


def test_p12_the_continuation_is_a_materially_different_strategy(tmp_path):
    _project(tmp_path, implemented=1, total=4)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def partial(ws):
        _implement(ws, implemented=3, total=4)
        return _timeout(tool_calls=10, changed_files=("calc.py",))

    ctl, turn, _ = _controller(tmp_path, [partial, lambda ws: _ok()],
                               spec=spec, baseline=baseline, max_attempts=2)
    _run(ctl.converge(_objective(tmp_path, criteria)))
    first, second = turn.calls
    assert first.rung == INITIAL_RUNG and second.rung == "REPAIR"
    assert first.directive != second.directive
    assert second.criteria == first.criteria, "the objective never changes"
    # The continuation directive says the measured thing, not "try harder".
    assert "measurably advanced this objective" in second.directive
    assert "Do NOT restart the task" in second.directive


def test_the_continuation_is_shown_what_improved_not_only_what_is_wrong(ws=None):
    """A continuation prompt that contradicts itself stalls the model.

    A live continuation attempt was told *"the objective advanced"* and then
    shown a measurement that still looked red, and it spent its whole turn
    re-inspecting instead of continuing. What the previous attempt IMPROVED is
    a harness measurement like any other, so it is carried into the next
    attempt's evidence.
    """
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    _project(tmp, implemented=1, total=4)
    spec = _spec()
    baseline = _measure(tmp, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def partial(path):
        _implement(path, implemented=3, total=4)
        return _timeout(tool_calls=9, changed_files=("calc.py",))

    ctl, turn, _ = _controller(tmp, [partial, lambda p: _ok()], spec=spec,
                               baseline=baseline, max_attempts=2)
    _run(ctl.converge(_objective(tmp, criteria)))
    first, second = turn.calls
    assert first.evidence == (), "attempt 0 has nothing to be shown"
    joined = "\n".join(second.evidence)
    assert "passing checks" in joined, joined
    assert "failing checks" in joined, joined
    # The still-failing measurement is there too — both halves are shown.
    assert any("verify:cmd0" in line for line in second.evidence)


def test_a_non_progressing_failure_shows_only_the_measurement():
    """The extra lines are for progress, not for every failure."""
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    _project(tmp, implemented=1, total=4)
    spec = _spec()
    baseline = _measure(tmp, spec)
    criteria = criteria_for([spec], baseline=baseline)

    ctl, turn, _ = _controller(tmp, [lambda p: _timeout(tool_calls=4),
                                     lambda p: _ok()],
                               spec=spec, baseline=baseline, max_attempts=2)
    _run(ctl.converge(_objective(tmp, criteria)))
    second = turn.calls[1]
    assert not any("passing checks" in line for line in second.evidence)


def test_the_continuation_directive_never_replaces_the_generic_one_by_default():
    for rung in ("REPAIR", "GLOBAL_REPLAN", "DIAGNOSTIC"):
        assert directive_for(rung) == directive_for(rung, progress=None)
        assert directive_for(rung, progress=NO_PROGRESS) == directive_for(rung)
        assert (directive_for(rung, progress=UNDETERMINABLE)
                == directive_for(rung))
    assert (directive_for("REPAIR", progress=MEANINGFUL)
            != directive_for("REPAIR"))


def test_every_rung_the_ladder_can_choose_still_has_a_directive():
    for rung in RecoveryRung:
        if rung is RecoveryRung.HUMAN:
            continue
        assert directive_for(rung.name), rung.name
        assert directive_for(rung.name, progress=MEANINGFUL), rung.name


# ── Non-vacuity ─────────────────────────────────────────────────────────


def test_the_p1_assertions_are_not_vacuous(tmp_path):
    """The P1 shape, with the progress removed, must NOT select continuation.

    If this test passed as well, P1's assertion would be satisfied by anything.
    """
    _project(tmp_path, implemented=1, total=4)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline)

    def no_change(_ws):
        return _timeout(tool_calls=10)

    ctl, _, _ = _controller(tmp_path, [no_change, lambda ws: _ok()], spec=spec,
                            baseline=baseline, max_attempts=2)
    result = _run(ctl.converge(_objective(tmp_path, criteria)))
    assert result.attempts[0].progress != MEANINGFUL.value
    assert result.attempts[1].rung != "REPAIR"
