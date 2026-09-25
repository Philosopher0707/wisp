"""ADR-0053 — the turn path's criteria set carries the objective's declared criteria.

ADR-0051 R1's precondition is that the turn path's required-criteria set contain at least
one required criterion **not derivable from `VerificationFloorGuard`'s own state**.
Measured in ADR-0051, it did not: `verdict == FAIL` agreed with `guard.rejection()`
192/192 times over the guard state space.

This file drives the source that satisfies it, and pins the three things a phase must not
lose:

1. **The criteria set.** With the flag OFF it is exactly `floor_guard_criteria(guard)` —
   today's behaviour, byte-for-byte. With it ON and a declaration at the head of the
   prompt, the declared criteria join it.
2. **The gate's condition is not the floor guard's.** The discriminating case is a
   declared criterion that fails while the floor guard is *satisfied*: floor-only that
   turn is `PASS`/`GOAL_MET`; with the declaration it is `FAIL`/`GOAL_FAILED`.
3. **The three non-violations** (ADR-0051 R8, carried forward) — `turn_succeeded`,
   `VerificationFloorGuard` and `goal.PRECEDENCE` are unchanged, asserted not stated.

Non-vacuity: each class was checked by breaking it (removing the union, widening the
gate's condition to any FAIL, flipping a `PRECEDENCE` row) and confirming the
corresponding test fails.
"""
from __future__ import annotations

import pathlib

import pytest

from wisp.config import WispConfig
from wisp.core.acceptance import Verdict
from wisp.core.convergence import CriteriaDeclarationRejected
from wisp.core.goal import (
    PRECEDENCE,
    GoalState,
    derive_goal_state,
    terminal_outcome_from_evidence,
)
from wisp.core.turn_criteria import (
    TURN_CRITERIA_SOURCE_ENV,
    floor_only,
    turn_acceptance_verdict,
    turn_criteria,
    verdict_keys_on_declared,
)
from wisp.core.verification import (
    FLOOR_CRITERION_ID,
    VerificationFloorGuard,
)

REPO = pathlib.Path(__file__).resolve().parents[2]

APP_PY = "def parse_duration(text):\n    return 0\n"

DECL_PRESENT = (
    "--- criteria ---\n"
    "symbol_defined: app.py::parse_duration\n"
    "--- /criteria ---\n"
    "Fix the bug in app.py.\n"
)
DECL_ABSENT = (
    "--- criteria ---\n"
    "symbol_defined: app.py::definitely_not_defined_here\n"
    "--- /criteria ---\n"
    "Fix the bug in app.py.\n"
)
PLAIN = "Fix the bug in app.py.\n"
#: A head block that is malformed — ADR-0050 R4's rejected case.
DECL_MALFORMED = "--- criteria ---\nsymbol_defined:\n--- /criteria ---\nFix it.\n"


@pytest.fixture
def workspace(tmp_path: pathlib.Path) -> str:
    (tmp_path / "app.py").write_text(APP_PY)
    return str(tmp_path)


def _goal(verdict) -> GoalState:
    return derive_goal_state(terminal_outcome="succeeded",
                             acceptance_verdict=verdict.verdict,
                             turn_succeeded=True)


# ── 1. The criteria set ────────────────────────────────────────────────────


class TestTheCriteriaSet:
    def test_off_is_exactly_the_floor_guards_criterion(self, workspace):
        guard = VerificationFloorGuard()
        tc = turn_criteria(guard, DECL_ABSENT, workspace, enabled=False)
        assert [c.criteria_id for c in tc.criteria] == [FLOOR_CRITERION_ID]
        assert tc.declared is False

    def test_off_is_the_same_set_the_floor_producer_gives(self, workspace):
        """The OFF path must be the floor producer's own output, not a copy of it."""
        guard = VerificationFloorGuard()
        off = turn_criteria(guard, DECL_ABSENT, workspace, enabled=False)
        base = floor_only(guard)
        assert [c.criteria_id for c in off.criteria] == [c.criteria_id for c in base.criteria]
        assert off.declared_ids == ()

    def test_on_adds_the_declared_criteria(self, workspace):
        guard = VerificationFloorGuard()
        tc = turn_criteria(guard, DECL_ABSENT, workspace, enabled=True)
        ids = [c.criteria_id for c in tc.criteria]
        assert ids[0] == FLOOR_CRITERION_ID, "the floor criterion must still be in the set"
        assert len(ids) > 1, "the declaration contributed nothing — R1 is unmet"
        assert tc.declared is True
        assert all(cid != FLOOR_CRITERION_ID for cid in tc.declared_ids)

    def test_on_with_a_plain_prompt_adds_nothing(self, workspace):
        """No declaration → the prose grammar is NOT consulted on the turn path."""
        guard = VerificationFloorGuard()
        tc = turn_criteria(guard, PLAIN, workspace, enabled=True)
        assert [c.criteria_id for c in tc.criteria] == [FLOOR_CRITERION_ID]
        assert tc.declared is False

    def test_a_mid_prose_block_is_not_a_declaration(self, workspace):
        """ADR-0050 R1 — the block must be at the HEAD. Not at the head → not a
        declaration (None), never a rejection."""
        guard = VerificationFloorGuard()
        prompt = "Fix the bug.\n\n--- criteria ---\nsymbol_defined: app.py::parse_duration\n--- /criteria ---\n"
        tc = turn_criteria(guard, prompt, workspace, enabled=True)
        assert [c.criteria_id for c in tc.criteria] == [FLOOR_CRITERION_ID]
        assert tc.declared is False


