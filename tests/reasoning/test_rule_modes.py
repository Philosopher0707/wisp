"""Per-rule modes: `reasoning_core` is the default and `reasoning_core_rules` (`R1=enforce,R4=observe`) overrides it rule by rule, so each heuristic is
flipped on its own once its baseline looks right. RC13 holds per rule: a typo is `observe`, never more intrusive than what the operator wrote."""

from __future__ import annotations

import pytest

from tests.reasoning import personas as P
from tests.reasoning.test_enforce_r4 import E5403
from wisp.config import WispConfig
from wisp.core.reasoning import runtime as rt
from wisp.core.reasoning.decision import Action, Mode, Modes, parse_modes, render_modes

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


class TestParsing:
    @pytest.mark.parametrize("rules,expected", [
        ("R1=enforce", {"R1": Mode.ENFORCE}), ("r4 = Enforce , R1=observe", {"R4": Mode.ENFORCE, "R1": Mode.OBSERVE}), ("R2:enforce", {"R2": Mode.ENFORCE}),
        ("R3=off", {"R3": Mode.OFF}), ("", {}), (None, {}), ("garbage", {}), ("R9=enforce", {}), ("R1", {"R1": Mode.OBSERVE}),
    ])
    def test_known_forms(self, rules, expected):
        assert dict(parse_modes("observe", rules).overrides) == expected

    @pytest.mark.parametrize("typo", ["enfroce", "on", "true", "1", "", "ENFORCED", "enforce!"])
    def test_rc13_a_mode_typo_for_a_rule_is_observe_never_enforce(self, typo):
        assert parse_modes("enforce", f"R1={typo}").for_rule("R1") is Mode.OBSERVE

    def test_an_unknown_rule_does_not_change_the_others(self):
        m = parse_modes("observe", "R9=enforce,R4=enforce")
        assert m.for_rule("R4") is Mode.ENFORCE and m.for_rule("R1") is Mode.OBSERVE

    def test_the_default_applies_without_an_override(self):
        m = parse_modes("enforce", "R2=observe")
        assert [m.for_rule(r) for r in ("R1", "R2", "R3", "R4")] == [Mode.ENFORCE, Mode.OBSERVE, Mode.ENFORCE, Mode.ENFORCE]

    def test_a_default_of_off_switches_everything_off(self):
        m = parse_modes("off", "R1=enforce,R4=enforce")
        assert {m.for_rule(r) for r in ("R1", "R2", "R3", "R4")} == {Mode.OFF}

    def test_a_typo_in_the_default_is_observe(self):
        assert parse_modes("enfroce", "").default is Mode.OBSERVE

    def test_render_round_trips_and_is_sorted(self):
        m = parse_modes("observe", "R4=enforce,R1=observe")
        assert render_modes(m) == "R1=observe,R4=enforce" and parse_modes("observe", render_modes(m)) == m


class TestConfig:
    def test_the_setting_is_normalised(self, monkeypatch):
        monkeypatch.setenv("WISP_REASONING_CORE_RULES", "r4=Enforce, junk, R9=enforce, R1=enfroce")
        monkeypatch.delenv("WISP_REASONING_CORE", raising=False)
        c = WispConfig()
        assert c.reasoning_core == "observe" and c.reasoning_core_rules == "R1=observe,R4=enforce"

    @pytest.fixture
    def unset(self, monkeypatch):
        monkeypatch.delenv("WISP_REASONING_CORE", raising=False)
        monkeypatch.delenv("WISP_REASONING_CORE_RULES", raising=False)
        monkeypatch.setattr("wisp.config.load_config", lambda: {})

    def test_unset_enforces_r1_and_r4_and_observes_the_rest(self, unset):
        c = WispConfig()
        m = parse_modes(c.reasoning_core, c.reasoning_core_rules)
        assert (m.for_rule("R1"), m.for_rule("R4")) == (Mode.ENFORCE, Mode.ENFORCE)
        assert (m.for_rule("R2"), m.for_rule("R3")) == (Mode.OBSERVE, Mode.OBSERVE) and c.reasoning_core == "observe"

    def test_the_default_list_is_the_single_constant(self, unset):
        from wisp.core.reasoning.decision import DEFAULT_ENFORCED_RULES

        assert WispConfig().reasoning_core_rules == render_modes(parse_modes("observe", DEFAULT_ENFORCED_RULES))

    @pytest.mark.parametrize("core,expected", [("observe", Mode.OBSERVE), ("off", Mode.OFF), ("enforce", Mode.ENFORCE)])
    def test_an_explicit_global_mode_is_the_kill_switch(self, unset, monkeypatch, core, expected):
        monkeypatch.setenv("WISP_REASONING_CORE", core)
        c = WispConfig()
        m = parse_modes(c.reasoning_core, c.reasoning_core_rules)
        assert {m.for_rule(r) for r in ("R1", "R2", "R3", "R4")} == {expected}

    def test_an_explicit_empty_rules_setting_means_no_overrides(self, unset, monkeypatch):
        monkeypatch.setenv("WISP_REASONING_CORE_RULES", "")
        c = WispConfig()
        assert c.reasoning_core_rules == "" and parse_modes(c.reasoning_core, c.reasoning_core_rules).for_rule("R4") is Mode.OBSERVE

    def test_explicit_rules_replace_the_default_list(self, unset, monkeypatch):
        monkeypatch.setenv("WISP_REASONING_CORE_RULES", "R4=observe")
        c = WispConfig()
        m = parse_modes(c.reasoning_core, c.reasoning_core_rules)
        assert (m.for_rule("R4"), m.for_rule("R1")) == (Mode.OBSERVE, Mode.OBSERVE)

    def test_the_config_file_is_an_explicit_setting_too(self, monkeypatch):
        monkeypatch.delenv("WISP_REASONING_CORE", raising=False)
        monkeypatch.delenv("WISP_REASONING_CORE_RULES", raising=False)
        monkeypatch.setattr("wisp.config.load_config", lambda: {"reasoning_core": "off"})
        c = WispConfig()
        assert parse_modes(c.reasoning_core, c.reasoning_core_rules).for_rule("R4") is Mode.OFF


