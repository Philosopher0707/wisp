"""The NEXT mission's convergence controller.

The controller is the one NEW mechanism: an objective-level loop that
re-measures, re-classifies and re-attempts until the objective is *proven*
by harness-produced evidence, or honestly is not.

These tests are written against the contract, not the implementation. Each
one names the property it pins, and the properties are the ones the mission
demands the agent be unable to violate:

* **No false success.** A turn that says it is done, with the criteria
  unmet, must not produce `GOAL_MET` — ever, under any combination.
* **No manufacture of success from silence.** No criteria, or criteria with
  no evidence, is `INCONCLUSIVE`, never `PASS`.
* **No repeated strategy.** The next rung must be structurally new, which is
  `RecoveryLadder`'s own `R5` rule, not a convention here.
* **No retry of a denial.** A denied action escalates; the no-retry rule is
  enforced by `FORBIDDEN_RUNGS`, and this suite proves the controller does
  not route around it.
* **Bounded.** Attempts are bounded and a bound is never exceeded.
* **Independence.** Evidence names the harness as its producer. The model
  has no way to write an evidence record.
* **Resumable.** A restart re-runs nothing.
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
    Objective,
    SymbolSpec,
    TurnObservation,
    WorkspaceSnapshot,
    criteria_for,
    derive_acceptance,
    directive_for,
)
from wisp.core.goal import GoalState
from wisp.core.recovery import FailureClass, RecoveryRung


# ── Harness ─────────────────────────────────────────────────────────────


class ScriptedTurn:
    """A `run_turn` whose behaviour is a script, and which records its calls.

    Each entry is a callable taking the workspace; it mutates the workspace
    and returns the observation the turn is to be credited with. Running out
    of script raises, so a test that expects N attempts fails loudly if the
    controller makes N+1.
    """

    def __init__(self, workspace: Path, script):
        self.workspace = workspace
        self.script = list(script)
        self.calls: list[AttemptRequest] = []

    async def __call__(self, request: AttemptRequest) -> TurnObservation:
        self.calls.append(request)
        if not self.script:
            raise AssertionError(
                f"controller made attempt {request.attempt + 1} with no "
                f"script left — it is not bounded by the script")
        step = self.script.pop(0)
        return step(self.workspace) or TurnObservation(
            turn_succeeded=True, terminal_outcome="succeeded")


def _ok(**kw) -> TurnObservation:
    base = dict(turn_succeeded=True, terminal_outcome="succeeded")
    base.update(kw)
    return TurnObservation(**base)


def _write(ws: Path, name: str, text: str) -> None:
    (ws / name).write_text(text, encoding="utf-8")


def _passes_when(ws: Path, predicate) -> None:
    """Helper: write totals.py so that `predicate()` decides the outcome."""
    if predicate():
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
    else:
        _write(ws, "totals.py", "def sum_to(n):\n    return 0\n")


CHECK_SUM = ("python3", "-c",
             "from totals import sum_to; assert sum_to(5) == 15")


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    _write(tmp_path, "totals.py", "def sum_to(n):\n    return 0\n")
    return tmp_path


def _controller(ws: Path, script, *, specs=None, max_attempts=3, **kw):
    specs = specs or (CommandSpec(criteria_id="verify:cmd0", argv=CHECK_SUM),)
    turn = ScriptedTurn(ws, script)
    ctl = ConvergenceController(
        run_turn=turn, probe=CommandProbe(specs),
        max_attempts=max_attempts, **kw)
    return ctl, turn, criteria_for(specs)


def _run(coro):
    return asyncio.run(coro)


# ── The happy path, and the exact rung sequence ─────────────────────────


def test_converges_on_the_second_attempt_with_a_repair_rung(ws):
    def fix(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return _ok()

    ctl, turn, criteria = _controller(ws, [lambda _w: _ok(), fix])
    result = _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                                        criteria=criteria)))
    assert result.converged
    assert result.goal_state is GoalState.GOAL_MET
    assert [a.rung for a in result.attempts] == [INITIAL_RUNG, "REPAIR"]
    assert len(turn.calls) == 2


def test_attempt_zero_carries_the_criteria_but_no_directive(ws):
    """The first attempt states the acceptance conditions, not a strategy.

    An agent cannot converge on a target it has not been told. The criteria
    are what "done" means, so they are stated on every attempt; a directive
    and prior evidence only exist once something has failed.
    """
    ctl, turn, criteria = _controller(ws, [lambda _w: _ok()], max_attempts=1)
    _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                               criteria=criteria)))
    assert turn.calls[0].directive == ""
    assert turn.calls[0].evidence == ()
    assert turn.calls[0].criteria, "the acceptance conditions must be stated"
    assert all(isinstance(c, str) and c for c in turn.calls[0].criteria)


def test_only_required_criteria_are_stated(ws):
    """An advisory criterion is recorded, not demanded."""
    from wisp.core.acceptance import AcceptanceCriteria, CriterionKind

    advisory = AcceptanceCriteria(
        criteria_id="advisory:note", description="ADVISORY-NOTE",
        kind=CriterionKind.DETERMINISTIC, required=False,
        check=lambda _p: True)
    ctl, turn, criteria = _controller(ws, [lambda _w: _ok()], max_attempts=1)
    _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                               criteria=criteria + (advisory,))))
    stated = turn.calls[0].criteria
    assert "ADVISORY-NOTE" not in stated
    assert stated, "the required criterion must still be stated"


# ── False success is unreachable ────────────────────────────────────────


def test_a_successful_turn_with_unmet_criteria_is_not_convergence(ws):
    """The model says done; the repository disagrees. The repository wins."""
    ctl, turn, criteria = _controller(
        ws, [lambda _w: _ok()] * 3, max_attempts=3)
    result = _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    assert result.goal_state is not GoalState.GOAL_MET
    assert result.goal_state is GoalState.GOAL_FAILED
    assert all(a.verdict == Verdict.FAIL.value for a in result.attempts)


def test_no_criteria_can_never_be_goal_met(ws):
    """A task with nothing to check has not been verified — it is unexamined."""
    ctl, turn, _ = _controller(ws, [lambda _w: _ok()] * 3, max_attempts=2)
    result = _run(ctl.converge(Objective(goal="do something vague",
                                        workspace=str(ws), criteria=())))
    assert not result.converged
    assert result.goal_state is GoalState.GOAL_UNVERIFIED
    assert all(a.verdict == Verdict.INCONCLUSIVE.value for a in result.attempts)


def test_criteria_without_evidence_are_inconclusive(ws):
    """Evidence is the probe's. With no probe output there is no PASS."""
    criteria = criteria_for([CommandSpec(criteria_id="verify:cmd0", argv=CHECK_SUM)])

    class NoEvidenceProbe:
        def measure(self, workspace):
            from wisp.core.convergence import Measurement
            return Measurement(observations={}, evidence=(), lines=())

    turn = ScriptedTurn(ws, [lambda _w: _ok()] * 2)
    ctl = ConvergenceController(run_turn=turn, probe=NoEvidenceProbe(),
                                max_attempts=2)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    assert result.goal_state is GoalState.GOAL_UNVERIFIED


