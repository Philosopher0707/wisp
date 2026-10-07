"""The decision rules R1-R4 as pure functions, and their invariants RC1, RC3, RC5, RC9, RC13."""

from __future__ import annotations

import random

import pytest

from tests.reasoning.conftest import FAIL, ev
from wisp.core.goal import GoalState
from wisp.core.reasoning.claims import Audit, Claim, ClaimKind, Verdict, audit
from wisp.core.reasoning.decision import (Action, Budgets, Decision, Mode, NudgeKind, State, affordability_limit, decide_denial, decide_failure, decide_final, failure_signature, parse_mode,
                                          plan_request)
from wisp.core.recovery import FailureClass, RecoveryRung


def a(kind, verdict, ids=("F1",)):
    return Audit(Claim(kind, "x", 0, 1), verdict, ids, "r")


class TestMode:
    @pytest.mark.parametrize("v,m", [("off", Mode.OFF), ("OBSERVE", Mode.OBSERVE), (" enforce ", Mode.ENFORCE)])
    def test_known_values(self, v, m):
        assert parse_mode(v) is m

    @pytest.mark.parametrize("v", ["", None, "enforc", "on", "true", "1", 7, "obsrve"])
    def test_a_typo_is_observe_never_enforce(self, v):
        assert parse_mode(v) is Mode.OBSERVE


class TestDecisionIsClosed:
    def test_stop_needs_a_goal_state(self):
        with pytest.raises(ValueError):
            Decision("R1", Action.STOP, "why")
        with pytest.raises(ValueError):
            Decision("R1", Action.STOP, "why", goal_state="goal_met")  # type: ignore[arg-type]

    def test_escalate_needs_a_rung(self):
        with pytest.raises(ValueError):
            Decision("R2", Action.ESCALATE, "why")

    def test_nudge_needs_a_kind_and_retry_a_budget(self):
        with pytest.raises(ValueError):
            Decision("R2", Action.NUDGE, "why")
        with pytest.raises(ValueError):
            Decision("R4", Action.RETRY_REQUEST, "why", max_tokens=0)

    def test_an_intervention_must_say_why(self):
        with pytest.raises(ValueError):
            Decision("R1", Action.ANNOTATE_FINAL)

    def test_failure_class_is_the_existing_taxonomy(self):
        with pytest.raises(ValueError):
            Decision("R2", Action.CONTINUE, failure_class="weird")  # type: ignore[arg-type]


class TestR1:
    def test_supported_claims_continue(self):
        d, s = decide_final((a(ClaimKind.TESTS_PASS, Verdict.SUPPORTED),), Mode.ENFORCE, State())
        assert d.action is Action.CONTINUE and s == State()

    def test_off_never_intervenes(self):
        assert decide_final((a(ClaimKind.TESTS_PASS, Verdict.UNSUPPORTED),), Mode.OFF, State())[0].action is Action.CONTINUE

    def test_observe_annotates_once(self):
        d, s = decide_final((a(ClaimKind.TESTS_PASS, Verdict.UNSUPPORTED),), Mode.OBSERVE, State())
        assert d.action is Action.ANNOTATE_FINAL and d.evidence_ids == ("F1",) and "not backed" in d.note
        assert decide_final((a(ClaimKind.TESTS_PASS, Verdict.UNSUPPORTED),), Mode.OBSERVE, s)[0].action is Action.CONTINUE

    def test_contradiction_is_named(self):
        d, _ = decide_final((a(ClaimKind.TESTS_PASS, Verdict.CONTRADICTED),), Mode.OBSERVE, State())
        assert "contradicted" in d.note

    def test_enforce_withholds_once_then_stops_unverified(self):
        d1, s1 = decide_final((a(ClaimKind.TESTS_PASS, Verdict.UNSUPPORTED),), Mode.ENFORCE, State())
        assert d1.action is Action.WITHHOLD_DONE
        d2, _ = decide_final((a(ClaimKind.TESTS_PASS, Verdict.UNSUPPORTED),), Mode.ENFORCE, s1)
        assert d2.action is Action.STOP and d2.goal_state is GoalState.GOAL_UNVERIFIED

    def test_file_and_command_claims_never_intervene(self):
        d, _ = decide_final((a(ClaimKind.FILE_CHANGED, Verdict.UNSUPPORTED), a(ClaimKind.COMMAND_RAN, Verdict.CONTRADICTED)), Mode.ENFORCE, State())
        assert d.action is Action.CONTINUE

    def test_no_claims_is_continue(self):
        assert decide_final((), Mode.ENFORCE, State())[0].action is Action.CONTINUE

    def test_the_input_is_audit_results_not_text(self):
        with pytest.raises(Exception):
            decide_final("All tests pass.", Mode.OBSERVE, State())  # type: ignore[arg-type]


class TestR2:
    def test_signature_ignores_numbers_paths_and_case(self):
        x = failure_signature("run_bash", "FileNotFoundError: /Users/a/b/c.py line 41 at 0xdeadbeef")
        y = failure_signature("run_bash", "filenotfounderror: /tmp/q/w/e.py   line 52 at 0xcafef00d")
        assert x == y

    def test_different_errors_differ(self):
        assert failure_signature("run_bash", "ImportError: x") != failure_signature("run_bash", "KeyError: x")
        assert failure_signature("run_bash", "boom") != failure_signature("edit_file", "boom")

    def test_first_continue_second_nudge_third_escalate(self):
        s = State()
        d1, s = decide_failure("sig", "F1", s)
        d2, s = decide_failure("sig", "F2", s)
        d3, s = decide_failure("sig", "F3", s)
        assert [d1.action, d2.action, d3.action] == [Action.CONTINUE, Action.NUDGE, Action.ESCALATE]
        assert d2.nudge is NudgeKind.CHANGE_HYPOTHESIS and d2.failure_class is FailureClass.REPEATED
        assert isinstance(d3.rung, RecoveryRung) and d3.failure_class is FailureClass.REPEATED

    def test_a_different_signature_starts_from_zero(self):
        s = State()
        for i in range(2):
            _, s = decide_failure("sig", f"F{i}", s)
        assert decide_failure("other", "F9", s)[0].action is Action.CONTINUE

    def test_distinct_signatures_do_not_accumulate(self):
        s = State()
        for i in range(10):
            d, s = decide_failure(f"sig{i}", f"F{i}", s)
            assert d.action is Action.CONTINUE