# ── 2. The gate has something to gate on ──────────────────────────────────


class TestTheGateHasSomethingToGateOn:
    """The point of the whole ADR: the gate's condition must NOT be the floor
    guard's condition under another name."""

    def test_a_declared_failure_moves_a_turn_the_floor_guard_passes(self, workspace):
        """The discriminating case. A mutation-verified turn satisfies the floor guard
        completely — `rejection()` is None, floor-only verdict is PASS — while the
        declared criterion fails."""
        guard = VerificationFloorGuard(wrote_code=True, verify_ok_after_edit=True)
        assert guard.rejection() is None, "the floor guard is satisfied — that is the premise"

        off, _ = turn_acceptance_verdict(guard, DECL_ABSENT, workspace, enabled=False)
        on, _ = turn_acceptance_verdict(guard, DECL_ABSENT, workspace, enabled=True)

        assert off.verdict is Verdict.PASS
        assert _goal(off) is GoalState.GOAL_MET
        assert on.verdict is Verdict.FAIL, "the declared criterion did not move the verdict"
        assert _goal(on) is GoalState.GOAL_FAILED

    def test_a_read_only_turn_moves_too(self, workspace):
        """Nothing mutated: the floor criterion is vacuously satisfied and unevidenced,
        so floor-only is INCONCLUSIVE. The declaration makes it decisive."""
        guard = VerificationFloorGuard(wrote_code=False)
        off, _ = turn_acceptance_verdict(guard, DECL_ABSENT, workspace, enabled=False)
        on, _ = turn_acceptance_verdict(guard, DECL_ABSENT, workspace, enabled=True)
        assert off.verdict is Verdict.INCONCLUSIVE
        assert on.verdict is Verdict.FAIL

    def test_a_satisfied_declaration_changes_nothing(self, workspace):
        """The declaration is not a blanket pessimism: a satisfied one leaves the
        floor-only verdict alone."""
        guard = VerificationFloorGuard(wrote_code=True, verify_ok_after_edit=True)
        off, _ = turn_acceptance_verdict(guard, DECL_PRESENT, workspace, enabled=False)
        on, _ = turn_acceptance_verdict(guard, DECL_PRESENT, workspace, enabled=True)
        assert off.verdict is Verdict.PASS and on.verdict is Verdict.PASS

    def test_the_gate_condition_is_true_only_for_a_declared_failure(self, workspace):
        guard = VerificationFloorGuard(wrote_code=True, verify_ok_after_edit=True)
        on, _ = turn_acceptance_verdict(guard, DECL_ABSENT, workspace, enabled=True)
        assert verdict_keys_on_declared(on) is True

    def test_the_gate_condition_is_false_for_the_floor_guards_own_failure(self, workspace):
        """A FAIL the floor guard already enforces is the floor guard's case; keying a
        gate on it would duplicate `rejection()` — the redundancy ADR-0051 measured."""
        guard = VerificationFloorGuard(wrote_code=True, verify_ok_after_edit=False)
        on, _ = turn_acceptance_verdict(guard, DECL_ABSENT, workspace, enabled=True)
        assert on.verdict is Verdict.FAIL
        assert verdict_keys_on_declared(on) is False

    def test_the_gate_condition_is_false_for_pass_and_inconclusive(self, workspace):
        guard = VerificationFloorGuard()
        for prompt in (PLAIN, DECL_PRESENT):
            on, _ = turn_acceptance_verdict(guard, prompt, workspace, enabled=True)
            if on.verdict is not Verdict.FAIL:
                assert verdict_keys_on_declared(on) is False


# ── 3. The three non-violations (ADR-0051 R8) ─────────────────────────────


