"""Multi-turn productive recovery and completion authority (ADR-0047).

Two questions, one file, because they are one decision:

**F60 — completion authority.** A turn fails (a timeout), and the harness's own
independent measurement says every objective criterion holds. Is the *objective*
failed? No: a failed *turn* is not a failed *objective*. `derive_goal_state` now
lets a `PASS` verdict decide, and demotes `turn_succeeded` from a precondition to
a recorded fact.

**F61 — multi-turn productive recovery.** R5 forbade *a rung* from repeating,
which was the right unit while a rung could only carry a failure. A rung that
produced `MEANINGFUL_PROGRESS` carries a *success that has not finished*, and
refusing to continue it guarantees failure. The unit is now **the same strategy
against materially unchanged state**, and `MEANINGFUL_PROGRESS` is the
host-owned witness that the state changed.

Everything here is deterministic: scripted turns, a real pytest project for the
measurements, and no model.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from wisp.core.acceptance import Verdict, evaluate
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
    read_journal_baseline,
)
from wisp.core.events import DENIAL_POLICY_DENIED
from wisp.core.goal import GoalState, TerminalOutcome, derive_goal_state
from wisp.core.progress import ProgressVerdict, evaluate_progress
from wisp.core.recovery import (
    PRODUCTIVE_BUDGET,
    BudgetGovernor,
    FailureClass,
    RecoveryBudget,
    RecoveryLadder,
    RecoveryRung,
)

MEANINGFUL = ProgressVerdict.MEANINGFUL_PROGRESS
NO_PROGRESS = ProgressVerdict.NO_PROGRESS
UNDETERMINABLE = ProgressVerdict.PROGRESS_UNDETERMINABLE

PYTEST = (sys.executable, "-m", "pytest", "tests", "-q",
          "-p", "no:cacheprovider")

OBJECTIVE_TEXT = "implement the remaining functions so the suite passes"

PYPROJECT = """[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
"""


# ── Harness ─────────────────────────────────────────────────────────────


def _project(root: Path, *, implemented: int, total: int) -> None:
    """`calc.py` with `total` functions, the first `implemented` working.

    Only `calc.py` is rewritten afterwards, so a legitimate step never touches
    the declared verification inputs — which is what makes the tamper probes
    meaningful rather than incidental.
    """
    (root / "tests").mkdir(parents=True, exist_ok=True)
    _implement(root, implemented=implemented, total=total)
    test_file = root / "tests" / "test_calc.py"
    if not test_file.exists():
        test_file.write_text(
            "import calc\n\n" + "\n\n".join(
                f"def test_f{i}():\n    assert calc.f{i}() == {i}\n"
                for i in range(total)), encoding="utf-8")
        (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")


def _implement(root: Path, *, implemented: int, total: int) -> None:
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


def _failed(measurement: Measurement) -> int:
    return int(measurement.observations["verify:cmd0"].get("failed") or 0)


def _timeout(**kw) -> TurnObservation:
    base = dict(turn_succeeded=False, terminal_outcome="failed",
                failure_code="E1101", failure_message="Turn timed out after 5s",
                failure_recoverable=False)
    base.update(kw)
    return TurnObservation(**base)


def _ok(**kw) -> TurnObservation:
    base = dict(turn_succeeded=True, terminal_outcome="succeeded")
    base.update(kw)
    return TurnObservation(**base)


class ScriptedTurn:
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


def _controller(ws: Path, script, *, spec=None, max_attempts=3, baseline=None,
                journal=None, ladder=None):
    spec = spec or _spec()
    turn = ScriptedTurn(ws, script)
    ctl = ConvergenceController(
        run_turn=turn, probe=CommandProbe([spec]), max_attempts=max_attempts,
        baseline=baseline, journal_path=journal, ladder=ladder)
    return ctl, turn, spec


def _run(coro):
    return asyncio.run(coro)


def _objective(ws: Path, criteria) -> Objective:
    return Objective(goal=OBJECTIVE_TEXT, workspace=str(ws), criteria=criteria)


def _fixture(tmp_path: Path, *, implemented: int, total: int):
    """The standard starting point: a red project, its baseline, its criteria."""
    _project(tmp_path, implemented=implemented, total=total)
    spec = _spec()
    baseline = _measure(tmp_path, spec)
    criteria = criteria_for([spec], baseline=baseline, promote_absolute=True)
    return spec, baseline, criteria


def _ladder(productive: int = 2) -> RecoveryLadder:
    return RecoveryLadder(governor=BudgetGovernor(
        budget=RecoveryBudget(productive_continuations=productive)))


# ══════════════════════════════════════════════════════════════════════════
# F60 — completion authority
# ══════════════════════════════════════════════════════════════════════════


class TestF60TheDecisionMatrix:
    """§11's matrix, and it has exactly one interpretation per row.

    Every row is a call to `derive_goal_state` — the ONE authority for the
    objective state — so the matrix cannot drift from the implementation. The
    run-level aggregation is separate and stated once:
    **a run's state is the ladder's escalation if it surrendered, otherwise the
    last attempt's derived state.**
    """

    #: (turn outcome, verdict, progress, expected) — progress is carried only to
    #: make the row's *story* legible; it is not an arbitration input.
    MATRIX = [
        ("succeeded", Verdict.PASS, "any", GoalState.GOAL_MET),
        ("succeeded", Verdict.FAIL, "any", GoalState.GOAL_FAILED),
        ("failed", Verdict.PASS, "meaningful", GoalState.GOAL_MET),
        ("failed", Verdict.PASS, "none", GoalState.GOAL_MET),
        ("failed", Verdict.FAIL, "meaningful", GoalState.GOAL_FAILED),
        ("failed", Verdict.FAIL, "none", GoalState.GOAL_FAILED),
        ("failed", Verdict.INCONCLUSIVE, "meaningful", GoalState.GOAL_FAILED),
        ("failed", Verdict.INCONCLUSIVE, "none", GoalState.GOAL_FAILED),
    ]

    @pytest.mark.parametrize("outcome,verdict,progress,expected", MATRIX)
    def test_each_row_has_one_interpretation(self, outcome, verdict, progress,
                                             expected):
        derived = derive_goal_state(
            terminal_outcome=outcome,
            acceptance_verdict=verdict,
            turn_succeeded=(outcome == "succeeded"),
        )
        assert derived is expected, (
            f"{outcome} + {verdict} + {progress} must be {expected}")

    def test_f60a_and_b_progress_does_not_decide_completion(self):
        """Progress is observational. It never arbitrates the goal state."""
        for progress in (MEANINGFUL, NO_PROGRESS, UNDETERMINABLE, None):
            assert derive_goal_state(
                terminal_outcome=TerminalOutcome.FAILED,
                acceptance_verdict=Verdict.PASS,
                turn_succeeded=False,
                stagnating=False) is GoalState.GOAL_MET

    def test_f60g_a_tampered_input_is_never_a_pass(self, tmp_path):
        """The tamper row of the matrix is **unreachable as a `PASS`**.

        Not "a PASS that we distrust" — the integrity criterion is *required*,
        so a moved verification-input digest makes the verdict `FAIL`. The
        matrix therefore has no `PASS + tampered` row at all, which is a
        stronger statement than handling it.
        """
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=3)
        integrity = [c for c in criteria if c.criteria_id.endswith(":inputs_unchanged")]
        assert integrity and integrity[0].required is True

        (tmp_path / "tests" / "test_calc.py").write_text(
            "import calc\n\n\ndef test_trivial():\n    assert True\n",
            encoding="utf-8")
        tampered = _measure(tmp_path, spec)
        verdict = evaluate(criteria, tampered.evidence, tampered.observations)
        assert verdict.verdict is Verdict.FAIL
        assert "verify:cmd0:inputs_unchanged" in verdict.unmet_criteria
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=verdict.verdict,
            turn_succeeded=False) is GoalState.GOAL_FAILED

    def test_f60h_a_security_violation_outranks_a_pass(self, tmp_path):
        """The security row, at the controller level where the class is known.

        The objective *is* satisfied here — and the run still escalates,
        because a denial means the attempt did not proceed as authorized and
        absorbing it into `GOAL_MET` would launder a security event.
        """
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=2)

        def denied(ws):
            _implement(ws, implemented=2, total=2)
            return _timeout(failure_message=DENIAL_POLICY_DENIED,
                            failure_code=None)

        ctl, turn, _ = _controller(tmp_path, [denied, denied], spec=spec,
                                   baseline=baseline, max_attempts=2)
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert result.attempts[0].verdict == Verdict.PASS.value
        assert result.attempts[0].failure_class == FailureClass.SECURITY.value
        assert result.goal_state is GoalState.ESCALATED_TO_HUMAN
        assert result.escalation is not None
        assert len(turn.calls) == 1

    def test_f60_the_completion_does_not_consume_an_attempt(self, tmp_path):
        """The cost of the old behaviour, measured: one wasted attempt per run."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=2)

        def finish(ws):
            _implement(ws, implemented=2, total=2)
            return _timeout()          # the work is done; the turn is cut off

        ctl, turn, _ = _controller(tmp_path, [finish, finish, finish],
                                   spec=spec, baseline=baseline, max_attempts=3)
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert result.goal_state is GoalState.GOAL_MET
        assert len(turn.calls) == 1
        assert len(result.attempts) == 1


class TestF60DoesNotWeakenTerminalHonesty:
    """The half of ADR-0035's row 3 that did **not** move."""

    def test_a_fatal_error_without_a_pass_is_still_goal_failed(self):
        for verdict in (Verdict.FAIL, Verdict.INCONCLUSIVE, None, "unknown"):
            assert derive_goal_state(
                terminal_outcome=TerminalOutcome.FAILED,
                acceptance_verdict=verdict,
                turn_succeeded=False) is GoalState.GOAL_FAILED

    def test_a_fatal_error_still_outranks_stagnation(self):
        """A *heuristic* must not soften a *fact*.

        This is why the revision is a conjunction rather than a reordering: the
        three preserved rules — fatal > stagnation, stagnation > PASS,
        PASS > fatal — are a cycle, so no ordering of rows can express them.
        """
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=Verdict.INCONCLUSIVE,
            stagnating=True,
            turn_succeeded=False) is GoalState.GOAL_FAILED

    def test_stagnation_still_outranks_a_pass(self):
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.SUCCEEDED,
            acceptance_verdict=Verdict.PASS,
            stagnating=True,
            turn_succeeded=True) is GoalState.GOAL_STAGNATED

    def test_an_incomplete_turn_without_a_verdict_is_unverified(self):
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.INCOMPLETE,
            acceptance_verdict=None,
            turn_succeeded=False) is GoalState.GOAL_UNVERIFIED

    def test_a_recorded_state_is_still_frozen(self):
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=Verdict.PASS,
            turn_succeeded=False,
            already_recorded=GoalState.GOAL_FAILED) is GoalState.GOAL_FAILED