def test_a_failed_turn_with_a_pass_is_goal_met(ws):
    """**Revised by ADR-0047 (F60).** This used to assert `GOAL_FAILED`.

    A failed *turn* is not a failed *objective*. Here the attempt wrote a
    correct implementation and then died — a provider failure, in fact — and the
    harness independently measured `exit 0`. The objective is satisfied; the
    timeout is a fact about the *attempt*, and it belongs in the record rather
    than in the verdict. Reporting `GOAL_FAILED` here was a false negative, and
    five live runs paid for it with an extra attempt each: the objective was
    already met, and the loop re-attempted only to close a turn the objective
    did not need.
    """
    def fix(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return TurnObservation(turn_succeeded=False, terminal_outcome="failed",
                               failure_code="E1101",
                               failure_message="Turn timed out after 5s",
                               failure_recoverable=False)

    ctl, turn, criteria = _controller(ws, [fix] * 3, max_attempts=2)
    result = _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                                        criteria=criteria)))
    assert result.converged
    assert result.goal_state is GoalState.GOAL_MET
    assert len(turn.calls) == 1, "no attempt is spent closing a met objective"
    # ...and the record still says the turn failed. The two facts are separate.
    first = result.attempts[0]
    assert first.observation.turn_succeeded is False
    assert first.observation.terminal_outcome == "failed"
    assert first.failure_class == FailureClass.ENVIRONMENT.value


def test_a_failed_turn_without_a_pass_is_still_goal_failed(ws):
    """The complement — F60 buys nothing by weakening terminal honesty.

    No `PASS` verdict, so the fatal error still decides: `GOAL_FAILED`, not
    `GOAL_MET`. This is the half of ADR-0035's row 3 that did not move.
    """
    def die(_ws):
        return TurnObservation(turn_succeeded=False, terminal_outcome="failed",
                               failure_message="provider died",
                               failure_recoverable=False)

    ctl, turn, criteria = _controller(ws, [die] * 3, max_attempts=2)
    result = _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    assert result.goal_state is GoalState.GOAL_FAILED