class TestR3:
    def test_second_refusal_of_one_rule_nudges_once(self):
        s = State()
        d1, s = decide_denial("OUTSIDE_WORKSPACE", "F1", s)
        d2, s = decide_denial("OUTSIDE_WORKSPACE", "F2", s)
        d3, s = decide_denial("OUTSIDE_WORKSPACE", "F3", s)
        assert [d1.action, d2.action, d3.action] == [Action.CONTINUE, Action.NUDGE, Action.CONTINUE]
        assert d2.nudge is NudgeKind.REFUSED_BY_POLICY and d2.failure_class is FailureClass.SECURITY and "OUTSIDE_WORKSPACE" in d2.note

    def test_different_rules_are_counted_apart(self):
        s = State()
        _, s = decide_denial("A", "F1", s)
        assert decide_denial("B", "F2", s)[0].action is Action.CONTINUE


class TestR4:
    MSG = "Error 402: This request requires more credits, or fewer max_tokens. You requested up to 16384 tokens, but can only afford 5403."

    def test_parses_the_limit(self):
        assert affordability_limit(self.MSG) == 5403
        assert affordability_limit("can only afford 12") == 12
        assert affordability_limit("rate limited") is None and affordability_limit("") is None and affordability_limit(None) is None  # type: ignore[arg-type]

    def test_one_smaller_retry_below_the_ceiling(self):
        d, s = plan_request(self.MSG, 16384, "F1", State())
        assert d.action is Action.RETRY_REQUEST and d.max_tokens == 5403 - 64 and s.ceiling == 5403 and s.retries == 1

    def test_a_second_failure_stops_honestly(self):
        _, s = plan_request(self.MSG, 16384, "F1", State())
        d, _ = plan_request("can only afford 5000", 5339, "F2", s)
        assert d.action is Action.STOP and d.goal_state is GoalState.ESCALATED_TO_HUMAN and d.failure_class is FailureClass.ENVIRONMENT

    def test_below_min_useful_stops_without_retry(self):
        d, s = plan_request("can only afford 100", 4096, "F1", State())
        assert d.action is Action.STOP and s.retries == 0

    def test_never_asks_for_more_than_was_requested(self):
        d, _ = plan_request("can only afford 9000", 4096, "F1", State())
        assert d.action is Action.STOP

    def test_the_ceiling_only_goes_down(self):
        _, s = plan_request("can only afford 3000", 4096, "F1", State())
        _, s = plan_request("can only afford 8000", 4096, "F2", s)
        assert s.ceiling == 3000

    def test_a_message_without_a_limit_is_ignored(self):
        d, s = plan_request("server error", 4096, "F1", State())
        assert d.action is Action.CONTINUE and s == State()


class TestRC5Termination:
    def test_interventions_per_turn_are_bounded_over_random_event_streams(self):
        budgets = Budgets()
        for seed in range(300):
            rng = random.Random(seed)
            s, interventions = State(), 0
            for step in range(200):
                kind = rng.choice(["final", "fail", "deny", "afford"])
                if kind == "final":
                    d, s = decide_final((a(ClaimKind.TESTS_PASS, rng.choice(list(Verdict))),), rng.choice(list(Mode)), s, budgets)
                elif kind == "fail":
                    d, s = decide_failure(f"s{rng.randrange(3)}", f"F{step}", s, budgets)
                elif kind == "deny":
                    d, s = decide_denial(f"r{rng.randrange(3)}", f"F{step}", s, budgets)
                else:
                    d, s = plan_request(f"can only afford {rng.randrange(50, 9000)}", rng.choice([512, 4096, 16384]), f"F{step}", s, budgets)
                interventions += d.action in (Action.NUDGE, Action.ANNOTATE_FINAL, Action.WITHHOLD_DONE, Action.RETRY_REQUEST)
            # one nudge per signature (3) and per refused rule (3); one annotate, one withhold, one retry.
            assert interventions <= 3 + 3 + budgets.annotate_final + budgets.withhold_done + budgets.retry_request, (seed, interventions)

    def test_decisions_are_deterministic(self):
        runs = []
        for _ in range(2):
            out, st = [], State()
            for sig in ["a", "a", "b", "a", "a"]:
                d, st = decide_failure(sig, "F", st)
                out.append(d)
            runs.append(out)
        assert runs[0] == runs[1]


class TestEndToEndWithTheLedger:
    def test_a_claim_after_a_failing_run_is_contradicted_and_annotated(self, ledger):
        ledger.observe(ev("run_bash", "tool", command="pytest -q", text=FAIL, failed=True))
        d, _ = decide_final(audit("All tests pass.", ledger), Mode.OBSERVE, State())
        assert d.action is Action.ANNOTATE_FINAL and "contradicted" in d.note

    def test_a_backed_claim_is_left_alone(self, ledger):
        ledger.observe(ev("run_bash", "tool", command="pytest -q", text="5 passed"))
        assert decide_final(audit("All tests pass.", ledger), Mode.ENFORCE, State())[0].action is Action.CONTINUE
