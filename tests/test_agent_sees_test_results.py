"""The agent must be able to SEE why a test failed.

Found by driving a real engine turn and printing what the provider is sent (2026-10-10):

* a shell command whose output passes 50,000 characters kept the START and dropped the END, and a test run puts its verdict last (`FAILED ...`, the
  short summary), so the one thing the model needed was the thing that was cut; the PTY capture loop even stopped recording after about 115 KB;
* the `run_tests` tool, without the optional `pytest-json-report` plugin, returned `test_x.py::test_add — failed` and nothing else: no assertion, no line.

Each test below drives the production path (a real subprocess, a real pytest, a real engine turn), not a fake.
"""

from __future__ import annotations

import pytest

from tests.reliability.test_verification_evidence_adapter import _content_round, _Provider, _run_turn, _tool_round
from wisp.infra.security import PermissionMode
from wisp.sandbox.router import PtySandbox
from wisp.tools._utils import _MAX_BASH_OUTPUT, truncate_output

MARK = "\n... [output truncated]"


def numbered(n: int) -> str:
    return "\n".join(f"line {i}" for i in range(n))


class TestTruncateOutput:
    def test_short_text_is_untouched(self):
        assert truncate_output("abc", 100) == "abc"
        assert truncate_output("x" * 100, 100) == "x" * 100

    def test_both_ends_survive_and_the_total_stays_within_the_limit(self):
        text = numbered(20000) + "\nFAILED tests/test_core.py::test_answer - AssertionError"
        out = truncate_output(text, 5000)
        assert len(out) <= 5000
        assert out.startswith("line 0\n") and out.endswith("FAILED tests/test_core.py::test_answer - AssertionError")

    def test_it_says_that_it_cut_and_how_much_with_the_marker_other_code_looks_for(self):
        out = truncate_output("a" * 10000, 1000)
        assert MARK in out and "omitted" in out

    def test_it_cuts_on_line_boundaries_when_it_can(self):
        out = truncate_output(numbered(5000), 2000)
        head, _, tail = out.partition(MARK)
        assert all(ln.startswith("line ") and ln[5:].isdigit() for ln in head.splitlines())
        assert all(ln.startswith("line ") and ln[5:].isdigit() for ln in tail.splitlines()[2:])

    def test_the_tail_gets_most_of_the_budget(self):
        out = truncate_output(numbered(20000), 4000)
        head, _, tail = out.partition(MARK)
        assert len(tail) > 2 * len(head)

    def test_a_text_with_no_newlines_still_keeps_both_ends(self):
        out = truncate_output("S" + "x" * 50000 + "E", 1000)
        assert out.startswith("S") and out.endswith("E") and len(out) <= 1000


class TestTheBashFormatterKeepsTheEnd:
    """`_format_bash_output` is also reached without the PTY (Docker, the host, background jobs), so it needs its own witness."""

    def test_a_huge_failing_stdout_keeps_the_exit_code_on_top_and_the_verdict_at_the_bottom(self):
        from wisp.tools.bash import _format_bash_output

        out = _format_bash_output(1, numbered(60000) + "\nFAILED test_core.py::test_answer - AssertionError", "")
        assert out.startswith("[exit code: 1]\n") and out.endswith("FAILED test_core.py::test_answer - AssertionError")
        assert MARK in out and len(out) <= _MAX_BASH_OUTPUT


class TestThePtyCaptureKeepsTheEnd:
    @pytest.mark.asyncio
    async def test_output_past_the_capture_cap_keeps_its_last_line_and_the_exit_status(self, tmp_path):
        rc, out, _err = await PtySandbox(str(tmp_path)).run("seq 1 400000; echo FAILED-AT-THE-VERY-END; exit 5", timeout=60)
        assert rc == 5
        assert out.rstrip().endswith("FAILED-AT-THE-VERY-END")
        assert out.startswith("1\n") and MARK in out and len(out) <= _MAX_BASH_OUTPUT

    @pytest.mark.asyncio
    async def test_output_whose_start_and_end_overlap_in_the_capture_keeps_the_last_line(self, tmp_path):
        """About 138 KB: more than the recorded head (~115 KB) and less than head plus tail, so the two buffers overlap."""
        rc, out, _err = await PtySandbox(str(tmp_path)).run("seq 1 25000", timeout=30)
        nums = [int(x) for x in out.replace(MARK, "\n").split() if x.isdigit()]
        assert rc == 0 and nums[-1] == 25000 and nums == sorted(set(nums))

    @pytest.mark.asyncio
    async def test_output_just_over_the_cap_has_no_gap_or_repeat(self, tmp_path):
        rc, out, _err = await PtySandbox(str(tmp_path)).run("seq 1 12000", timeout=30)
        assert rc == 0
        nums = [int(x) for x in out.replace(MARK, "\n").split() if x.isdigit()]
        assert nums == sorted(set(nums)) and nums[-1] == 12000


