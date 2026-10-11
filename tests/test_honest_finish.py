"""An honest finish. A real session (kvagent, 2026-10-07, docs/harness/field-observations-2026-10-07.md O-9 and O-10): the agent ran its tests many times with a
command whose exit status is `tail`'s (`pytest > f 2>&1; echo "EXIT:$?"; tail -40 f`), so the verification floor never counted it; the nudge said "no verification command
has been run" (false: one ran, it did not count); after the nudges were spent the floor let the turn end, the model answered with reasoning only, and the user got no summary
and no statement that the work is unverified. The engine now (1) says WHY a run did not count, (2) never lets a code-changing turn end unverified without saying so itself, and
(3) gives a round that ends with no answer one bounded nudge, then a plain statement."""

from __future__ import annotations

import pytest

from tests.reasoning import personas as P
from wisp.core.honest_finish import (
    NO_ANSWER_NOTE,
    compose_empty_round_nudge,
    is_empty_answer,
    uncounted_reason,
    unverified_note,
)
from wisp.core.verification import COMMAND_ARG, VerificationFloorGuard

pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")

MASKED = 'python3 -c "print(1)" > out.txt 2>&1; echo "EXIT:$?"; tail -5 out.txt'
PLAIN_VERIFY = "python3 -m compileall -q ."


def thinking_only():
    def _g():
        yield {"type": "thinking", "text": "The test failure is: it passes when it should fail..."}
        yield {"type": "done", "done_reason": "stop"}
    return _g


def play(rounds_fn, tmp_path, name="HonestFinish", **kw):
    return P.run(P.Persona(name, "honest finish", "none", rounds_fn, lambda o, ws: False), "off", tmp_path, **kw)


def edit(ws):
    return P.tool_round("write_file", {"path": str(ws / "sandbox.py"), "content": "x = 1\n"}, "c0")


def systems(o):
    return [str(e.get("text") or e.get("message") or "") for e in o.events if e.get("type") == "system"]


def note_count(o):
    return o.all_text.count("Harness note:") and o.all_text.count("UNVERIFIED")


class TestPureText:
    @pytest.mark.parametrize("text,empty", [("", True), (None, True), ("  \n\t ", True), ("x", False), ("Done.", False)])
    def test_is_empty_answer(self, text, empty):
        assert is_empty_answer(text) is empty

    def test_the_note_when_nothing_was_run(self):
        n = unverified_note(failed=False, uncounted=None)
        assert n.startswith("Harness note:") and "UNVERIFIED" in n and "no verification command" in n

    def test_the_note_when_the_last_run_failed(self):
        n = unverified_note(failed=True, uncounted=None)
        assert "UNVERIFIED" in n and "failed" in n

    def test_the_note_when_a_run_did_not_count_names_it_and_says_why(self):
        n = unverified_note(failed=False, uncounted=(MASKED, "not a test run whose exit status decides the command's result"))
        assert "UNVERIFIED" in n and "tail -5 out.txt" in n and "exit status" in n

    def test_a_long_multiline_command_is_bounded_and_one_line(self):
        n = unverified_note(failed=False, uncounted=("pytest\n" + "x" * 5000, "why"))
        assert "\n" not in n and "…" in n and len(n) < 330

    def test_the_empty_round_nudge_and_the_final_statement(self):
        assert "no answer" in compose_empty_round_nudge().lower() and "unverified" in compose_empty_round_nudge().lower()
        assert NO_ANSWER_NOTE.startswith("Harness note:")

    def test_uncounted_reason_text_is_stable(self):
        assert "exit status" in uncounted_reason(MASKED, "x")


class TestTheGuardRemembersARunThatDidNotCount:
    def guard(self):
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        return g

    def test_a_masked_run_is_remembered_with_its_reason(self):
        g = self.guard()
        g.note_tool_result("run_bash", "EXIT:1\n1 failed", {COMMAND_ARG: MASKED})
        assert g.verify_ok_after_edit is None and g.last_uncounted is not None
        cmd, why = g.last_uncounted
        assert cmd == MASKED and "exit status" in why

    def test_a_counted_run_clears_it(self):
        g = self.guard()
        g.note_tool_result("run_bash", "x", {COMMAND_ARG: MASKED})
        g.note_tool_result("run_bash", "", {COMMAND_ARG: PLAIN_VERIFY})
        assert g.verify_ok_after_edit is True and g.last_uncounted is None

    def test_nothing_is_remembered_without_a_command(self):
        g = self.guard()
        g.note_tool_result("run_bash", "ok", {})
        assert g.last_uncounted is None

    def test_reset_turn_forgets_it(self):
        g = self.guard()
        g.note_tool_result("run_bash", "x", {COMMAND_ARG: MASKED})
        g.reset_turn()
        assert g.last_uncounted is None


