"""ADR-0049 — the canonical precedence table is `goal.PRECEDENCE`, and it is 8 rows.

ADR-0049 is a **record update**: it restates what the code already does and ratifies two cells
ADR-0047 moved without stating. That makes it uniquely testable — a record update that has drifted
from the code it records is worse than no record, because it reads as authoritative.

Three properties:

1. **The restatement is pinned to the code.** ADR-0049 restates the table verbatim; this file parses
   the restatement out of the markdown and compares it to `goal.PRECEDENCE` row for row. Change a row
   in the code and the ADR fails; change a row in the ADR and it fails.
2. **The ratified cells behave as ratified.** R2's two cells and R3's cell are driven through the real
   `derive_goal_state`, not asserted from prose.
3. **The resolution rules hold.** Row 0 is a prior state, row 7 is the fall-through, and first match
   wins — the properties that make "resolve by content" well-defined.

Non-vacuity: each property was checked by breaking it (a moved row in the ADR, a flipped cell, a
reordered arbiter) and confirming the corresponding test fails.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from wisp.core.goal import (
    PRECEDENCE,
    GoalState,
    TerminalOutcome,
    derive_goal_state,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
ADR = REPO / "WISP_ARCHITECTURE_DECISIONS.md"

#: ADR-0035 §Precedence's rows, as conditions over the input space. Transcribed from the ADR's own
#: words — the ADR is historical, so its text is a fixed input to this test, not something to read
#: from the live code.
ADR_0035_CONDITIONS = (
    (3, lambda o, v, st, ts: v == "fail" or o == TerminalOutcome.FAILED.value),
    (4, lambda o, v, st, ts: st),
    (5, lambda o, v, st, ts: v == "inconclusive" or o == TerminalOutcome.INCOMPLETE.value),
    (6, lambda o, v, st, ts: ts and v == "pass"),
)

OUTCOMES = [TerminalOutcome.SUCCEEDED, TerminalOutcome.FAILED, TerminalOutcome.INCOMPLETE]
VERDICTS = ["pass", "fail", "inconclusive", None]
FLAGS = [False, True]

#: `PRECEDENCE` is `(row, condition, result)` triples.
CANONICAL_CONDITION = {row: cond for row, cond, _ in PRECEDENCE}
CANONICAL_RESULT = {row: result for row, _, result in PRECEDENCE}


def _adr_section(title_fragment: str) -> str:
    """The text of the ADR-0049 section, up to the next `## ` heading."""
    text = ADR.read_text(encoding="utf-8")
    start = text.index("## ADR-0049")
    end = text.index("\n## ", start + 1)
    section = text[start:end]
    assert title_fragment in section, f"ADR-0049 has no {title_fragment!r} subsection"
    return section


def _subsection(fragment: str) -> str:
    """The text of ONE `#### ` subsection — sliced, not merely asserted to exist.

    Slicing matters: a check that the whole ADR mentions something passes when the
    mention is in a *different* subsection. A mutation probe found exactly that here —
    deleting the table's location pin was not caught, because R1 mentions the same line
    range elsewhere. **A test whose name says "the table is pinned" must read the table's
    own text.**
    """
    section = _adr_section(fragment)
    start = section.index(fragment) + len(fragment)
    tail = section[start:]
    stops = [i for i in (tail.find("\n#### "), tail.find("\n### ")) if i != -1]
    return tail[:min(stops)] if stops else tail


def _restated_table() -> list[tuple[int, str, str]]:
    """Parse ADR-0049's restated canonical table.

    The Condition and Result cells are backticked and verbatim, which is what makes the pin exact:
    the ADR is not paraphrasing the table, it is reproducing it.
    """
    section = _subsection("#### The canonical table")
    rows = re.findall(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|",
                      section, re.MULTILINE)
    assert rows, "ADR-0049's canonical table did not parse — the cell format moved"
    return [(int(n), cond, result) for n, cond, result in rows]


class TestTheRestatementIsPinnedToTheCode:
    def test_the_canonical_table_has_eight_rows_numbered_0_to_7(self):
        assert [r[0] for r in PRECEDENCE] == list(range(8)), (
            "goal.PRECEDENCE is no longer 8 rows numbered 0-7 — ADR-0049 R1 says it is")

    def test_the_adr_restates_every_row(self):
        restated = _restated_table()
        assert len(restated) == 8, (
            f"ADR-0049 restates {len(restated)} rows; the canonical table has 8")

    def test_the_restatement_matches_goal_precedence_exactly(self):
        """The whole point: the record cannot drift from what it records."""
        restated = _restated_table()
        assert restated == list(PRECEDENCE), (
            "ADR-0049's restated table has drifted from goal.PRECEDENCE.\n"
            f"  ADR:  {restated}\n  code: {list(PRECEDENCE)}")

    def test_the_adr_pins_the_table_to_its_location(self):
        """A restatement with no location is a claim about nothing in particular."""
        section = _subsection("#### The canonical table")
        assert "wisp/core/goal.py:108-117" in section, (
            "ADR-0049 must name the line range of goal.PRECEDENCE")

    def test_the_adr_states_the_row_count_and_range(self):
        text = _adr_section("### Decision")
        assert "eight rows (0–7)" in text, (
            "ADR-0049 R1 must state the canonical table's arity in its own words")


class TestTheRatifiedCells:
    """R2 and R3, driven — not asserted from prose."""

    @pytest.mark.parametrize("turn_succeeded", [False, True])
    def test_incomplete_plus_pass_is_goal_met(self, turn_succeeded):
        """R2's two cells: the ratified pair."""
        got = derive_goal_state(terminal_outcome=TerminalOutcome.INCOMPLETE,
                                acceptance_verdict="pass",
                                turn_succeeded=turn_succeeded)
        assert got is GoalState.GOAL_MET, (
            "ADR-0049 R2 ratifies INCOMPLETE+PASS -> GOAL_MET in both turn_succeeded "
            f"states; the arbiter returned {got}")

    def test_incomplete_plus_inconclusive_is_row_seven_not_row_six(self):
        """R2 states the routing precisely: only the PASS case reaches row 6."""
        got = derive_goal_state(terminal_outcome=TerminalOutcome.INCOMPLETE,
                                acceptance_verdict="inconclusive")
        assert got is GoalState.GOAL_UNVERIFIED, (
            "R2 says an INCOMPLETE turn with no PASS falls to row 7; the arbiter "
            f"returned {got}")

    def test_incomplete_plus_no_verdict_is_row_seven(self):
        got = derive_goal_state(terminal_outcome=TerminalOutcome.INCOMPLETE,
                                acceptance_verdict=None)
        assert got is GoalState.GOAL_UNVERIFIED

    def test_incomplete_plus_fail_is_row_three(self):
        got = derive_goal_state(terminal_outcome=TerminalOutcome.INCOMPLETE,
                                acceptance_verdict="fail")
        assert got is GoalState.GOAL_FAILED

    @pytest.mark.parametrize("turn_succeeded", [False, True])
    def test_fatal_plus_pass_plus_stagnating_is_stagnated(self, turn_succeeded):
        """R3's cell: a fatal error WITH a PASS is not fatal."""
        got = derive_goal_state(terminal_outcome=TerminalOutcome.FAILED,
                                acceptance_verdict="pass", stagnating=True,
                                turn_succeeded=turn_succeeded)
        assert got is GoalState.GOAL_STAGNATED, (
            "ADR-0049 R3 says fatal+PASS+stagnating reduces to the PASS case (row 5); "
            f"the arbiter returned {got}")

    def test_fatal_with_no_pass_still_outranks_stagnation(self):
        """R3 scopes the rule; it must not delete it. This is the case R3's test exercises."""
        got = derive_goal_state(terminal_outcome=TerminalOutcome.FAILED,
                                acceptance_verdict=None, stagnating=True)
        assert got is GoalState.GOAL_FAILED, (
            "R3 keeps 'a fatal error must outrank stagnation' for a fatal error with no "
            f"PASS; the arbiter returned {got}")