class TestRunTestsShowsTheFailure:
    FAILING = (
        "def test_add():\n    assert 1 + 1 == 3, 'math is broken'\n\n"
        "def test_ok():\n    assert True\n\n"
        "class TestK:\n    def test_m(self):\n        x = {'a': 1}\n        assert x['b'] == 2\n"
    )

    def project(self, tmp_path, addopts=None):
        (tmp_path / "test_x.py").write_text(self.FAILING)
        if addopts:
            (tmp_path / "pytest.ini").write_text(f"[pytest]\naddopts = {addopts}\n")
        return tmp_path

    @pytest.fixture(autouse=True)
    def no_json_plugin(self, monkeypatch):
        """The plugin is optional and absent in most projects: that is the case that was blind."""
        monkeypatch.setattr("wisp.test_runner._has_plugin", lambda name: False)

    @pytest.mark.parametrize("addopts", [None, "-q", "-q -q"], ids=["plain", "addopts-q (cancels -v)", "addopts-qq"])
    def test_the_model_is_told_which_assertion_failed_and_where(self, tmp_path, addopts):
        from wisp.test_runner import run_tests

        ws = self.project(tmp_path, addopts)
        summary = run_tests([ws / "test_x.py"], workspace=ws, timeout=60)
        text = summary.format_for_llm()
        assert (summary.failed, summary.passed) == (2, 1), text
        assert "math is broken" in text and "test_x.py:2" in text, text
        assert "KeyError: 'b'" in text and "TestK::test_m" in text, text

    def test_the_counts_are_right_when_pytest_prints_no_equals_banner(self, tmp_path):
        from wisp.test_runner import run_tests

        ws = self.project(tmp_path, "-q")
        summary = run_tests([ws / "test_x.py"], workspace=ws, timeout=60)
        assert (summary.total, summary.failed) == (3, 2)

    def test_a_collection_error_shows_its_message(self, tmp_path):
        from wisp.test_runner import run_tests

        (tmp_path / "test_broken.py").write_text("import module_that_does_not_exist_anywhere\n\ndef test_a():\n    pass\n")
        summary = run_tests([tmp_path / "test_broken.py"], workspace=tmp_path, timeout=60)
        text = summary.format_for_llm()
        assert summary.errors >= 1 and "module_that_does_not_exist_anywhere" in text, text

    def test_when_nothing_could_be_parsed_the_end_of_the_output_is_still_shown(self, tmp_path):
        from wisp.test_runner import UnitTestRunSummary

        s = UnitTestRunSummary(total=3, passed=1, failed=2, stdout="x" * 10000 + "\nTHE-REASON-AT-THE-END", stderr="")
        assert "THE-REASON-AT-THE-END" in s.format_for_llm()

    def test_a_failure_with_only_the_short_summary_line_still_shows_its_message(self):
        from wisp.test_runner import UnitTestRunSummary, _parse_pytest_output

        s = UnitTestRunSummary()
        _parse_pytest_output("FAILED tests/test_a.py::test_x - AssertionError: 41 != 42\n1 failed in 0.1s\n", "", s)
        assert "41 != 42" in s.format_for_llm() and s.failed == 1

    def test_counts_come_from_the_per_test_lines_when_there_is_no_summary_line(self):
        from wisp.test_runner import UnitTestRunSummary, _parse_pytest_output

        s = UnitTestRunSummary()
        _parse_pytest_output("t.py::test_a PASSED\nt.py::test_b PASSED\nt.py::test_c FAILED\n", "", s)
        assert (s.total, s.passed, s.failed) == (3, 2, 1)

    def test_passing_tests_are_listed_too_in_a_verbose_run(self, tmp_path):
        from wisp.test_runner import run_tests

        ws = self.project(tmp_path)
        ids = {r.test_id: r.outcome for r in run_tests([ws / "test_x.py"], workspace=ws, timeout=60).results}
        assert ids.get("test_x.py::test_ok") == "passed" and ids.get("test_x.py::test_add") == "failed"

    def test_a_green_run_stays_short(self, tmp_path):
        from wisp.test_runner import run_tests

        (tmp_path / "test_g.py").write_text("def test_a():\n    assert True\n")
        text = run_tests([tmp_path / "test_g.py"], workspace=tmp_path, timeout=60).format_for_llm()
        assert "1/1 passed" in text and "### Failures" not in text and len(text) < 300


class TestThroughARealEngineTurn:
    """What the provider is actually sent after the tool ran. The model is the reader; this is the only place that proves it can read the answer."""

    @pytest.fixture
    def requests(self, monkeypatch):
        sent: list[list[dict]] = []
        orig = _Provider.generate_stream_events

        def spy(self, system_prompt, messages, tools=None):
            sent.append([dict(m) for m in messages])
            yield from orig(self, system_prompt, messages, tools)

        monkeypatch.setattr(_Provider, "generate_stream_events", spy)
        monkeypatch.setattr("wisp.config.load_config", lambda: {})
        return sent

    @staticmethod
    def tool_text(sent) -> str:
        return "\n".join(str(m.get("content", "")) for m in sent[1] if m.get("role") == "tool")

    def test_a_huge_failing_run_still_shows_the_model_the_failure_at_its_end(self, tmp_path, requests):
        cmd = "seq 1 300000; echo 'FAILED tests/test_core.py::test_answer - AssertionError: 41 != 42'; exit 1"
        _run_turn(tmp_path, [_tool_round("run_bash", {"command": cmd}, "c0"), _content_round("ok")], mode=PermissionMode.ASK_ALL, approve_calls=True, sid="seesA")
        text = self.tool_text(requests)
        assert "[exit code: 1]" in text and "test_answer - AssertionError: 41 != 42" in text

    def test_run_tests_shows_the_model_the_assertion(self, tmp_path, requests, monkeypatch):
        monkeypatch.setattr("wisp.test_runner._has_plugin", lambda name: False)
        (tmp_path / "test_x.py").write_text("def test_add():\n    assert 1 + 1 == 3, 'math is broken'\n")
        _run_turn(tmp_path, [_tool_round("run_tests", {"path": "test_x.py"}, "c0"), _content_round("ok")], mode=PermissionMode.ASK_ALL, approve_calls=True, sid="seesB")
        text = self.tool_text(requests)
        assert "math is broken" in text and "test_x.py:2" in text