class TestTheReportedSession:
    def rounds(self, ws):
        return [edit(ws), P.tool_round("run_bash", {"command": MASKED}, "c1"), thinking_only()]

    def test_a_reasoning_only_ending_still_tells_the_user_the_work_is_unverified(self, tmp_path):
        o = play(self.rounds, tmp_path)
        assert o.ending == "done" and "UNVERIFIED" in o.all_text and o.all_text.count("Harness note:") >= 1

    def test_the_first_nudge_names_the_command_that_did_not_count_and_does_not_say_none_ran(self, tmp_path):
        first = systems(play(self.rounds, tmp_path))[0]
        assert "tail -5 out.txt" in first and "exit status" in first and "no verification command (tests/linter) has been run" not in first

    def test_the_turn_stays_bounded(self, tmp_path):
        assert play(self.rounds, tmp_path).provider_calls <= 7


class TestFloorSurrenderWithAnAnswer:
    def rounds(self, ws):
        return [edit(ws), P.tool_round("run_bash", {"command": MASKED}, "c1"), P.content_round("Fixed it, everything works.")]

    def test_the_harness_adds_its_own_unverified_line_exactly_once(self, tmp_path):
        o = play(self.rounds, tmp_path)
        assert o.all_text.count("UNVERIFIED") == 1 and o.all_text.startswith("Fixed it") or "Fixed it" in o.all_text

    def test_a_model_that_already_says_unverified_is_not_repeated_back(self, tmp_path):
        def rounds(ws):
            return [edit(ws), P.tool_round("run_bash", {"command": MASKED}, "c1"), P.content_round("Changed sandbox.py. The work is UNVERIFIED: I could not run the tests.")]

        o = play(rounds, tmp_path)
        assert "Harness note:" not in o.all_text  # the scripted provider repeats its last round, so the model's own sentence appears per round; the harness adds nothing


class TestNoNoteWhenNoneIsOwed:
    def test_a_verified_turn_gets_no_note(self, tmp_path):
        def rounds(ws):
            return [edit(ws), P.tool_round("run_bash", {"command": PLAIN_VERIFY}, "c1"), P.content_round("Changed sandbox.py; compileall passes.")]

        o = play(rounds, tmp_path)
        assert "UNVERIFIED" not in o.all_text and o.provider_calls == 3

    def test_a_turn_that_changed_no_code_gets_no_note(self, tmp_path):
        o = play(lambda ws: [P.content_round("The answer is 42.")], tmp_path)
        assert "Harness note" not in o.all_text and o.provider_calls == 1


class TestAnEmptyRound:
    def test_one_nudge_then_a_plain_statement(self, tmp_path):
        o = play(lambda ws: [thinking_only()], tmp_path)
        assert o.ending == "done" and o.provider_calls == 2 and NO_ANSWER_NOTE in o.all_text
        assert len([s for s in systems(o) if "no answer" in s.lower()]) == 1

    def test_a_model_that_answers_after_the_nudge_is_left_alone(self, tmp_path):
        o = play(lambda ws: [thinking_only(), P.content_round("Here is the answer.")], tmp_path)
        assert o.provider_calls == 2 and "Here is the answer." in o.all_text and NO_ANSWER_NOTE not in o.all_text

    def test_a_normal_answer_is_never_nudged(self, tmp_path):
        o = play(lambda ws: [P.content_round("Done.")], tmp_path)
        assert o.provider_calls == 1 and not [s for s in systems(o) if "no answer" in s.lower()]

    def test_no_round_left_means_the_statement_without_a_nudge(self, tmp_path):
        o = play(lambda ws: [thinking_only()], tmp_path, max_iterations=1)
        assert o.provider_calls == 1 and NO_ANSWER_NOTE in o.all_text