class TestRuntimePerRule:
    def test_r4_enforced_r1_observed(self):
        t = rt.TurnReasoning(parse_modes("observe", "R4=enforce"))
        t.observe_provider_error(E5403, 16384)
        assert t.take_enforced("provider_error").action is Action.RETRY_REQUEST
        assert t.observe_final("All tests pass.").action is Action.ANNOTATE_FINAL and t.take_enforced("final") is None

    def test_r1_enforced_r4_observed(self):
        t = rt.TurnReasoning(parse_modes("observe", "R1=enforce"))
        t.observe_provider_error(E5403, 16384)
        assert t.take_enforced("provider_error") is None
        t.observe_final("All tests pass.")
        assert t.take_enforced("final").action is Action.WITHHOLD_DONE

    def test_a_rule_set_to_off_records_nothing(self):
        t = rt.TurnReasoning(parse_modes("observe", "R1=off,R4=off,R2=off,R3=off"))
        assert t.observe_final("All tests pass.") is None and t.observe_provider_error(E5403, 16384) is None and t.journal == ()

    def test_r3_off_ignores_refusals_but_r2_still_sees_failures(self):
        from tests.reasoning.test_runtime import denied

        t = rt.TurnReasoning(parse_modes("observe", "R3=off"))
        for i in range(3):
            assert t.observe_refusal(denied(), {"command": "x"}) is None
        assert t.journal == () and t.ledger.facts  # the fact is still recorded; only the rule is off
        out = "[exit code: 1]\nboom"
        bad = lambda i: {"type": "tool_result", "name": "run_bash", "tool_call_id": f"c{i}", "result": {"status": "ok", "data": out}}  # noqa: E731
        acts = [getattr(t.observe_tool_result(bad(i), out, {"command": "x"}), "action", None) for i in range(3)]
        assert acts == [Action.CONTINUE, Action.NUDGE, Action.ESCALATE]

    def test_r2_off_ignores_failures_but_r3_still_sees_refusals(self):
        from tests.reasoning.test_runtime import denied

        t = rt.TurnReasoning(parse_modes("observe", "R2=off"))
        out = "[exit code: 1]\nboom"
        bad = lambda i: {"type": "tool_result", "name": "run_bash", "tool_call_id": f"c{i}", "result": {"status": "ok", "data": out}}  # noqa: E731
        assert all(t.observe_tool_result(bad(i), out, {"command": "x"}) is None for i in range(3))
        assert t.observe_refusal(denied(), {"command": "x"}).action is Action.CONTINUE
        assert t.observe_refusal(denied(), {"command": "y"}).action is Action.NUDGE

    def test_the_journal_records_each_rules_own_mode(self):
        t = rt.TurnReasoning(parse_modes("observe", "R4=enforce"))
        t.observe_provider_error(E5403, 16384)
        t.observe_final("All tests pass.")
        modes = {r["rule"]: r["mode"] for r in t.journal if r.get("rule")}
        assert modes == {"R4": "enforce", "R1": "observe"}

    def test_a_plain_mode_still_works_as_before(self):
        assert rt.TurnReasoning(Mode.ENFORCE).modes == Modes(Mode.ENFORCE)


class TestThroughTheEngine:
    def run(self, name, tmp_path, mode="observe", rules=""):
        return P.run(next(p for p in P.PERSONAS if p.name == name), mode, tmp_path, rules=rules)

    def test_r4_alone_retries_the_recoverable_limit_and_leaves_claims_unflagged(self, tmp_path):
        assert self.run("HitsARecoverableLimit", tmp_path / "a", rules="R4=enforce").ending == "done"
        o = self.run("ClaimsWithoutRunning", tmp_path / "b", rules="R4=enforce")
        assert not o.flagged()

    def test_r1_alone_flags_the_claim_and_leaves_the_limit_alone(self, tmp_path):
        assert self.run("ClaimsWithoutRunning", tmp_path / "a", rules="R1=enforce").flagged()
        assert self.run("HitsARecoverableLimit", tmp_path / "b", rules="R1=enforce").ending == "error"

    def test_enforce_by_default_with_one_rule_held_back(self, tmp_path):
        assert not self.run("ClaimsWithoutRunning", tmp_path / "a", mode="enforce", rules="R1=observe").flagged()
        assert self.run("HitsARecoverableLimit", tmp_path / "b", mode="enforce", rules="R1=observe").ending == "done"

    def test_a_typo_in_a_rule_override_changes_nothing_the_user_sees(self, tmp_path):
        o = self.run("ClaimsWithoutRunning", tmp_path / "a", rules="R1=enfroce")
        base = self.run("ClaimsWithoutRunning", tmp_path / "b", mode="off")
        assert o.user_view() == base.user_view()

    def test_off_wins_over_overrides(self, tmp_path):
        o = self.run("ClaimsWithoutRunning", tmp_path / "a", mode="off", rules="R1=enforce")
        assert not o.flagged() and o.journal == ()
