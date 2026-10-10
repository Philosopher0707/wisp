"""R5, "same observation, nothing changed" (docs/harness/r5-no-progress-design.md): a SUCCESSFUL call that returns the same normalised output as before, with no file
mutation in between, is a loop R2 cannot see (R2 counts failures). Phase P1: decided and journaled, never applied."""

from __future__ import annotations

import dataclasses

import pytest

from tests.reasoning.test_runtime import ok
from wisp.core.recovery import FailureClass, RecoveryRung
from wisp.core.reasoning import runtime as rt
from wisp.core.reasoning.decision import (
    DEFAULT_ENFORCED_RULES,
    RULES,
    Action,
    Budgets,
    Mode,
    Modes,
    NudgeKind,
    State,
    decide_stale,
    observation_digest,
    parse_modes,
)

PYTEST_OUT = "collected 12 items\n\ntests/test_x.py ...x...xxxx.   [100%]\n\n===== 7 passed, 5 xfailed in 0.04s =====\n"


class TestTheDigest:
    def test_runs_that_differ_only_in_timing_and_whitespace_are_the_same_observation(self):
        a = observation_digest("7 passed, 5 xfailed in 0.04s\n")
        assert a == observation_digest("7 passed, 5 xfailed in 12.91s\n\n") == observation_digest("  7 passed, 5 xfailed in 906ms  \r\n")
        assert observation_digest("built at 2026-10-07T16:29:56Z ok") == observation_digest("built at 2026-10-07T17:01:02Z ok")
        assert observation_digest("run 20261007_162956 done") == observation_digest("run 20261007_163011 done")

    @pytest.mark.parametrize("a,b", [
        ("7 passed, 5 xfailed in 0.04s", "8 passed, 4 xfailed in 0.04s"),   # counts are information
        ("error in a.py", "error in b.py"),                                 # so are paths
        ("ok", "OK"),
    ])
    def test_different_information_is_a_different_observation(self, a, b):
        assert observation_digest(a) != observation_digest(b)

    def test_no_output_is_no_observation(self):
        assert observation_digest("") == observation_digest("   \n\n") == "" and observation_digest(None) == ""  # type: ignore[arg-type]

    def test_it_never_raises_and_is_bounded(self):
        for bad in (5, b"x", "\x00" * 10, "a" * 2_000_000, object()):
            assert isinstance(observation_digest(bad), str)  # type: ignore[arg-type]

    def test_the_digest_is_a_short_stable_hex_string(self):
        d = observation_digest(PYTEST_OUT)
        assert d == observation_digest(PYTEST_OUT) and len(d) == 16 and int(d, 16) >= 0


class TestDecideStale:
    def run(self, tool, n, *, epoch=0, digest="d1", budgets=Budgets()):
        state, out = State(), []
        for _ in range(n):
            d, state = decide_stale(tool, digest, epoch, state, budgets)
            out.append(d)
        return out, state

    def test_a_shell_tool_nudges_on_the_second_identical_output_then_escalates_once(self):
        ds, _ = self.run("run_bash", 5)
        assert [d.action for d in ds] == [Action.CONTINUE, Action.NUDGE, Action.ESCALATE, Action.CONTINUE, Action.CONTINUE]
        assert ds[1].rule == "R5" and ds[1].nudge is NudgeKind.NO_NEW_INFORMATION and ds[1].failure_class is FailureClass.REPEATED
        assert ds[2].rung is RecoveryRung.LOCAL_REPLAN and all(d.rule == "R5" for d in ds[1:3])

    def test_run_tests_is_treated_like_a_shell_tool(self):
        ds, _ = self.run("run_tests", 2)
        assert ds[1].action is Action.NUDGE

    def test_other_tools_get_one_more_repeat(self):
        ds, _ = self.run("read_file", 4)
        assert [d.action for d in ds] == [Action.CONTINUE, Action.CONTINUE, Action.NUDGE, Action.ESCALATE]

    @pytest.mark.parametrize("tool", ["subagent_wait", "subagent_list", "subagent_result", "subagent_send", "subagent_cancel"])
    def test_polling_tools_never_fire(self, tool):
        ds, state = self.run(tool, 6)
        assert {d.action for d in ds} == {Action.CONTINUE} and state.stale == ()

    def test_a_mutation_starts_a_new_count(self):
        state = State()
        for epoch in (0, 0, 1, 1):
            d, state = decide_stale("run_bash", "d1", epoch, state, Budgets())
        assert d.action is Action.NUDGE  # the 2nd occurrence in epoch 1, not the 4th overall

    def test_different_digests_are_counted_separately(self):
        state = State()
        for digest in ("a", "b", "a", "b"):
            d, state = decide_stale("run_bash", digest, 0, state, Budgets())
        assert d.action is Action.NUDGE

    def test_an_empty_digest_never_counts(self):
        ds, state = self.run("run_bash", 4, digest="")
        assert {d.action for d in ds} == {Action.CONTINUE} and state.stale == ()

    def test_the_state_is_frozen_and_the_rule_is_deterministic(self):
        d1, s1 = decide_stale("run_bash", "d", 0, State(), Budgets())
        d2, s2 = decide_stale("run_bash", "d", 0, State(), Budgets())
        assert (d1, s1) == (d2, s2)
        with pytest.raises(dataclasses.FrozenInstanceError):
            s1.stale = ()  # type: ignore[misc]

    def test_the_note_never_quotes_the_output_and_says_why(self):
        ds, _ = self.run("run_bash", 2, digest="secret-looking-digest")
        n = ds[1]
        assert n.note.startswith("Harness note:") and "secret-looking-digest" not in n.note and n.reason