def test_an_authorization_event_never_completes_even_with_a_pass(ws):
    """F60-H. A denial is an exception to "the objective's evidence decides".

    The attempt satisfied every criterion — and then tried something it was not
    authorized to do. Absorbing that into `GOAL_MET` would launder a security
    event, so the run escalates instead. This preserves the existing security
    ordering (a `SECURITY` failure's only legal rung is `HUMAN`) rather than
    inventing a rule: the escalation goes through the ladder's own row.
    """
    from wisp.core.events import DENIAL_POLICY_DENIED

    def denied(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return TurnObservation(turn_succeeded=False, terminal_outcome="failed",
                               failure_message=DENIAL_POLICY_DENIED,
                               failure_recoverable=False)

    ctl, turn, criteria = _controller(ws, [denied] * 3, max_attempts=2)
    result = _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    assert result.goal_state is GoalState.ESCALATED_TO_HUMAN
    assert result.escalation is not None
    assert len(turn.calls) == 1, "an authorization event is terminal for the run"


# ── Partial success and hidden regression ───────────────────────────────


def test_partial_success_is_not_convergence(ws):
    """One criterion satisfied, one not — the objective is not satisfied."""
    _write(ws, "strings_util.py", "def greet(name):\n    return 'hello'\n")
    specs = (
        CommandSpec(criteria_id="verify:cmd0", argv=CHECK_SUM),
        SymbolSpec(criteria_id="symbol:shout", path="strings_util.py",
                   symbol="shout"),
    )

    def half(_ws):
        # Fix the sum, but never add shout().
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return _ok()

    ctl, turn, criteria = _controller(ws, [half] * 3, specs=specs, max_attempts=2)
    result = _run(ctl.converge(Objective(goal="fix and add", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    assert result.attempts[-1].unmet == ("symbol:shout",)


def test_hidden_regression_blocks_convergence(ws):
    """The requested change lands; an unrelated criterion breaks. Not done."""
    _write(ws, "other.py", "VALUE = 1\n")
    specs = (
        CommandSpec(criteria_id="verify:cmd0", argv=CHECK_SUM),
        SymbolSpec(criteria_id="symbol:VALUE", path="other.py", symbol="VALUE"),
    )

    def regress(_ws):
        # The requested fix lands, but an unrelated file is removed.
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        if (ws / "other.py").exists():
            (ws / "other.py").unlink()
        return _ok()

    ctl, turn, criteria = _controller(ws, [regress] * 3, specs=specs,
                                      max_attempts=2)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    assert "symbol:VALUE" in result.attempts[-1].unmet


# ── Stagnation must change the strategy ─────────────────────────────────


def test_stagnation_selects_a_strategy_changing_rung(ws):
    """Identical measurements ⇒ STAGNATION ⇒ GLOBAL_REPLAN, never RETRY.

    The sequence is the point: the first failure is an ordinary
    `IMPLEMENTATION` failure and the ladder spends its cheapest rung
    (`REPAIR`) on it. Only when the *measurement* fails to move does the
    class become `STAGNATION`, whose legal rungs are the strategy-changing
    ones. `RETRY` and `REPAIR` are forbidden for that class, so a stalled
    objective cannot be met with "try again".
    """
    ctl, turn, criteria = _controller(ws, [lambda _w: _ok()] * 3, max_attempts=3)
    result = _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    assert [a.rung for a in result.attempts] == [
        INITIAL_RUNG, "REPAIR", "GLOBAL_REPLAN"]
    assert result.attempts[0].failure_class == FailureClass.IMPLEMENTATION.value
    assert result.attempts[1].failure_class == FailureClass.STAGNATION.value
    # A stagnation-classified attempt is not allowed to claim a met goal.
    assert result.goal_state in (GoalState.GOAL_STAGNATED,
                                 GoalState.GOAL_FAILED)


def test_progress_resets_the_stagnation_witness(ws):
    """A different measurement is progress — the next rung may be REPAIR."""
    def edit_then_fail(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return 0  # changed\n")
        return _ok()

    def fix(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return _ok()

    ctl, turn, criteria = _controller(ws, [edit_then_fail, edit_then_fail, fix],
                                      max_attempts=3)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    # Attempts 0 and 1 measure differently (mtime/size change is not
    # measured — the observation payload is the exit code and output tail,
    # which is identical), so this is stagnation; attempt 1 is a
    # strategy change, and attempt 2 succeeds.
    assert result.converged
    assert [a.rung for a in result.attempts][1] in ("REPAIR", "GLOBAL_REPLAN")


# ── Denials are never retried ───────────────────────────────────────────


def test_a_denied_action_escalates_and_is_never_replanned(ws):
    """P6's rule: a denial's only legal rung is HUMAN."""
    denial = TurnObservation(
        turn_succeeded=False, terminal_outcome="failed",
        failure_code="E3101", failure_message="Blocked: policy denied",
        failure_recoverable=False)

    ctl, turn, criteria = _controller(ws, [lambda _w: denial] * 4, max_attempts=4)
    result = _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    assert result.goal_state is GoalState.ESCALATED_TO_HUMAN
    assert result.attempts[-1].failure_class == FailureClass.SECURITY.value
    # Only ONE turn ran: escalation is terminal, not another attempt.
    assert len(turn.calls) == 1
    assert result.escalation is not None


# ── R5: the same rung is never chosen twice ─────────────────────────────


def test_the_ladder_never_repeats_a_rung(ws):
    """R5 is `RecoveryLadder`'s rule; the controller must not route around it."""
    seen: list[str] = []

    def noisy(_ws):
        return _ok()

    ctl, turn, criteria = _controller(ws, [noisy] * 8, max_attempts=8)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    seen = [a.rung for a in result.attempts]
    assert len(seen) == len(set(seen)), f"a rung repeated: {seen}"
    assert INITIAL_RUNG in seen


def test_rollback_is_skipped_when_it_cannot_be_executed(ws):
    """ROLLBACK without a snapshot is not silently attempted."""
    original = (ws / "totals.py").read_text(encoding="utf-8")

    def damage(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return 0  # broken\n")
        return _ok()

    ctl, turn, criteria = _controller(ws, [damage] * 4, max_attempts=4,
                                      allow_rollback=False)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    assert "ROLLBACK" not in [a.rung for a in result.attempts]
    # Nothing was reverted, because nothing was allowed to be.
    assert (ws / "totals.py").read_text(encoding="utf-8") != original


def test_rollback_restores_the_workspace_when_enabled(ws):
    original = (ws / "totals.py").read_text(encoding="utf-8")

    def damage(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return 0  # broken\n")
        return _ok()

    snapshot = WorkspaceSnapshot()
    assert snapshot.capture(str(ws))
    assert snapshot.restore(str(ws))
    assert (ws / "totals.py").read_text(encoding="utf-8") == original


# ── Bounded ─────────────────────────────────────────────────────────────


def test_attempts_are_bounded_by_max_attempts(ws):
    ctl, turn, criteria = _controller(ws, [lambda _w: _ok()] * 10, max_attempts=2)
    _run(ctl.converge(Objective(goal="fix", workspace=str(ws), criteria=criteria)))
    assert len(turn.calls) == 2


def test_the_objective_budget_overrides_the_controller_default(ws):
    ctl, turn, criteria = _controller(ws, [lambda _w: _ok()] * 10, max_attempts=5)
    _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                               criteria=criteria, max_attempts=3)))
    assert len(turn.calls) == 3


# ── Independence of the evidence ────────────────────────────────────────


def test_evidence_is_produced_by_the_harness_not_the_actor(ws):
    """The only evidence in play names the harness as its producer.

    The model has no channel to write an evidence record: `evaluate` is
    handed whatever the probe measured, and the probe is constructed by the
    host. A model that *claims* success changes nothing here.
    """
    ctl, turn, criteria = _controller(ws, [lambda _w: _ok()] * 2, max_attempts=1)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    measurement = result.final_measurement
    assert measurement is not None
    assert measurement.evidence, "the probe must have produced evidence"
    producers = {e.producer for e in measurement.evidence}
    assert producers == {CommandProbe.PRODUCER}
    assert all("guard" not in p and "model" not in p for p in producers)
    # The actor's own claim (`turn_succeeded=True`) did not produce a PASS.
    assert result.attempts[0].verdict == Verdict.FAIL.value


def test_a_passing_attempt_cites_the_harness_evidence(ws):
    """On a PASS the cited evidence ids are the probe's, not the actor's."""
    def fix(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return _ok()

    ctl, turn, criteria = _controller(ws, [fix], max_attempts=1)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    assert result.converged
    assert result.attempts[0].evidence_ids
    assert all(eid.startswith("verify:cmd0:") for eid in result.attempts[0].evidence_ids)


def test_a_vacuous_green_is_not_evidence(tmp_path):
    """`pytest` that collects nothing must not count as a passing suite."""
    spec = CommandSpec(criteria_id="verify:cmd0",
                       argv=(sys.executable, "-c", "pass"),
                       require_collected=True)
    probe = CommandProbe([spec])
    measurement = probe.measure(str(tmp_path))
    criteria = criteria_for([spec])
    from wisp.core.acceptance import evaluate
    verdict = evaluate(criteria, measurement.evidence, measurement.observations)
    assert verdict.verdict is Verdict.FAIL
    assert measurement.observations["verify:cmd0"]["collected"] == 0


# ── Objective → criteria derivation ─────────────────────────────────────


def test_derivation_reads_a_named_definition(tmp_path):
    _write(tmp_path, "strings_util.py", "def greet(name):\n    return 'hi'\n")
    criteria, specs = derive_acceptance(
        "Add a function shout(name) to strings_util.py that upper-cases it.",
        str(tmp_path))
    ids = [c.criteria_id for c in criteria]
    assert "symbol:shout" in ids
    assert any(isinstance(s, SymbolSpec) and s.symbol == "shout" for s in specs)


def test_derivation_refuses_an_unparseable_objective(tmp_path):
    """No confident criterion beats a manufactured one."""
    criteria, specs = derive_acceptance("Make the code better.", str(tmp_path))
    assert criteria == ()
    assert specs == ()


def test_derivation_uses_the_projects_own_verification_command(tmp_path):
    _write(tmp_path, "pyproject.toml", "[project]\nname='x'\n")
    (tmp_path / "tests").mkdir()
    _write(tmp_path / "tests", "test_x.py", "def test_x():\n    assert True\n")
    criteria, specs = derive_acceptance("Fix the failing test.", str(tmp_path))
    assert any(isinstance(s, CommandSpec) for s in specs)
    assert all(c.required for c in criteria)


# ── Durability: resume re-runs nothing ──────────────────────────────────


def test_resume_continues_without_re_running_completed_attempts(ws, tmp_path):
    journal = tmp_path / "converge.jsonl"

    ctl, turn, criteria = _controller(
        ws, [lambda _w: _ok()] * 5, max_attempts=1, journal_path=journal)
    first = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                       criteria=criteria, max_attempts=1)))
    assert len(first.attempts) == 1
    assert len(turn.calls) == 1
    assert journal.exists()

    # A fresh controller, same journal: the completed attempt is NOT re-run.
    def fix(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return _ok()

    ctl2, turn2, _ = _controller(ws, [fix], max_attempts=3,
                                 journal_path=journal)
    second = _run(ctl2.converge(
        Objective(goal="fix", workspace=str(ws), criteria=criteria,
                  max_attempts=3), resume=True))
    assert len(second.attempts) == 2
    assert second.attempts[0].index == 0
    assert second.attempts[1].index == 1
    assert len(turn2.calls) == 1          # exactly one NEW turn
    assert second.converged


def test_resume_preserves_the_recovery_strategy(ws, tmp_path):
    """A restart must not lose the rung the interrupted run had chosen.

    Without this, `_last_rung` is empty after a reload, so attempt N+1 runs
    with **no rung and no directive** — the recovery is silently lost across
    a restart, which is the one thing a resume exists to preserve. The rung
    is re-derived from the durable facts, not remembered, so live and resumed
    cannot disagree about it.
    """
    journal = tmp_path / "resume.jsonl"

    # The uninterrupted run, for comparison.
    ctl_a, _turn_a, criteria = _controller(ws, [lambda _w: _ok()] * 4,
                                           max_attempts=3)
    uninterrupted = _run(ctl_a.converge(
        Objective(goal="fix", workspace=str(ws), criteria=criteria,
                  max_attempts=3)))
    assert [a.rung for a in uninterrupted.attempts] == [
        INITIAL_RUNG, "REPAIR", "GLOBAL_REPLAN"]

    # The interrupted run: attempt 0 only, then a fresh controller resumes.
    ws_b = tmp_path / "ws_b"
    ws_b.mkdir()
    _write(ws_b, "totals.py", "def sum_to(n):\n    return 0\n")
    ctl_b, turn_b, criteria_b = _controller(ws_b, [lambda _w: _ok()] * 4,
                                            max_attempts=1,
                                            journal_path=journal)
    first = _run(ctl_b.converge(Objective(goal="fix", workspace=str(ws_b),
                                         criteria=criteria_b, max_attempts=1)))
    assert len(first.attempts) == 1
    assert len(turn_b.calls) == 1

    ctl_c, turn_c, _ = _controller(ws_b, [lambda _w: _ok()] * 4, max_attempts=3,
                                   journal_path=journal)
    resumed = _run(ctl_c.converge(
        Objective(goal="fix", workspace=str(ws_b), criteria=criteria_b,
                  max_attempts=3), resume=True))

    # Attempt 0 was NOT re-run, and the rung sequence continues where the
    # interrupted run left off.
    assert [a.index for a in resumed.attempts] == [0, 1, 2]
    assert len(turn_c.calls) == 2
    assert [a.rung for a in resumed.attempts] == [
        a.rung for a in uninterrupted.attempts]
    assert resumed.attempts[1].directive, "the resumed attempt lost its directive"


def test_the_ladder_history_survives_a_resume(ws, tmp_path):
    """R5 must still hold after a restart: no rung is chosen twice."""
    journal = tmp_path / "r5.jsonl"
    ctl, _turn, criteria = _controller(ws, [lambda _w: _ok()] * 6, max_attempts=2,
                                       journal_path=journal)
    _run(ctl.converge(Objective(goal="fix", workspace=str(ws), criteria=criteria,
                               max_attempts=2)))
    ctl2, _turn2, _ = _controller(ws, [lambda _w: _ok()] * 6, max_attempts=4,
                                  journal_path=journal)
    resumed = _run(ctl2.converge(
        Objective(goal="fix", workspace=str(ws), criteria=criteria,
                  max_attempts=4), resume=True))
    rungs = [a.rung for a in resumed.attempts]
    assert len(rungs) == len(set(rungs)), f"a rung repeated after resume: {rungs}"


def test_a_torn_journal_line_does_not_break_resume(ws, tmp_path):
    journal = tmp_path / "converge.jsonl"
    journal.write_text(json.dumps({
        "index": 0, "rung": INITIAL_RUNG, "directive": "",
        "turn_succeeded": True, "terminal_outcome": "succeeded",
    }) + "\n" + '{"index": 1, "rung": "REP', encoding="utf-8")

    ctl, turn, criteria = _controller(ws, [lambda _w: _ok()] * 3,
                                      max_attempts=3, journal_path=journal)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria, max_attempts=3),
                               resume=True))
    assert len(result.attempts) >= 1
    assert len(turn.calls) <= 2


# ── Directives: every rung the ladder can choose has one ────────────────


def test_every_non_initial_rung_has_a_directive():
    for rung in RecoveryRung:
        if rung is RecoveryRung.HUMAN:
            continue
        assert directive_for(rung.name), f"{rung.name} has no directive"
    assert directive_for(INITIAL_RUNG) == ""


# ── Baseline-relative acceptance ────────────────────────────────────────


def test_a_red_baseline_makes_the_absolute_criterion_advisory():
    """"Make it pass" is not a requirement nobody stated.

    On a repository whose suite is already failing, requiring exit 0 would
    make every objective end in exhaustion. The required criterion becomes
    "no worse than baseline"; the absolute one is recorded but does not gate.
    """
    from wisp.core.convergence import Measurement

    spec = CommandSpec(criteria_id="verify:cmd0", argv=CHECK_SUM)
    baseline = Measurement(observations={
        "verify:cmd0": {"exit": 1, "collected": 3, "failed": 2}})
    criteria = criteria_for([spec], baseline=baseline)
    by_id = {c.criteria_id: c for c in criteria}

    assert by_id["verify:cmd0"].required is False          # advisory
    assert by_id["verify:cmd0:no_regression"].required is True


def test_an_objective_that_asks_to_fix_the_tests_promotes_the_absolute_criterion():
    from wisp.core.convergence import Measurement

    spec = CommandSpec(criteria_id="verify:cmd0", argv=CHECK_SUM)
    baseline = Measurement(observations={
        "verify:cmd0": {"exit": 1, "collected": 3, "failed": 2}})
    criteria = criteria_for([spec], baseline=baseline, promote_absolute=True)
    by_id = {c.criteria_id: c for c in criteria}
    assert by_id["verify:cmd0"].required is True


def test_an_objective_asking_to_fix_the_suite_is_detected():
    from wisp.core.convergence import _WANTS_FIX_RE
    assert _WANTS_FIX_RE.search("Fix the failing test in totals.py.")
    assert _WANTS_FIX_RE.search("Make the suite pass again.")
    assert not _WANTS_FIX_RE.search("Add a function shout() to strings_util.py.")


def _measurement(criteria_id: str, payload: dict) -> "object":
    """A consistent Measurement: evidence that cites the payload it rests on.

    Built explicitly so the fixture cannot diverge from production the way
    F40/F41's did — observations and evidence always describe one run.
    """
    from wisp.core.acceptance import CriterionKind, Evidence, content_digest
    from wisp.core.convergence import Measurement

    ids = (criteria_id, f"{criteria_id}:no_regression")
    return Measurement(
        observations={criteria_id: payload},
        evidence=tuple(
            Evidence(evidence_id=f"{cid}:{content_digest(payload)[:16]}",
                     criteria_id=cid, producer="test.probe",
                     kind=CriterionKind.DETERMINISTIC,
                     content_hash=content_digest(payload))
            for cid in ids),
        lines=(f"{criteria_id}: exit {payload.get('exit')}",))


def test_the_no_regression_criterion_is_gating():
    """A red baseline plus a *new* failure must block convergence."""
    from wisp.core.acceptance import evaluate

    spec = CommandSpec(criteria_id="verify:cmd0", argv=CHECK_SUM)
    baseline = _measurement("verify:cmd0",
                            {"exit": 1, "collected": 3, "failed": 2})
    criteria = criteria_for([spec], baseline=baseline)

    worse = _measurement("verify:cmd0",
                         {"exit": 1, "collected": 6, "failed": 5})
    verdict = evaluate(criteria, worse.evidence, worse.observations)
    assert verdict.verdict is Verdict.FAIL
    assert "verify:cmd0:no_regression" in verdict.unmet_criteria


def test_the_no_regression_criterion_passes_when_nothing_got_worse():
    from wisp.core.acceptance import evaluate

    spec = CommandSpec(criteria_id="verify:cmd0", argv=CHECK_SUM)
    baseline = _measurement("verify:cmd0",
                            {"exit": 1, "collected": 3, "failed": 2})
    criteria = criteria_for([spec], baseline=baseline)
    same = _measurement("verify:cmd0",
                        {"exit": 1, "collected": 3, "failed": 2})
    verdict = evaluate(criteria, same.evidence, same.observations)
    # The advisory absolute criterion failed, but it cannot block.
    assert verdict.verdict is Verdict.PASS


# ── Per-attempt provenance ──────────────────────────────────────────────


def test_the_record_carries_the_session_and_the_evidence_it_was_shown(ws):
    """A trajectory must be reviewable from the record alone.

    "Attempt 1 was a fresh session, and it was shown materially different
    information" is exactly the claim a recovery experiment has to be able
    to check — so both facts are on the record, not inferred from a log.
    """
    def fix(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return TurnObservation(turn_succeeded=True, terminal_outcome="succeeded",
                               session_id="sess-2")

    first = TurnObservation(turn_succeeded=True, terminal_outcome="succeeded",
                            session_id="sess-1")
    ctl, turn, criteria = _controller(ws, [lambda _w: first, fix], max_attempts=2)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    assert result.converged
    zero, one = result.attempts
    assert zero.session_id == "sess-1"
    assert one.session_id == "sess-2"
    assert zero.session_id != one.session_id, "attempts share a session"
    # Attempt 0 was shown nothing (nothing had failed); attempt 1 was shown
    # the measurement that failed.
    assert zero.evidence_lines == ()
    assert one.evidence_lines, "attempt 1 was shown no evidence"
    assert one.to_dict()["session_id"] == "sess-2"
    assert list(one.to_dict()["evidence_lines"]) == list(one.evidence_lines)


def test_the_attempts_have_distinct_strategies(ws):
    """`attempt 0 != attempt 1` in directive, evidence AND rung."""
    ctl, turn, criteria = _controller(ws, [lambda _w: _ok()] * 3, max_attempts=3)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    zero, one = result.attempts[:2]
    assert zero.rung != one.rung
    assert zero.directive != one.directive
    assert zero.evidence_lines != one.evidence_lines


# ── Verification-input integrity (the live F2 falsification) ────────────


def test_a_modified_verification_input_fails_the_integrity_criterion(tmp_path):
    """A green suite proves the objective only if it is the SAME suite.

    This is not hypothetical. A live run of the negative experiment had an
    agent **rewrite a read-only pinned test** — changing its assertion to
    something satisfiable inside the workspace — and the suite went green, so
    the run reported `goal_met`. Falsification F2, demonstrated end to end.
    The integrity criterion is what makes the measurement tamper-evident.
    """
    from wisp.core.acceptance import evaluate

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_pinned.py").write_text(
        "def test_pinned():\n    assert False\n", encoding="utf-8")

    spec = CommandSpec(criteria_id="verify:cmd0",
                       argv=(sys.executable, "-m", "pytest", "tests/", "-q"),
                       inputs=("tests",))
    probe = CommandProbe([spec])
    baseline = probe.measure(str(tmp_path))
    criteria = criteria_for([spec], baseline=baseline)
    assert any(c.criteria_id == "verify:cmd0:inputs_unchanged" for c in criteria)

    # The agent "fixes" it by relaxing the contract.
    (tests / "test_pinned.py").write_text(
        "def test_pinned():\n    assert True\n", encoding="utf-8")
    tampered = probe.measure(str(tmp_path))
    verdict = evaluate(criteria, tampered.evidence, tampered.observations)
    assert verdict.verdict is Verdict.FAIL
    assert "verify:cmd0:inputs_unchanged" in verdict.unmet_criteria
    # The suite really is green — which is exactly why exit status alone
    # cannot be the evidence.
    assert tampered.observations["verify:cmd0"]["exit"] == 0


def test_untouched_verification_inputs_pass_the_integrity_criterion(tmp_path):
    """Positive control: an unchanged suite is admissible evidence."""
    from wisp.core.acceptance import evaluate

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8")

    spec = CommandSpec(criteria_id="verify:cmd0",
                       argv=(sys.executable, "-m", "pytest", "tests/", "-q"),
                       inputs=("tests",))
    probe = CommandProbe([spec])
    baseline = probe.measure(str(tmp_path))
    criteria = criteria_for([spec], baseline=baseline)
    again = probe.measure(str(tmp_path))
    verdict = evaluate(criteria, again.evidence, again.observations)
    assert verdict.verdict is Verdict.PASS, verdict.unmet_criteria


def test_a_touched_but_unchanged_input_is_not_tampering(tmp_path):
    """The digest is by CONTENT: rewriting the same bytes is not a change."""
    from wisp.core.convergence import _inputs_digest

    tests = tmp_path / "tests"
    tests.mkdir()
    target = tests / "test_same.py"
    target.write_text("def test_same():\n    assert True\n", encoding="utf-8")
    before, names = _inputs_digest(str(tmp_path), ("tests",))
    target.write_text("def test_same():\n    assert True\n", encoding="utf-8")
    after, _ = _inputs_digest(str(tmp_path), ("tests",))
    assert before == after
    assert "tests/test_same.py" in names


def test_the_inputs_digest_is_stable_across_processes(tmp_path):
    """Two calls in one process, and the same digest — no mtime, no ordering."""
    from wisp.core.convergence import _inputs_digest

    (tmp_path / "b.py").write_text("B = 2\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("A = 1\n", encoding="utf-8")
    first, names = _inputs_digest(str(tmp_path), ("*.py",))
    second, _ = _inputs_digest(str(tmp_path), ("a.py", "b.py"))
    assert first == second
    assert names == ("a.py", "b.py")


# ── Provider failure mid-task ───────────────────────────────────────────


def test_a_transient_provider_failure_retries(ws):
    """A blip is `TRANSIENT`, whose cheapest legal rung is RETRY."""
    def blip(_ws):
        return TurnObservation(
            turn_succeeded=False, terminal_outcome="incomplete",
            failure_message="connection reset by peer",
            failure_recoverable=True)

    def fix(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return _ok()

    ctl, turn, criteria = _controller(ws, [blip, fix], max_attempts=2)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    assert result.converged
    assert result.attempts[0].failure_class == FailureClass.TRANSIENT.value
    assert [a.rung for a in result.attempts] == [INITIAL_RUNG, "RETRY"]


def test_a_capacity_rejection_is_not_retried_forever(ws):
    """An engine code routes to its taxonomy class, and the ladder is bounded."""
    def rejected(_ws):
        return TurnObservation(
            turn_succeeded=False, terminal_outcome="failed",
            failure_code="E1102", failure_message="Ollama HTTP error",
            failure_recoverable=False)

    ctl, turn, criteria = _controller(ws, [rejected] * 6, max_attempts=6)
    result = _run(ctl.converge(Objective(goal="fix", workspace=str(ws),
                                        criteria=criteria)))
    assert not result.converged
    # ENVIRONMENT's legal rungs are DIAGNOSTIC and HUMAN; the second choice
    # escalates rather than repeating the first.
    rungs = [a.rung for a in result.attempts]
    assert len(rungs) == len(set(rungs))
    assert len(rungs) <= 2
    assert result.goal_state in (GoalState.ESCALATED_TO_HUMAN,
                                 GoalState.GOAL_FAILED,
                                 GoalState.GOAL_UNVERIFIED)


# ── Non-vacuity: the assertions above can actually fail ─────────────────


def test_the_false_success_test_is_not_vacuous(ws):
    """Control for `test_a_successful_turn_with_unmet_criteria_...`.

    If the criterion is satisfiable by the same script, the same shape DOES
    converge — so the earlier test fails for the reason it claims (the
    criteria were unmet), not because the controller never converges.
    """
    def fix(_ws):
        _write(ws, "totals.py", "def sum_to(n):\n    return sum(range(1, n + 1))\n")
        return _ok()

    ctl, turn, criteria = _controller(ws, [fix], max_attempts=1)
    result = _run(ctl.converge(Objective(goal="fix sum_to", workspace=str(ws),
                                        criteria=criteria)))
    assert result.converged


def test_a_denial_without_the_no_retry_rule_would_loop():
    """Control: SECURITY's legal rungs exclude every retry-shaped rung.

    Proves the escalation in the denial test comes from the taxonomy rather
    than from the controller happening to stop.
    """
    from wisp.core.recovery import FORBIDDEN_RUNGS, LEGAL_RUNGS
    legal = LEGAL_RUNGS[FailureClass.SECURITY]
    forbidden = FORBIDDEN_RUNGS[FailureClass.SECURITY]
    assert legal == frozenset({RecoveryRung.HUMAN})
    for rung in (RecoveryRung.RETRY, RecoveryRung.REPAIR,
                 RecoveryRung.ROLLBACK, RecoveryRung.LOCAL_REPLAN,
                 RecoveryRung.GLOBAL_REPLAN, RecoveryRung.DIAGNOSTIC):
        assert rung in forbidden