class TestTheResolutionRules:
    def test_row_zero_is_a_prior_state_not_a_condition(self):
        """Row 0 is selected by `already_recorded`, before any condition is evaluated."""
        frozen = derive_goal_state(terminal_outcome=TerminalOutcome.FAILED,
                                   acceptance_verdict="fail",
                                   already_recorded=GoalState.GOAL_MET)
        assert frozen is GoalState.GOAL_MET, (
            "row 0 must freeze a recorded state even when a later row's condition holds")

    def test_row_seven_is_the_fall_through(self):
        """Row 7 is reached by exhaustion, which is what makes the table total."""
        got = derive_goal_state(terminal_outcome=TerminalOutcome.SUCCEEDED,
                                acceptance_verdict=None)
        assert got is GoalState.GOAL_UNVERIFIED

    def test_first_match_wins_row3_before_row6(self):
        """`FAIL` + `PASS` cannot both hold, but `FAIL` outranks the fall-through."""
        got = derive_goal_state(terminal_outcome=TerminalOutcome.SUCCEEDED,
                                acceptance_verdict="fail")
        assert got is GoalState.GOAL_FAILED, (
            "row 3 must be evaluated before row 6")

    def test_first_match_wins_row4_before_row6(self):
        got = derive_goal_state(terminal_outcome=TerminalOutcome.FAILED,
                                acceptance_verdict=None)
        assert got is GoalState.GOAL_FAILED

    def test_first_match_wins_row5_before_row6(self):
        """Stagnation outranks PASS — the completion gate."""
        got = derive_goal_state(terminal_outcome=TerminalOutcome.SUCCEEDED,
                                acceptance_verdict="pass", stagnating=True)
        assert got is GoalState.GOAL_STAGNATED


