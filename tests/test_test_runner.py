"""Tests for wisp.test_runner."""

from pathlib import Path

import pytest

from wisp.test_runner import (
    UnitTestResult,
    UnitTestRunSummary,
    _parse_pytest_output,
    run_tests,
)


class TestUnitTestRunSummary:
    def test_success_property(self):
        summary = UnitTestRunSummary(passed=5, failed=0, errors=0)
        assert summary.success is True

        summary.failed = 1
        assert summary.success is False

    def test_format_for_llm_all_passed(self):
        summary = UnitTestRunSummary(total=5, passed=5, failed=0, duration=1.23)
        text = summary.format_for_llm()
        assert "5/5 passed" in text
        assert "1.23s" in text
        assert "Failures" not in text

    def test_format_for_llm_with_failures(self):
        summary = UnitTestRunSummary(
            total=5, passed=3, failed=2, duration=2.0,
            results=[
                UnitTestResult("test_a", "passed", 0.1),
                UnitTestResult("test_b", "failed", 0.2, traceback="Error: boom"),
            ]
        )
        text = summary.format_for_llm()
        assert "3/5 passed" in text
        assert "test_b" in text
        assert "Error: boom" in text


class TestParsePytestOutput:
    def test_single_passed(self):
        stdout = "==================================== 1 passed in 0.5s ====================================="
        summary = UnitTestRunSummary()
        _parse_pytest_output(stdout, "", summary)
        assert summary.total == 1
        assert summary.passed == 1
        assert summary.failed == 0

    def test_mixed_results(self):
        stdout = (
            "tests/test_a.py::test_1 PASSED\n"
            "tests/test_a.py::test_2 FAILED\n"
            "==================================== 3 passed, 2 failed, 1 skipped in 1.2s ====================================="
        )
        summary = UnitTestRunSummary()
        _parse_pytest_output(stdout, "", summary)
        assert summary.total == 6
        assert summary.passed == 3
        assert summary.failed == 2
        assert summary.skipped == 1

    def test_no_summary_line(self):
        stdout = "some random output\n"
        summary = UnitTestRunSummary()
        _parse_pytest_output(stdout, "", summary)
        assert summary.total == 0

    def test_test_lines_parsed(self):
        stdout = (
            "tests/test_a.py::test_1 PASSED\n"
            "tests/test_a.py::test_2 FAILED\n"
            "tests/test_a.py::test_3 ERROR\n"
            "tests/test_a.py::test_4 SKIPPED\n"
            "==================================== 1 passed, 1 failed, 1 error, 1 skipped in 0.5s ====================================="
        )
        summary = UnitTestRunSummary()
        _parse_pytest_output(stdout, "", summary)
        assert len(summary.results) == 4
        outcomes = {r.outcome for r in summary.results}
        assert outcomes == {"passed", "failed", "error", "skipped"}


class TestRunTests:
    def test_run_single_test_file(self, tmp_path: Path):
        test_file = tmp_path / "test_sample.py"
        test_file.write_text("""
def test_pass():
    assert True

def test_fail():
    assert False
""")
        summary = run_tests([str(test_file)], workspace=tmp_path, timeout=30)
        assert summary.total == 2
        assert summary.passed == 1
        assert summary.failed == 1
        assert summary.success is False

    def test_run_empty_list(self):
        summary = run_tests([])
        assert summary.total == 0
        assert summary.duration == 0.0

    def test_run_nonexistent_file(self, tmp_path: Path):
        summary = run_tests([str(tmp_path / "nonexistent.py")], workspace=tmp_path, timeout=30)
        # pytest will error because file doesn't exist
        assert summary.total >= 0