class TestTheRuleIsAccepted:
    def test_r5_is_a_rule_and_is_not_enforced_by_default(self):
        assert "R5" in RULES
        m = parse_modes("observe", DEFAULT_ENFORCED_RULES)
        assert m.for_rule("R5") is Mode.OBSERVE
        assert parse_modes("observe", "R5=enforce").for_rule("R5") is Mode.ENFORCE
        assert parse_modes("off", "R5=enforce").for_rule("R5") is Mode.OFF


class TestInTheRuntime:
    def feed(self, t, cmd, out=PYTEST_OUT, tool="run_bash", cid=None):
        cid = cid or f"c{t._calls}"
        return t.observe_tool_result(ok(tool, cid=cid), out, {"command": cmd})

    def fired(self, t):
        return [(r["rule"], r["action"]) for r in t.journal if r.get("rule") == "R5"]

    def test_the_real_session_three_spellings_one_output(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE))
        for cmd in ("pytest tests/x.py -v", "pytest tests/x.py -v | cat", "pytest tests/x.py -v | tail -20"):
            self.feed(t, cmd)
        assert self.fired(t) == [("R5", "nudge"), ("R5", "escalate")]

    def test_an_edit_between_two_identical_runs_is_progress(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE))
        self.feed(t, "pytest -q")
        t.observe_tool_result(ok("write_file"), "wrote", {"path": "a.py", "content": "x"})
        self.feed(t, "pytest -q")
        assert self.fired(t) == []

    def test_a_shell_write_between_two_identical_runs_is_progress_too(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE))
        self.feed(t, "pytest -q")
        self.feed(t, "sed -i 's/a/b/' src/x.py", out="")
        self.feed(t, "pytest -q")
        assert self.fired(t) == []

    def test_a_changed_output_is_progress(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE))
        self.feed(t, "pytest -q", out="1 failed, 6 passed in 0.1s")
        self.feed(t, "pytest -q", out="7 passed in 0.1s")
        assert self.fired(t) == []

    def test_a_failing_call_is_r2s_business_not_r5s(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE))
        for _ in range(3):
            t.observe_tool_result(ok("run_bash"), "[exit code: 1]\nboom", {"command": "false"})  # the marker leads the text: that is how bash.py writes it
        assert self.fired(t) == [] and any(r.get("rule") == "R2" for r in t.journal)

    def test_off_records_nothing(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE, (("R5", Mode.OFF),)))
        for _ in range(3):
            self.feed(t, "pytest -q")
        assert self.fired(t) == []

    def test_in_enforce_the_note_is_handed_over_once_and_journaled_as_applied(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE, (("R5", Mode.ENFORCE),)))
        self.feed(t, "pytest -q")
        self.feed(t, "pytest -q")
        d = t.take_enforced("tool_result")
        assert d is not None and d.action is Action.NUDGE and d.note.startswith("Harness note:")
        assert t.take_enforced("tool_result") is None  # handed over once
        assert any(r.get("rule") == "R5" and r.get("applied") for r in t.journal)

    def test_in_observe_nothing_is_ever_handed_over(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE))
        for _ in range(4):
            self.feed(t, "pytest -q")
        assert self.fired(t) == [("R5", "nudge"), ("R5", "escalate")]
        assert t.take_enforced("tool_result") is None

    def test_one_note_per_round_and_the_stronger_one_wins(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE, (("R5", Mode.ENFORCE),)))
        for _ in range(3):  # nudge on the 2nd, escalate on the 3rd, both in the same round
            self.feed(t, "pytest -q")
        assert t.take_enforced("tool_result").action is Action.ESCALATE
        assert t.take_enforced("tool_result") is None

    def test_a_later_nudge_does_not_replace_an_earlier_escalation_in_the_same_round(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE, (("R5", Mode.ENFORCE),)))
        for _ in range(3):
            self.feed(t, "pytest a", out="alpha output")  # nudge, then escalate
        for _ in range(2):
            self.feed(t, "pytest b", out="beta output")  # a nudge for a different output, after the escalation
        assert t.take_enforced("tool_result").action is Action.ESCALATE

    def test_a_success_that_is_not_a_repeat_returns_nothing(self):
        t = rt.TurnReasoning(Modes(Mode.ENFORCE))
        assert self.feed(t, "pytest -q", out="1 passed") is None
        assert self.feed(t, "pytest -q", out="2 passed") is None
        assert t.take_enforced("tool_result") is None

    def test_the_return_value_is_the_decision_for_the_seam(self):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE))
        assert self.feed(t, "pytest -q") is None
        d = self.feed(t, "pytest -q")
        assert d is not None and d.rule == "R5" and d.action is Action.NUDGE

    def test_a_broken_digest_cannot_fail_the_turn(self, monkeypatch):
        t = rt.TurnReasoning(Modes(Mode.OBSERVE))
        monkeypatch.setattr("wisp.core.reasoning.runtime.observation_digest", lambda text: 1 / 0)
        assert self.feed(t, "pytest -q") is None
        assert any(r.get("action") == "core_error" for r in t.journal)