class TestTheThreeNonViolations:
    def test_turn_succeeded_is_still_terminal_evidence(self):
        assert terminal_outcome_from_evidence(
            saw_done=True, saw_fatal_error=False).value == "succeeded"
        assert terminal_outcome_from_evidence(
            saw_done=False, saw_fatal_error=False).value == "incomplete"

    def test_the_floor_guards_own_semantics_are_unchanged(self):
        assert VerificationFloorGuard(wrote_code=False).rejection() is None
        assert VerificationFloorGuard(
            wrote_code=True, verify_ok_after_edit=True).rejection() is None
        assert VerificationFloorGuard(
            wrote_code=True, verify_ok_after_edit=False).rejection() is not None

    def test_the_floor_criterion_id_is_unchanged(self):
        assert FLOOR_CRITERION_ID == "floor:verification"

    def test_precedence_is_unchanged_in_content_and_count(self):
        assert [row[0] for row in PRECEDENCE] == list(range(8))
        assert derive_goal_state(
            terminal_outcome="succeeded", acceptance_verdict=Verdict.PASS,
            turn_succeeded=True) is GoalState.GOAL_MET
        assert derive_goal_state(
            terminal_outcome="failed", acceptance_verdict=Verdict.PASS,
            turn_succeeded=True) is GoalState.GOAL_MET
        assert derive_goal_state(
            terminal_outcome="succeeded", acceptance_verdict=Verdict.FAIL,
            turn_succeeded=True) is GoalState.GOAL_FAILED


# ── 4. The flag ───────────────────────────────────────────────────────────


class TestTheFlag:
    def test_it_defaults_off(self):
        assert WispConfig().turn_criteria_source is False

    def test_the_env_var_is_named(self):
        assert TURN_CRITERIA_SOURCE_ENV == "WISP_TURN_CRITERIA_SOURCE"

    def test_it_is_read_once(self):
        """ADR-0002 — a flag read in two places can disagree with itself. One string
        literal, therefore one read."""
        src = (REPO / "wisp/core/runtime.py").read_text()
        assert src.count('"turn_criteria_source"') == 1, (
            "the flag is read at more than one site")

    def test_the_read_site_is_the_composition_point(self):
        src = (REPO / "wisp/core/runtime.py").read_text()
        assert 'turn_criteria_source_enabled' in src
        assert "turn_acceptance_verdict" in src, (
            "the runtime does not call the new source — the flag would gate nothing")

    def test_it_is_not_coupled_to_the_objective_level_flag(self):
        """ADR-0053 R8: one flag per concern. The turn path must not read
        `WISP_CRITERIA_STRUCTURED_DECLARATION`, which gates the objective-level
        derivation at a different composition point."""
        src = (REPO / "wisp/core/turn_criteria.py").read_text()
        assert "structured_declaration" not in src
        assert "STRUCTURED_DECLARATION" not in src


# ── 5. Today's behaviour when the flag is off ─────────────────────────────


class TestEveryCallerThatDoesNotSetTheFlagSeesTodaysBehaviour:
    def test_the_off_branch_is_still_the_floor_guard_verdict(self):
        """Structural pin: the OFF path must remain `floor_guard_verdict`, so a caller
        that never sets the flag is on today's code."""
        src = (REPO / "wisp/core/runtime.py").read_text()
        assert "floor_guard_verdict" in src, (
            "the floor path was replaced rather than branched — every caller now sees "
            "the new behaviour")

    def test_the_floor_path_and_the_new_source_agree_when_nothing_is_declared(
            self, workspace):
        """The strongest form of 'today's behaviour': for a prompt with no declaration,
        the two computations produce the same verdict over the whole guard space."""
        from wisp.core.verification import floor_guard_verdict
        import itertools
        for enabled, wrote, verify in itertools.product(
                (True, False), (True, False), (True, False, None)):
            g = VerificationFloorGuard(enabled=enabled, wrote_code=wrote,
                                       verify_ok_after_edit=verify)
            old = floor_guard_verdict(g).verdict
            new, _ = turn_acceptance_verdict(g, PLAIN, workspace, enabled=True)
            assert new.verdict is old, (enabled, wrote, verify)


# ── 6. A rejected declaration does not fall back ──────────────────────────


class TestARejectedDeclarationDoesNotFallBack:
    def test_a_malformed_head_declaration_raises(self, workspace):
        """ADR-0050 R4 — loud, and never a fallback: measuring the floor alone while
        the caller declared more is the silent downgrade R4 exists to prevent."""
        guard = VerificationFloorGuard()
        with pytest.raises(CriteriaDeclarationRejected):
            turn_criteria(guard, DECL_MALFORMED, workspace, enabled=True)

    def test_it_does_not_raise_when_the_flag_is_off(self, workspace):
        """With the flag off the turn path never looks at the prompt, so a caller that
        has not opted in cannot be broken by a malformed block."""
        guard = VerificationFloorGuard()
        tc = turn_criteria(guard, DECL_MALFORMED, workspace, enabled=False)
        assert [c.criteria_id for c in tc.criteria] == [FLOOR_CRITERION_ID]

    def test_the_runtime_does_not_swallow_it(self):
        """Structural pin: the parse must sit OUTSIDE the `except Exception` that
        guards 'verdict unavailable', or a rejection becomes a silent floor-only run."""
        src = (REPO / "wisp/core/runtime.py").read_text()
        i = src.index("turn_criteria_source_enabled:")
        window = src[i:i + 1400]
        assert "except Exception" not in window.split("elif _guard_for_goal")[0], (
            "the declared-criteria branch is inside a broad handler — a rejected "
            "declaration would be swallowed and the run would continue floor-only")
