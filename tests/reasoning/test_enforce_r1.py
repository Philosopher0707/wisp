"""P3 / R1 in `enforce`: a success claim the ledger cannot back is withheld ONCE (the model is told how to fix it), and if it still stands the answer is
flagged in its own text and the turn ends unverified. R1 sits after every other completion gate, so the verification floor keeps its behaviour and a
turn is never nudged twice. In observe/off nothing changes (RC4)."""

from __future__ import annotations

import pytest

from tests.reasoning import personas as P
from wisp.core.reasoning import runtime as rt
from wisp.core.reasoning.decision import Action, Mode, State

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


def persona(name):
    return next(p for p in P.PERSONAS if p.name == name)


def play(name, mode, tmp_path, **kw):
    return P.run(persona(name), mode, tmp_path, **kw)


def applied(o):
    return [r for r in o.journal if r.get("applied")]


class TestWithholdOnce:
    def test_the_model_is_asked_to_verify_and_then_does(self, tmp_path):
        o = play("CorrectsAfterANudge", "enforce", tmp_path)
        assert o.provider_calls == 3 and o.ending == "done" and "build succeeds" in o.final_text and not o.flagged()
        assert any("compileall" in c for c in o.commands_run)
        assert [r["action"] for r in applied(o)] == ["withhold_done"]

    def test_the_nudge_is_what_the_model_is_shown(self, tmp_path):
        o = play("CorrectsAfterANudge", "enforce", tmp_path)
        second_call = " ".join(str(m.get("content", "")) for m in o.provider.seen[1])
        assert "Run the relevant verification now" in second_call and "not backed" in second_call
        assert "The build succeeds." in second_call  # the model's own earlier answer is kept in the conversation

    def test_the_harness_note_never_quotes_the_model(self, tmp_path):
        o = play("CorrectsAfterANudge", "enforce", tmp_path)
        note = applied(o)[0]["note"]
        assert "The build succeeds" not in note and note.startswith("Harness note:")


class TestStillUnbackedIsFlaggedNotLooped:
    def test_the_answer_is_flagged_once_and_the_turn_ends(self, tmp_path):
        o = play("ClaimsWithoutRunning", "enforce", tmp_path)
        assert o.ending == "done" and o.all_text.count("Harness note") == 1 and o.provider_calls <= 8
        assert {r["action"] for r in applied(o)} <= {"withhold_done", "stop"} and applied(o)[-1]["goal_state"] in ("goal_unverified", None)

    def test_no_round_left_means_flag_without_withholding(self, tmp_path):
        o = play("CorrectsAfterANudge", "enforce", tmp_path, max_iterations=1)
        assert o.provider_calls == 1 and o.all_text.count("Harness note") == 1

    def test_the_total_number_of_rounds_is_bounded_by_the_budget(self, tmp_path):
        o = play("CorrectsAfterANudge", "enforce", tmp_path)
        assert o.provider_calls <= 3  # one withhold, then the verified answer


class TestObserveAndOffChangeNothing:
    @pytest.mark.parametrize("name", ["ClaimsWithoutRunning", "CorrectsAfterANudge"])
    def test_observe_shows_the_user_no_note_and_withholds_nothing(self, tmp_path, name):
        o = play(name, "observe", tmp_path)
        assert not o.r1_flagged() and not applied(o)

    def test_the_control_is_untouched_in_enforce(self, tmp_path):
        off, enf = play("HonestSolver", "off", tmp_path), play("HonestSolver", "enforce", tmp_path)
        assert not applied(enf) and not enf.r1_flagged() and off.user_view() == enf.user_view()


class TestOnlyVerificationClaimsIntervene:
    def test_a_file_claim_without_an_edit_does_not_withhold(self):
        t = rt.TurnReasoning(Mode.ENFORCE)
        t.observe_final("I updated `src/app.py`. I ran `make test`.")
        assert t.take_enforced("final") is None


class TestRuntimeDeferral:
    def test_deciding_is_deferred_until_the_last_gate(self):
        t = rt.TurnReasoning(Mode.ENFORCE)
        t.observe_final("All tests pass.")
        assert t.state == State() and not any(r.get("applied") for r in t.journal)

    def test_a_round_the_floor_handled_does_not_spend_the_budget(self):
        t = rt.TurnReasoning(Mode.ENFORCE)
        t.observe_final("All tests pass.")  # round 1: another gate nudges, take_enforced is never reached
        t.observe_final("All tests pass.")  # round 2
        assert t.take_enforced("final").action is Action.WITHHOLD_DONE

    def test_withhold_then_stop_then_nothing_pending(self):
        t = rt.TurnReasoning(Mode.ENFORCE)
        t.observe_final("All tests pass.")
        assert t.take_enforced("final").action is Action.WITHHOLD_DONE
        assert t.take_enforced("final") is None  # handed over once
        t.observe_final("All tests pass.")
        d = t.take_enforced("final")
        assert d.action is Action.STOP and d.goal_state.value == "goal_unverified"

    def test_a_backed_claim_returns_nothing(self):
        from tests.reasoning.conftest import ev

        t = rt.TurnReasoning(Mode.ENFORCE)
        t.ledger.observe(ev("run_bash", "tool", command="pytest -q", text="5 passed"))
        t.observe_final("All tests pass.")
        assert t.take_enforced("final") is None

    def test_observe_decides_immediately_as_before(self):
        t = rt.TurnReasoning(Mode.OBSERVE)
        d = t.observe_final("All tests pass.")
        assert d.action is Action.ANNOTATE_FINAL and t.take_enforced("final") is None

    def test_rc4_even_stored_audits_are_never_applied_outside_enforce(self):
        from tests.reasoning.conftest import ev  # noqa: F401

        enforcing = rt.TurnReasoning(Mode.ENFORCE)
        enforcing.observe_final("All tests pass.")
        observing = rt.TurnReasoning(Mode.OBSERVE)
        observing._final_audits = enforcing._final_audits  # a bug elsewhere must not be able to turn observe into enforcement
        assert observing._final_audits and observing.take_enforced("final") is None
