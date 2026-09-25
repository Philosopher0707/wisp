"""ADR-0051 — the acceptance gate's enablement contract, and the property that blocks it.

ADR-0051 **amends** ADR-0016's 3b condition. Its R1 is a *precondition on the criteria set*:
the gate may be enabled only when the turn path's required-criteria set contains at least one
required criterion not derivable from `VerificationFloorGuard`. Today it does not, because the
turn path's acceptance verdict is a **pure projection** of that guard:

    runtime.py:1189-1190   _acceptance = floor_guard_verdict(guard).verdict
    verification.py:236    floor_guard_criteria()  <- the ONE producer on the turn path
    verification.py:252    check = (not guard.wrote_code) or guard.resolved()
    stateless.py:911       guard.rejection()       <- ALREADY wired at the pre-`done` gate

Three properties, and the third is a tripwire rather than an invariant:

1. **The projection is exact.** Over the reachable guard state space, `verdict == FAIL` holds iff
   the guard is in its own blocking condition — 0 disagreements. This is *why* the gate is
   redundant (FAIL-keyed) or harmful (non-PASS-keyed), and it is the precondition R1 names.
2. **The three authorities ADR-0051 R8 forbids touching are unchanged** — `goal.PRECEDENCE`'s eight
   rows and its ratified cells, `VerificationFloorGuard`'s own semantics, and the turn predicate
   `turn_succeeded` derives from.
3. **No unwired flag was added.** R1 says the flag is *not* added to `config.py` while the
   precondition is unmet, because a flag whose gate cannot fire is the written-but-unwired control
   this repository has already diagnosed as its dominant pathology. This test asserts the absence,
   and **fails the moment someone adds it** — which is the intended signal: adding it requires
   satisfying R1 first.

Non-vacuity: each property was checked by breaking it (a flipped verdict in the projection, a moved
`PRECEDENCE` row, an added `WISP_ACCEPTANCE_GATE` read) and confirming the corresponding test fails.
"""
from __future__ import annotations

import itertools
import pathlib

from wisp.core.acceptance import Verdict
from wisp.core.goal import (
    PRECEDENCE,
    GoalState,
    derive_goal_state,
    terminal_outcome_from_evidence,
)
from wisp.core.verification import (
    VerificationFloorGuard,
    floor_guard_verdict,
)

REPO = pathlib.Path(__file__).resolve().parents[2]

#: The turn path's only criteria producer, named so the precondition's subject is explicit.
TURN_PATH_CRITERIA_PRODUCER = "wisp/core/verification.py:floor_guard_criteria"


def _floor_blocks(guard: VerificationFloorGuard) -> bool:
    """`rejection()`'s own precondition, before any budget is consulted.

    `rejection()` returns None early iff `not enabled or not wrote_code`, and again iff
    `verify_ok_after_edit is True`. This is that predicate, factored out.
    """
    return bool(
        guard.enabled
        and guard.wrote_code
        and guard.verify_ok_after_edit is not True
    )


def _floor_surrendered(guard: VerificationFloorGuard) -> bool:
    return bool(
        guard.turns_used >= guard.min_turns
        and guard.nudges_used >= guard.max_nudges
    )


def _states() -> list[VerificationFloorGuard]:
    """The reachable guard state space: 2 × 2 × 3 × 4 × 4 = 192."""
    out = []
    for enabled, wrote_code, verify, nudges, turns in itertools.product(
        (True, False), (True, False), (True, False, None), (0, 1, 2, 3), (0, 4, 5, 6),
    ):
        out.append(VerificationFloorGuard(
            enabled=enabled,
            wrote_code=wrote_code,
            verify_ok_after_edit=verify,
            nudges_used=nudges,
            turns_used=turns,
        ))
    return out


# ── 1. The projection — the precondition R1 names ───────────────────────────


