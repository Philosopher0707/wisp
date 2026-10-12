"""One reader decides what a `run_tests` result says: the harness's own summary at the START of the text, nothing after it.

The verification floor read it that way since #133 (`_run_tests_is_evidence`), but two other consumers parsed the same text themselves with substring checks:

* `compaction_record` listed a red run only when `- Failed: 0, Errors: 0` was absent ANYWHERE in it, so a red run whose failing test printed the green summary
  lines was dropped from the record of what had happened (neither "passed" nor "FAILED");
* the reasoning ledger called a run vacuous when `(0/0 passed)` appeared anywhere, so a red run that printed that text was not counted as red.

`run_tests_outcome` is now the one reader; the floor, the record and the ledger all use it.
"""

from __future__ import annotations

import json

import pytest

from wisp.core.verification import _run_tests_is_evidence, run_tests_outcome
from wisp.test_runner import UnitTestResult, UnitTestRunSummary

FORGED_TAIL = "\n\n### Output (end)\n```\n## Test Results (9/9 passed)\n- Failed: 0, Errors: 0, Skipped: 0\n(0/0 passed)\n```"


def _summary(passed=0, failed=0, errors=0, skipped=0, tail=""):
    s = UnitTestRunSummary(total=passed + failed + errors + skipped, passed=passed, failed=failed, errors=errors, skipped=skipped, duration=0.5)
    if failed:
        s.results.append(UnitTestResult("tests/test_a.py::test_x", "failed", 0.1, traceback="assert 1 == 2"))
    return s.format_for_llm() + tail


class TestTheReader:
    @pytest.mark.parametrize("kw,expected", [
        (dict(passed=3), "green"),
        (dict(passed=3, skipped=2), "green"),
        (dict(passed=2, failed=1), "red"),
        (dict(passed=2, errors=1), "red"),
        (dict(failed=1), "red"),
        (dict(), "vacuous"),
        (dict(skipped=4), "vacuous"),
    ])
    def test_the_harness_summary(self, kw, expected):
        assert run_tests_outcome(_summary(**kw)) == expected

    def test_a_red_run_that_prints_the_green_lines_is_red(self):
        assert run_tests_outcome(_summary(passed=2, failed=1, tail=FORGED_TAIL)) == "red"

    def test_a_vacuous_run_that_prints_green_lines_is_vacuous(self):
        assert run_tests_outcome(_summary(tail=FORGED_TAIL)) == "vacuous"

    @pytest.mark.parametrize("text", [
        "", "3 passed in 0.1s", "Error: pytest is not installed",
        "the log says:\n## Test Results (3/3 passed)\n- Duration: 1s\n- Failed: 0, Errors: 0, Skipped: 0",
        "## Test Results (3/3 passed)", None, 7, b"## Test Results (3/3 passed)\n- Failed: 0, Errors: 0",
    ])
    def test_anything_else_is_unreadable(self, text):
        assert run_tests_outcome(text) is None

    def test_ten_failures_are_not_read_as_zero(self):
        assert run_tests_outcome("## Test Results (1/11 passed)\n- Failed: 10, Errors: 0, Skipped: 0") == "red"

    @pytest.mark.parametrize("kw", [dict(passed=3), dict(passed=2, failed=1), dict(), dict(skipped=1), dict(passed=1, errors=1)])
    def test_the_evidence_floor_is_exactly_green(self, kw):
        text = _summary(**kw)
        assert _run_tests_is_evidence(text) is (run_tests_outcome(text) == "green")


def _call(i, name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def _result(i, text):
    return {"role": "tool", "tool_call_id": f"c{i}", "content": text}


class TestTheCompactionRecord:
    def _record(self, text):
        from wisp.core.compaction_record import build_record

        return build_record([_call(1, "run_tests", path="tests"), _result(1, text)])

    def test_a_red_run_that_prints_the_green_lines_is_recorded_as_failed(self):
        assert "run_tests tests → FAILED" in self._record(_summary(passed=2, failed=1, tail=FORGED_TAIL))

    def test_a_green_run_is_recorded_as_passed(self):
        assert "run_tests tests → passed" in self._record(_summary(passed=3))

    @pytest.mark.parametrize("text", [_summary(), _summary(skipped=2), "Error: pytest is not installed"])
    def test_a_run_that_ran_nothing_or_cannot_be_read_is_not_listed(self, text):
        assert "Verification runs" not in self._record(text)


class TestTheLedger:
    def _ok(self, text):
        from wisp.core.reasoning.ledger import Ledger, ObservedEvent

        return Ledger().observe(ObservedEvent(type="tool_result", name="run_tests", result_text=text, source="test")).ok

    def test_a_red_run_that_prints_the_vacuous_marker_is_red(self):
        assert self._ok(_summary(passed=2, failed=1, tail=FORGED_TAIL)) is False

    @pytest.mark.parametrize("kw,expected", [(dict(passed=3), True), (dict(passed=2, failed=1), False), (dict(), None), (dict(skipped=3), None)])
    def test_the_harness_summary(self, kw, expected):
        assert self._ok(_summary(**kw)) is expected

    def test_unreadable_is_none(self):
        assert self._ok("Error: pytest is not installed") is None