# ══════════════════════════════════════════════════════════════════════════
# F61 — multi-turn productive recovery
# ══════════════════════════════════════════════════════════════════════════


class TestF61TheContinuationContract:
    """§7's C1–C10, each one a test where it is host-checkable."""

    def test_c3_c4_a_productive_attempt_may_continue(self, tmp_path):
        """C3+C4: `MEANINGFUL_PROGRESS` is the witness that the state changed."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)

        def step(n):
            def _step(ws):
                _implement(ws, implemented=n, total=6)
                return _timeout(changed_files=("calc.py",))
            return _step

        ctl, turn, _ = _controller(tmp_path, [step(2), step(4), step(6)],
                                   spec=spec, baseline=baseline, max_attempts=3,
                                   ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        rungs = [a.rung for a in result.attempts]
        assert rungs == [INITIAL_RUNG, "REPAIR", "REPAIR"], rungs
        assert all(a.progress == MEANINGFUL.value for a in result.attempts)

    def test_c6_an_unchanged_strategy_cannot_repeat(self, tmp_path):
        """No progress → the original R5, unchanged. This is the C6 test.

        The strategy identity is host-derived and needs no fingerprint
        mechanism: the journal already carries rung, directive class, evidence
        lines and the measurement digest, and `MEANINGFUL_PROGRESS` is false
        exactly when the measurement did not move.
        """
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)

        def nothing(_ws):
            return _timeout(tool_calls=3)

        ctl, _, _ = _controller(tmp_path, [nothing, nothing, nothing], spec=spec,
                                baseline=baseline, max_attempts=3,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        rungs = [a.rung for a in result.attempts]
        assert len(rungs) == len(set(rungs)), f"a rung repeated: {rungs}"
        assert rungs == [INITIAL_RUNG, "DIAGNOSTIC"]

    def test_c6b_a_changed_measurement_with_a_denial_cannot_repeat(self, tmp_path):
        """`SECURITY` is never widened, so it can never be continued."""
        ladder = _ladder(productive=5)
        assert ladder.legal_rungs(FailureClass.SECURITY,
                                  progress=MEANINGFUL) == []

    def test_c7_the_productive_budget_bounds_the_continuation(self, tmp_path):
        """C7, and the invariant §5 demands: productive recovery terminates."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=12)

        def step(n):
            def _step(ws):
                _implement(ws, implemented=n, total=12)
                return _timeout(changed_files=("calc.py",))
            return _step

        ladder = _ladder(productive=2)
        ctl, turn, _ = _controller(tmp_path, [step(n) for n in (2, 4, 6, 8, 10)],
                                   spec=spec, baseline=baseline, max_attempts=6,
                                   ladder=ladder)
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert len(result.attempts) <= 6
        assert len(turn.calls) <= 6
        repeats = len([a.rung for a in result.attempts]) - len(
            {a.rung for a in result.attempts})
        assert repeats == 2, [a.rung for a in result.attempts]
        assert result.goal_state in (GoalState.GOAL_FAILED,
                                     GoalState.ESCALATED_TO_HUMAN,
                                     GoalState.GOAL_UNVERIFIED)

    def test_c7b_the_productive_budget_is_durable_and_reported(self, tmp_path):
        """§8: the answer to "how many continuations are left?" is readable."""
        ladder = _ladder(productive=3)
        assert ladder.governor.remaining(PRODUCTIVE_BUDGET) == 3
        ladder.decide(FailureClass.ENVIRONMENT, ("e",),
                      progress=MEANINGFUL)          # first use: not a re-choice
        assert ladder.governor.remaining(PRODUCTIVE_BUDGET) == 3
        ladder.decide(FailureClass.ENVIRONMENT, ("e",),
                      progress=MEANINGFUL)          # re-choice: charged
        assert ladder.governor.remaining(PRODUCTIVE_BUDGET) == 2
        assert ladder.governor.snapshot()[PRODUCTIVE_BUDGET] == {
            "remaining": 2, "spent": 1, "total": 3}

    def test_c8_continuation_cannot_bypass_acceptance(self, tmp_path):
        """§12: no rung, and no amount of progress, reaches `GOAL_MET`."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)

        def step(ws):
            _implement(ws, implemented=4, total=6)      # still 2 failing
            return _timeout(changed_files=("calc.py",))

        ctl, _, _ = _controller(tmp_path, [step, step, step], spec=spec,
                                baseline=baseline, max_attempts=3,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert all(a.verdict == Verdict.FAIL.value for a in result.attempts)
        assert not result.converged
        assert result.goal_state is not GoalState.GOAL_MET

    def test_c9_a_moved_input_cannot_unlock_a_continuation(self, tmp_path):
        """Tampering is not progress, so it cannot buy another attempt."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=4)

        def tamper(ws):
            (ws / "tests" / "test_calc.py").write_text(
                "import calc\n\n\ndef test_trivial():\n    assert True\n",
                encoding="utf-8")
            return _timeout(changed_files=("tests/test_calc.py",))

        ctl, _, _ = _controller(tmp_path, [tamper, tamper, tamper], spec=spec,
                                baseline=baseline, max_attempts=3,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert result.attempts[0].progress == UNDETERMINABLE.value
        rungs = [a.rung for a in result.attempts]
        assert len(rungs) == len(set(rungs)), f"a rung repeated: {rungs}"

    def test_c10_every_attempt_is_journaled_before_it_is_authoritative(
            self, tmp_path):
        """§9: progress is not failure erasure. The journal is append-only."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)
        journal = tmp_path / "run.jsonl"

        def step(n):
            def _step(ws):
                _implement(ws, implemented=n, total=6)
                return _timeout(changed_files=("calc.py",))
            return _step

        ctl, _, _ = _controller(tmp_path, [step(n) for n in (2, 4, 5)],
                                spec=spec, baseline=baseline, max_attempts=3,
                                journal=journal, ladder=_ladder(productive=2))
        _run(ctl.converge(_objective(tmp_path, criteria)))

        lines = [json.loads(x) for x in
                 journal.read_text(encoding="utf-8").splitlines() if x.strip()]
        kinds = [x.get("kind") for x in lines]
        assert kinds[0] == "baseline"
        attempts = [x for x in lines if x.get("kind") == "attempt"]
        assert [x["index"] for x in attempts] == [0, 1, 2]
        # Every failure survives, in order, including the ones progress followed.
        assert all(x["turn_succeeded"] is False for x in attempts)
        assert all(x["failure_code"] == "E1101" for x in attempts)
        assert [x["progress"] for x in attempts] == [MEANINGFUL.value] * 3
        assert [x["rung"] for x in attempts] == [INITIAL_RUNG, "REPAIR", "REPAIR"]


class TestF61ContinuationIsNotBlindRetry:
    def test_the_repeat_is_permitted_only_with_progress(self, tmp_path):
        """The rule, stated directly against the ladder."""
        for progress, may_repeat in ((MEANINGFUL, True), (NO_PROGRESS, False),
                                     (UNDETERMINABLE, False), (None, False),
                                     ("not-a-verdict", False)):
            ladder = _ladder(productive=5)
            first = ladder.decide(FailureClass.ENVIRONMENT, ("e",),
                                  progress=MEANINGFUL)
            assert first.rung is RecoveryRung.REPAIR
            again = ladder.legal_rungs(FailureClass.ENVIRONMENT,
                                       progress=progress)
            assert (RecoveryRung.REPAIR in again) is may_repeat, progress

    def test_a_regression_cannot_unlock_a_continuation(self, tmp_path):
        """§10: `progress → regression` is not progress."""
        before = {"verify:cmd0": {"exit": 1, "collected": 2, "failed": 2}}
        after = {"verify:cmd0": {"exit": 1, "collected": 1, "failed": 3}}
        report = evaluate_progress(before, after)
        assert report.verdict is NO_PROGRESS
        ladder = _ladder(productive=5)
        ladder.decide(FailureClass.ENVIRONMENT, ("e",), progress=None)
        assert RecoveryRung.REPAIR not in ladder.legal_rungs(
            FailureClass.ENVIRONMENT, progress=report.verdict)

    def test_an_unexecutable_rung_does_not_stall_the_retry(self, tmp_path):
        """The `exclude` guard: a progress-legalised rung must not loop."""
        ladder = _ladder(productive=5)
        ladder.decide(FailureClass.IMPLEMENTATION, ("e",), progress=MEANINGFUL)
        # ROLLBACK is not executable without a snapshot; the controller excludes
        # it and asks again rather than choosing it forever.
        seen = ladder.legal_rungs(FailureClass.IMPLEMENTATION,
                                  progress=MEANINGFUL,
                                  exclude=[RecoveryRung.ROLLBACK])
        assert RecoveryRung.ROLLBACK not in seen


# ══════════════════════════════════════════════════════════════════════════
# Resume / replay
# ══════════════════════════════════════════════════════════════════════════


class TestResumeReproducesTheContinuationDecision:
    def test_interrupted_equals_uninterrupted_across_two_continuations(
            self, tmp_path):
        """§13 case 18, at the hardest point: two productive continuations."""
        spec = _spec()

        def step(n):
            def _step(ws):
                _implement(ws, implemented=n, total=12)
                return _timeout(changed_files=("calc.py",))
            return _step

        def finish(ws):
            _implement(ws, implemented=12, total=12)
            return _ok(changed_files=("calc.py",))

        # (a) uninterrupted
        _project(tmp_path, implemented=0, total=12)
        baseline = _measure(tmp_path, spec)
        criteria = criteria_for([spec], baseline=baseline, promote_absolute=True)
        whole_ctl, _, _ = _controller(
            tmp_path, [step(2), step(4), step(8), finish], spec=spec,
            baseline=baseline, max_attempts=4, ladder=_ladder(productive=2))
        whole = _run(whole_ctl.converge(_objective(tmp_path, criteria)))
        assert whole.goal_state is GoalState.GOAL_MET
        assert [a.rung for a in whole.attempts] == [
            INITIAL_RUNG, "REPAIR", "REPAIR", "REPAIR"]

        # (b) interrupted after attempt 1, resumed in a fresh controller
        _project(tmp_path, implemented=0, total=12)
        journal = tmp_path / "run.jsonl"
        first, _, _ = _controller(
            tmp_path, [step(2), step(4)], spec=spec, baseline=baseline,
            max_attempts=2, journal=journal, ladder=_ladder(productive=2))
        _run(first.converge(_objective(tmp_path, criteria)))
        recovered = read_journal_baseline(journal)
        assert recovered is not None and _failed(recovered) == 12

        resumed, turn2, _ = _controller(
            tmp_path, [step(8), finish], spec=spec, baseline=recovered,
            max_attempts=4, journal=journal, ladder=_ladder(productive=2))
        after = _run(resumed.converge(_objective(tmp_path, criteria), resume=True))

        assert len(after.attempts) == 4
        assert len(turn2.calls) == 2, "completed attempts must not be re-run"
        assert [a.rung for a in after.attempts] == [a.rung for a in whole.attempts]
        assert [a.progress for a in after.attempts] == [
            a.progress for a in whole.attempts]
        assert after.goal_state is whole.goal_state is GoalState.GOAL_MET

    def test_a_torn_journal_does_not_break_the_continuation(self, tmp_path):
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)
        journal = tmp_path / "run.jsonl"

        def step(ws):
            _implement(ws, implemented=3, total=6)
            return _timeout(changed_files=("calc.py",))

        ctl, _, _ = _controller(tmp_path, [step], spec=spec, baseline=baseline,
                                max_attempts=1, journal=journal,
                                ladder=_ladder(productive=2))
        _run(ctl.converge(_objective(tmp_path, criteria)))
        with journal.open("a", encoding="utf-8") as fh:
            fh.write('{"kind": "attempt", "index": 1, "ru')      # torn

        resumed, turn2, _ = _controller(
            tmp_path, [step], spec=spec, baseline=baseline, max_attempts=2,
            journal=journal, ladder=_ladder(productive=2))
        _run(resumed.converge(_objective(tmp_path, criteria), resume=True))
        assert [a.index for a in resumed.attempts] == [0, 1]
        assert len(turn2.calls) == 1
        assert turn2.calls[0].attempt == 1


# ══════════════════════════════════════════════════════════════════════════
# Stagnation interaction (§10, cases 21–25)
# ══════════════════════════════════════════════════════════════════════════


class TestStagnationInteraction:
    def test_21_genuine_progress_is_not_stagnation(self, tmp_path):
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)

        def step(n):
            def _step(ws):
                _implement(ws, implemented=n, total=6)
                return _timeout(changed_files=("calc.py",))
            return _step

        ctl, _, _ = _controller(tmp_path, [step(2), step(4), step(6)],
                                spec=spec, baseline=baseline, max_attempts=3,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert all(a.failure_class != FailureClass.STAGNATION.value
                   for a in result.attempts)

    def test_22_a_repeated_unchanged_measurement_is_stagnation(self, tmp_path):
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)

        def nothing(_ws):
            # A *successful* turn that changes nothing. No failure code, so the
            # stagnation observation is what is left to name it — a timeout
            # would (correctly) be classified as the environment fault it is.
            return _ok(tool_calls=2)

        ctl, _, _ = _controller(tmp_path, [nothing, nothing, nothing],
                                spec=spec, baseline=baseline, max_attempts=3,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert result.attempts[1].failure_class == FailureClass.STAGNATION.value

    def test_23_tiny_churn_cannot_reset_stagnation_into_progress(self, tmp_path):
        """A cosmetic rewrite changes the digest but not the measurement."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)

        def churn(ws):
            text = (ws / "calc.py").read_text(encoding="utf-8")
            (ws / "calc.py").write_text(
                "\n".join(line + "  # touch" for line in text.splitlines())
                + "\n", encoding="utf-8")
            return _timeout(tool_calls=2, changed_files=("calc.py",))

        ctl, _, _ = _controller(tmp_path, [churn, churn, churn], spec=spec,
                                baseline=baseline, max_attempts=3,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert all(a.progress == NO_PROGRESS.value for a in result.attempts)
        rungs = [a.rung for a in result.attempts]
        assert len(rungs) == len(set(rungs)), f"churn unlocked a repeat: {rungs}"

    def test_24_a_regression_is_not_progress_and_not_stagnation(self, tmp_path):
        """It is its own observation: `NO_PROGRESS` with a regression signal."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=3, total=6)

        def regress(ws):
            _implement(ws, implemented=1, total=6)     # 3 failing → 5 failing
            return _timeout(changed_files=("calc.py",))

        ctl, _, _ = _controller(tmp_path, [regress, regress, regress], spec=spec,
                                baseline=baseline, max_attempts=3,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        first = result.attempts[0]
        assert first.progress == NO_PROGRESS.value
        assert any("failing checks 3→5" in s for s in first.progress_signals)
        assert RecoveryRung.REPAIR.name not in [a.rung for a in result.attempts[1:]]

    def test_25_the_stagnation_latch_is_untouched(self):
        """ADR-0037's monotonic latch is not reachable from this mechanism."""
        import inspect

        from wisp.core import stagnation

        source = inspect.getsource(stagnation)
        assert "productive_continuation" not in source
        assert "PRODUCTIVE_BUDGET" not in source


class TestTheWitnessIsDeterministic:
    """F63: the stagnation witness was a function of *when* it was taken.

    `Measurement.digest` hashed the whole payload, including `output_tail` —
    which ends with the command's elapsed time (`"3 failed in 0.04s"`). Two
    probes of the same unchanged workspace therefore digested differently, so
    `repeated = digest == stagnation_witness` was a coin flip: a genuinely
    stagnant run could classify as `IMPLEMENTATION` and take `REPAIR` rather
    than `GLOBAL_REPLAN`, and ADR-0046 R10's replay determinism did not hold.
    """

    def test_the_same_state_digests_the_same(self, tmp_path):
        spec, baseline, _ = _fixture(tmp_path, implemented=0, total=3)
        probes = [_measure(tmp_path, spec) for _ in range(3)]
        assert len({p.digest for p in probes}) == 1, (
            "the same state must produce the same witness")

    def test_evidence_identity_is_a_function_of_the_state(self, tmp_path):
        spec, _, _ = _fixture(tmp_path, implemented=0, total=3)
        a, b = _measure(tmp_path, spec), _measure(tmp_path, spec)
        assert ([e.evidence_id for e in a.evidence]
                == [e.evidence_id for e in b.evidence])
        assert ([e.content_hash for e in a.evidence]
                == [e.content_hash for e in b.evidence])

    def test_a_changed_state_digests_differently(self, tmp_path):
        spec, _, _ = _fixture(tmp_path, implemented=0, total=3)
        before = _measure(tmp_path, spec)
        _implement(tmp_path, implemented=1, total=3)
        assert _measure(tmp_path, spec).digest != before.digest

    def test_the_prose_excerpt_is_still_recorded(self, tmp_path):
        """The fix is a *projection*, not a deletion — the excerpt is evidence."""
        spec, _, _ = _fixture(tmp_path, implemented=0, total=3)
        payload = _measure(tmp_path, spec).observations["verify:cmd0"]
        assert payload["output_tail"], "the human-readable excerpt is kept"
        assert "failed" in payload["output_tail"]

    def test_stagnation_is_detected_reliably(self, tmp_path):
        """The predicate this protects, end to end and without flakiness."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=4)

        def nothing(_ws):
            return _ok(tool_calls=2)

        ctl, _, _ = _controller(tmp_path, [nothing, nothing, nothing],
                                spec=spec, baseline=baseline, max_attempts=3,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert result.attempts[1].failure_class == FailureClass.STAGNATION.value
        assert result.attempts[2].failure_class == FailureClass.STAGNATION.value


# ══════════════════════════════════════════════════════════════════════════
# Falsification probes (§14)
# ══════════════════════════════════════════════════════════════════════════


class TestFalsificationProbes:
    def test_probe_a_a_timed_out_turn_that_satisfies_everything(self, tmp_path):
        """Turn failure and objective satisfaction are separately readable."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=3)

        def finish(ws):
            _implement(ws, implemented=3, total=3)
            return _timeout()

        ctl, _, _ = _controller(tmp_path, [finish], spec=spec,
                                baseline=baseline, max_attempts=2)
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        record = result.attempts[0]
        assert record.observation.turn_succeeded is False
        assert record.verdict == Verdict.PASS.value
        assert result.goal_state is GoalState.GOAL_MET

    def test_probe_b_three_consecutive_productive_turns(self, tmp_path):
        """It continues safely, and it stops when the budget is spent."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=12)

        def step(n):
            def _step(ws):
                _implement(ws, implemented=n, total=12)
                return _timeout(changed_files=("calc.py",))
            return _step

        ladder = _ladder(productive=2)
        ctl, turn, _ = _controller(tmp_path, [step(n) for n in (3, 6, 9, 11)],
                                   spec=spec, baseline=baseline, max_attempts=4,
                                   ladder=ladder)
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert len(turn.calls) == 4, "it continued three times, then stopped"
        assert [a.rung for a in result.attempts] == [
            INITIAL_RUNG, "REPAIR", "REPAIR", "REPAIR"]
        assert ladder.governor.exhausted(PRODUCTIVE_BUDGET)

    def test_probe_c_repeated_edits_without_improvement_terminate(self, tmp_path):
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)

        def edit(_ws):
            return _timeout(tool_calls=9, changed_files=("calc.py",))

        ctl, turn, _ = _controller(tmp_path, [edit] * 5, spec=spec,
                                   baseline=baseline, max_attempts=5,
                                   ladder=_ladder(productive=5))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert len(turn.calls) <= 3, "unproductive edits must not keep it alive"
        assert not result.converged

    def test_probe_d_alternating_progress_and_regression(self, tmp_path):
        """`progress → regression → progress` must not read as infinite progress."""
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=8)

        def cycle(n):
            def _step(ws):
                _implement(ws, implemented=n, total=8)
                return _timeout(changed_files=("calc.py",))
            return _step

        # 4 → 2 (progress) → 6 (regression) → 3 (progress) → 7 (regression)
        ladder = _ladder(productive=5)
        ctl, turn, _ = _controller(tmp_path, [cycle(n) for n in (4, 2, 3, 1)],
                                   spec=spec, baseline=baseline, max_attempts=4,
                                   ladder=ladder)
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        progresses = [a.progress for a in result.attempts]
        assert progresses[0] == MEANINGFUL.value
        assert progresses[1] == NO_PROGRESS.value, "a regression is not progress"
        assert not result.converged

    def test_probe_e_restart_between_every_continuation(self, tmp_path):
        """One attempt per process, four times over. Same decisions."""
        spec = _spec()

        def step(n):
            def _step(ws):
                _implement(ws, implemented=n, total=10)
                return _timeout(changed_files=("calc.py",))
            return _step

        _project(tmp_path, implemented=0, total=10)
        baseline = _measure(tmp_path, spec)
        criteria = criteria_for([spec], baseline=baseline, promote_absolute=True)
        journal = tmp_path / "run.jsonl"

        def once(script, attempts, ladder):
            ctl, turn, _ = _controller(tmp_path, script, spec=spec,
                                       baseline=baseline, max_attempts=attempts,
                                       journal=journal, ladder=ladder)
            _run(ctl.converge(_objective(tmp_path, criteria), resume=True))
            return turn

        # Each process makes exactly one NEW attempt and rebuilds the rest.
        once([step(2)], 1, _ladder(productive=2))
        once([step(5)], 2, _ladder(productive=2))
        t3 = once([step(8)], 3, _ladder(productive=2))
        assert len(t3.calls) == 1 and t3.calls[0].attempt == 2
        t4 = once([step(10)], 4, _ladder(productive=2))
        assert len(t4.calls) == 1 and t4.calls[0].attempt == 3

        lines = [json.loads(x) for x in
                 journal.read_text(encoding="utf-8").splitlines() if x.strip()]
        attempts = [x for x in lines if x.get("kind") == "attempt"]
        assert [x["index"] for x in attempts] == [0, 1, 2, 3]
        assert [x["rung"] for x in attempts] == [
            INITIAL_RUNG, "REPAIR", "REPAIR", "REPAIR"]

    def test_probe_f_a_corrupt_final_record_is_survivable(self, tmp_path):
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=6)
        journal = tmp_path / "run.jsonl"

        def step(ws):
            _implement(ws, implemented=3, total=6)
            return _timeout(changed_files=("calc.py",))

        ctl, _, _ = _controller(tmp_path, [step], spec=spec, baseline=baseline,
                                max_attempts=1, journal=journal,
                                ladder=_ladder(productive=2))
        _run(ctl.converge(_objective(tmp_path, criteria)))
        with journal.open("a", encoding="utf-8") as fh:
            fh.write('{"kind": "attempt", "index": 1, "progress": "meani')

        resumed, turn2, _ = _controller(tmp_path, [step], spec=spec,
                                        baseline=baseline, max_attempts=2,
                                        journal=journal,
                                        ladder=_ladder(productive=2))
        result = _run(resumed.converge(_objective(tmp_path, criteria),
                                      resume=True))
        assert [a.index for a in result.attempts] == [0, 1]
        assert turn2.calls[0].attempt == 1

    def test_probe_g_a_moved_input_still_prevents_success(self, tmp_path):
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=4)

        def tamper(ws):
            (ws / "tests" / "test_calc.py").write_text(
                "import calc\n\n\ndef test_ok():\n    assert True\n",
                encoding="utf-8")
            return _ok(changed_files=("tests/test_calc.py",))

        ctl, _, _ = _controller(tmp_path, [tamper, tamper], spec=spec,
                                baseline=baseline, max_attempts=2,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert not result.converged
        assert result.goal_state is not GoalState.GOAL_MET

    def test_probe_h_a_provider_only_timeout_stays_infrastructure(self, tmp_path):
        spec, baseline, criteria = _fixture(tmp_path, implemented=0, total=4)

        def stall(_ws):
            return _timeout(tool_calls=0, changed_files=(),
                            failure_message="provider stream stalled")

        ctl, _, _ = _controller(tmp_path, [stall, stall], spec=spec,
                                baseline=baseline, max_attempts=2,
                                ladder=_ladder(productive=2))
        result = _run(ctl.converge(_objective(tmp_path, criteria)))
        assert result.attempts[0].progress == NO_PROGRESS.value
        assert result.goal_state is not GoalState.GOAL_MET
        assert result.attempts[1].rung == "DIAGNOSTIC"

    def test_probe_i_a_denial_can_never_be_widened(self, tmp_path):
        """No progress observation opens a forbidden path.

        Asserted as *equality of the candidate sets*, not emptiness:
        `DIAGNOSTIC` is legitimately legal for `REPEATED`/`STAGNATION` with no
        progress at all, so an emptiness assertion would fail for the wrong
        reason. What must hold is that progress adds nothing.
        """
        for fc in (FailureClass.SECURITY, FailureClass.REPEATED,
                   FailureClass.STAGNATION):
            plain = _ladder(productive=9).legal_rungs(fc)
            for progress in (MEANINGFUL, NO_PROGRESS, UNDETERMINABLE, None):
                assert _ladder(productive=9).legal_rungs(
                    fc, progress=progress) == plain, (fc, progress)

    def test_probe_j_the_model_cannot_declare_progress_or_completion(self):
        """Nothing in the arbitration reads model text.

        `evaluate_progress` is pure over probe payloads, and `derive_goal_state`
        is pure over established facts. Asserted structurally, because "it
        happens not to" is not a property.
        """
        import inspect

        from wisp.core import progress as progress_module

        source = inspect.getsource(progress_module)
        for forbidden in ("message", "content", "assistant", "response"):
            assert f'"{forbidden}"' not in source, forbidden
        # And the goal arbiter takes no verdict from a model.
        assert "model" not in inspect.getsource(derive_goal_state).lower()