class TestTheVerdictIsAProjectionOfTheFloorGuard:
    """R1's subject: what the turn path's verdict actually is."""

    def test_the_state_space_is_the_documented_size(self):
        assert len(_states()) == 192

    def test_fail_holds_iff_the_guard_is_in_its_own_blocking_condition(self):
        """The load-bearing equivalence. A FAIL-keyed gate withholds under the
        condition `rejection()` already tests — same condition, same nudge, same budget."""
        disagreements = [
            g for g in _states()
            if (floor_guard_verdict(g).verdict is Verdict.FAIL) != _floor_blocks(g)
        ]
        assert disagreements == [], (
            f"{len(disagreements)} state(s) where the verdict is not the guard's blocking "
            f"condition — R1's precondition may now be MET, and ADR-0051 must be revisited "
            f"before the gate is enabled. First: "
            f"{[(g.enabled, g.wrote_code, g.verify_ok_after_edit, g.nudges_used, g.turns_used) for g in disagreements[:3]]}"
        )

    def test_a_non_pass_keyed_gate_would_block_states_the_guard_allows(self):
        """144 states: 96 where the guard is DISABLED, 48 where it is enabled and nothing
        was mutated. A `!= PASS` gate withholds `done` on every read-only turn."""
        allowed_non_pass = [
            g for g in _states()
            if floor_guard_verdict(g).verdict is not Verdict.PASS and not _floor_blocks(g)
        ]
        assert len(allowed_non_pass) == 144
        assert sum(1 for g in allowed_non_pass if not g.enabled) == 96
        assert sum(1 for g in allowed_non_pass if g.enabled and not g.wrote_code) == 48

    def test_a_fail_keyed_gate_adds_a_second_budget_after_surrender(self):
        """8 states where the floor guard has already surrendered honestly and the verdict
        is still FAIL: a FAIL-keyed gate would withhold again, on the same condition."""
        after_surrender = [
            g for g in _states()
            if floor_guard_verdict(g).verdict is Verdict.FAIL and _floor_surrendered(g)
        ]
        assert len(after_surrender) == 8

    def test_the_verdict_still_carries_the_three_way_distinction(self):
        """The verdict is not useless — it re-expresses the guard's state in one vocabulary.
        That is what `derive_goal_state` consumes; what it does NOT carry is a new
        withholding condition."""
        seen = {floor_guard_verdict(g).verdict for g in _states()}
        assert seen == {Verdict.PASS, Verdict.FAIL, Verdict.INCONCLUSIVE}

    def test_the_turn_paths_criteria_producer_is_still_the_floor(self):
        """R1's precondition, checked at the source rather than inferred.

        If a second producer reaches the turn path, this file's premise is gone and the
        test above will already have failed — this one names the reason."""
        runtime = (REPO / "wisp/core/runtime.py").read_text()
        assert "floor_guard_verdict" in runtime, (
            "the turn path no longer computes its verdict from the floor guard — "
            "re-derive R1's precondition before enabling anything"
        )
        producers = []
        for path in (REPO / "wisp").rglob("*.py"):
            if path.name == "acceptance.py":
                continue
            text = path.read_text()
            if "AcceptanceCriteria(" in text:
                producers.append(f"{path.relative_to(REPO)}")
        assert sorted(producers) == ["wisp/core/convergence.py", "wisp/core/verification.py"], (
            f"the set of AcceptanceCriteria producers changed: {producers}. "
            f"Exactly one of them ({TURN_PATH_CRITERIA_PRODUCER}) is on the turn path; "
            f"if that is no longer true, ADR-0051 R1 may be satisfied."
        )


# ── 2. The three non-violations (ADR-0051 R8) ───────────────────────────────


class TestTheThreeNonViolations:
    """R8: asserted, not merely stated."""

    def test_precedence_is_still_eight_rows(self):
        assert [row[0] for row in PRECEDENCE] == list(range(8))

    def test_the_ratified_cells_are_unchanged(self):
        """ADR-0049 R1's canonical table, driven — not read."""
        assert derive_goal_state(
            terminal_outcome="succeeded", acceptance_verdict=Verdict.PASS,
            turn_succeeded=True,
        ) is GoalState.GOAL_MET
        assert derive_goal_state(
            terminal_outcome="failed", acceptance_verdict=Verdict.PASS,
            turn_succeeded=True,
        ) is GoalState.GOAL_MET, "row 4 must not fire when a PASS exists (ADR-0047 R1)"
        assert derive_goal_state(
            terminal_outcome="failed", acceptance_verdict=Verdict.FAIL,
            turn_succeeded=True,
        ) is GoalState.GOAL_FAILED
        assert derive_goal_state(
            terminal_outcome="succeeded", acceptance_verdict=Verdict.INCONCLUSIVE,
            turn_succeeded=True,
        ) is GoalState.GOAL_UNVERIFIED

    def test_the_floor_guards_own_semantics_are_unchanged(self):
        """`rejection()`'s contract: no mutation or verified ⇒ never blocks."""
        untouched = VerificationFloorGuard(wrote_code=False)
        assert untouched.rejection() is None

        verified = VerificationFloorGuard(wrote_code=True, verify_ok_after_edit=True)
        assert verified.rejection() is None

        failed = VerificationFloorGuard(wrote_code=True, verify_ok_after_edit=False)
        assert failed.rejection() is not None

    def test_the_turn_predicate_is_still_terminal_evidence(self):
        """ADR-0044 R2 — `turn_succeeded` is a projection of this, computed once."""
        assert terminal_outcome_from_evidence(
            saw_done=True, saw_fatal_error=False).value == "succeeded"
        assert terminal_outcome_from_evidence(
            saw_done=True, saw_fatal_error=True).value == "failed"
        assert terminal_outcome_from_evidence(
            saw_done=False, saw_fatal_error=False).value == "incomplete"


# ── 3. The tripwire: no unwired flag was added (ADR-0051 R1) ────────────────


class TestNoUnwiredAcceptanceGateFlagWasAdded:
    """R1: the flag is named but NOT added while the precondition is unmet.

    This test is a **tripwire**. It is expected to fail the moment a non-floor criteria
    source reaches the turn path and someone adds the flag — which is the correct signal,
    because that change requires re-running the measurement ADR-0051 R2 specifies.
    """

    def test_the_flag_is_absent_from_production(self):
        hits = [
            str(p.relative_to(REPO))
            for p in (REPO / "wisp").rglob("*.py")
            if "WISP_ACCEPTANCE_GATE" in p.read_text()
            or "acceptance_gate" in p.read_text()
        ]
        assert hits == [], (
            f"`acceptance_gate` / `WISP_ACCEPTANCE_GATE` appears in production: {hits}. "
            f"ADR-0051 R1 forbids adding the flag while the turn path's criteria set is a "
            f"projection of the floor guard — a flag whose gate cannot fire is a "
            f"written-but-unwired control. Satisfy R1 first, then re-run R2's measurement."
        )

    def test_the_flag_is_not_read_by_the_config_reader(self):
        config = (REPO / "wisp/config.py").read_text()
        assert "acceptance_gate" not in config