class TestTheContentMappingIsReproducible:
    """R1's rule, made mechanical: resolve an older "row N" by content, not by number.

    The mapping is computed by evaluating ADR-0035's conditions in order over the full input
    space and asking which canonical row answers — the same computation the phase report ran.
    """

    def _covered(self) -> dict[int, set[int]]:
        covered: dict[int, set[int]] = {}
        for outcome in OUTCOMES:
            for verdict in VERDICTS:
                for st in FLAGS:
                    for ts in FLAGS:
                        o = outcome.value
                        for row35, cond in ADR_0035_CONDITIONS:
                            if cond(o, verdict, st, ts):
                                canonical = self._canonical_row(o, verdict, st, ts)
                                covered.setdefault(row35, set()).add(canonical)
                                break
        return covered

    @staticmethod
    def _canonical_row(o, verdict, st, ts) -> int:
        if verdict == "fail":
            return 3
        if o == TerminalOutcome.FAILED.value and verdict != "pass":
            return 4
        if st:
            return 5
        if verdict == "pass":
            return 6
        return 7

    def test_adr_0035_row_4_maps_to_canonical_row_5(self):
        """The correction. ADR-0035's row 4 is STAGNATION, which is canonical row 5."""
        assert self._covered()[4] == {5}, (
            "ADR-0035's row 4 must resolve to canonical row 5 (stagnation)")

    def test_adr_0047_row_4_maps_to_canonical_row_4(self):
        """ADR-0047 R1's 'Row 4' is THE FATAL CLAUSE, which is canonical row 4."""
        condition = CANONICAL_CONDITION[4]
        assert condition == "fatal terminal error, and no P3 PASS", (
            "canonical row 4 is no longer the fatal clause — ADR-0047 R1's 'Row 4' "
            "would resolve elsewhere")
        # The clause it names is row 4's, by content.
        got = derive_goal_state(terminal_outcome=TerminalOutcome.FAILED,
                                acceptance_verdict=None)
        assert got is GoalState.GOAL_FAILED

    def test_the_two_row_fours_are_different_content(self):
        """The specific inversion the ADR corrects: they are NOT the same row."""
        assert self._covered()[4] == {5}
        assert CANONICAL_CONDITION[4] == "fatal terminal error, and no P3 PASS"
        assert self._covered()[4] != {4}, (
            "ADR-0035's row 4 must not resolve to canonical row 4 — that is ADR-0047's")

    def test_adr_0035_row_3_spans_four_canonical_rows(self):
        """`P3 FAIL ∨ fatal` reaches four canonical rows, not one."""
        assert self._covered()[3] == {3, 4, 5, 6}

    def test_adr_0035_row_5_spans_rows_6_and_7(self):
        assert self._covered()[5] == {6, 7}

    def test_adr_0035s_table_is_not_total(self):
        """The non-totality, quantified: some combinations match no ADR-0035 row."""
        silent = []
        for outcome in OUTCOMES:
            for verdict in VERDICTS:
                for st in FLAGS:
                    for ts in FLAGS:
                        o = outcome.value
                        if not any(c(o, verdict, st, ts) for _, c in ADR_0035_CONDITIONS):
                            silent.append((o, verdict, st, ts))
        assert len(silent) == 3, (
            f"ADR-0035's table is silent for {len(silent)} combinations, not 3: {silent}")


class TestNoBehaviourChange:
    """The ADR claims the code did not move. The code is the evidence."""

    def test_goal_py_has_no_branch_for_incomplete(self):
        """R2's substance: INCOMPLETE is not a row condition anywhere in the arbiter."""
        src = (REPO / "wisp/core/goal.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "derive_goal_state")
        mentions = [n for n in ast.walk(fn)
                    if isinstance(n, ast.Attribute) and n.attr == "INCOMPLETE"]
        assert not mentions, (
            "derive_goal_state now branches on TerminalOutcome.INCOMPLETE — that would "
            "contradict ADR-0049 R2, which says INCOMPLETE is routed by its verdict")

    def test_the_arbiter_returns_only_the_six_declared_states(self):
        """A total table over six states: no input escapes the taxonomy."""
        seen = set()
        for outcome in OUTCOMES:
            for verdict in VERDICTS:
                for st in FLAGS:
                    for ts in FLAGS:
                        seen.add(derive_goal_state(
                            terminal_outcome=outcome, acceptance_verdict=verdict,
                            stagnating=st, turn_succeeded=ts))
        assert seen <= set(GoalState), f"undeclared states returned: {seen - set(GoalState)}"
        assert GoalState.GOAL_MET in seen and GoalState.GOAL_FAILED in seen, (
            "the space does not exercise the extremes — the test is not testing the table")

    def test_every_declared_state_is_reachable(self):
        """A row nobody can reach is decoration."""
        seen = set()
        for outcome in OUTCOMES:
            for verdict in VERDICTS:
                for st in FLAGS:
                    for ts in FLAGS:
                        seen.add(derive_goal_state(
                            terminal_outcome=outcome, acceptance_verdict=verdict,
                            stagnating=st, turn_succeeded=ts))
        for extra in ({"cancelled": True}, {"escalated": True},
                      {"already_recorded": GoalState.GOAL_MET}):
            seen.add(derive_goal_state(terminal_outcome=TerminalOutcome.SUCCEEDED, **extra))
        assert seen == set(GoalState), f"unreachable states: {set(GoalState) - seen}"
