"""`--run-tests`: the harness runs the tests the change affects and a failure is established evidence, so it can block. Local checkouts only, never a pull request."""

from __future__ import annotations

import asyncio
import io
import subprocess
from types import SimpleNamespace

import pytest

from tests.review.helpers import added_file
from wisp.review import checks, cli as C, engine as E
from wisp.review.checks import CheckContext
from wisp.review.rules import Rules
from wisp.review.types import Severity


def summary(*, results=(), stdout="", **kw):
    base = dict(total=len(results), passed=sum(r.outcome == "passed" for r in results), failed=sum(r.outcome == "failed" for r in results), errors=sum(r.outcome == "error" for r in results), skipped=0)
    base.update(kw)
    return SimpleNamespace(results=list(results), stdout=stdout, **base)


def res(test_id, outcome, traceback=""):
    return SimpleNamespace(test_id=test_id, outcome=outcome, traceback=traceback)


def run(files, outcome):
    ctx = CheckContext(run_tests=lambda changed: outcome)
    return checks.check_affected_tests(files, ctx), ctx


FILES = added_file("src/calc.py", ["def add(a, b):", "    return a - b"])


def test_a_failing_test_blocks_and_names_itself():
    findings, _ = run(FILES, summary(results=[res("tests/test_calc.py::test_add", "failed", "E   assert 1 == 3")]))
    (finding,) = findings
    assert (finding.rule, finding.severity) == ("tests-failing", Severity.BLOCK) and "test_add" in finding.message and "run by the harness" in finding.evidence
    assert "assert 1 == 3" in finding.quote


def test_an_erroring_test_blocks_too_and_the_list_is_bounded():
    findings, _ = run(FILES, summary(results=[res(f"t.py::test_{i}", "error") for i in range(40)]))
    assert len(findings) == checks.MAX_TEST_FINDINGS and all(f.severity is Severity.BLOCK for f in findings)


def test_counts_without_per_test_results_still_block():
    findings, _ = run(FILES, summary(failed=2, total=2))
    assert [f.severity for f in findings] == [Severity.BLOCK] and "2" in findings[0].message


def test_passing_tests_are_a_note_with_the_count():
    findings, _ = run(FILES, summary(results=[res("t.py::a", "passed"), res("t.py::b", "passed")]))
    assert [(f.rule, f.severity) for f in findings] == [("tests-passed", Severity.INFO)] and "2" in findings[0].message


def test_no_affected_tests_is_a_note_not_a_pass():
    findings, _ = run(FILES, summary())
    assert [f.rule for f in findings] == ["tests-none"]


def test_a_lookup_that_was_skipped_is_a_gap():
    findings, ctx = run(FILES, summary(stdout="Affected-test lookup skipped: the workspace is too large"))
    assert findings == [] and ctx.gaps and "too large" in ctx.gaps[0]


def test_a_traceback_with_a_secret_is_scrubbed():
    from tests.review.helpers import fake_token

    token = fake_token("github")
    findings, _ = run(FILES, summary(results=[res("t.py::a", "failed", f"E   KeyError: '{token}'")]))
    assert token not in repr(findings)


def test_the_check_does_nothing_unless_asked():
    assert checks.check_affected_tests(FILES, CheckContext()) == []


def test_deleted_files_are_not_handed_to_the_test_runner():
    seen = []
    from tests.review.helpers import files_of, make_diff

    ctx = CheckContext(run_tests=lambda changed: (seen.append(list(changed)), summary())[1])
    checks.check_affected_tests(files_of(make_diff("a.py", ["x"], None), make_diff("b.py", None, ["y"])), ctx)
    assert seen == [["b.py"]]


def test_the_default_check_set_does_not_include_it_and_the_engine_adds_it_only_when_asked():
    assert checks.check_affected_tests not in checks.ALL_CHECKS
    off = asyncio.run(E.review(FILES, 0, CheckContext(symbol_tested=lambda n: True, read_post_image=lambda p: ""), Rules(), E.ReviewOptions(use_model=False), None))
    on_ctx = CheckContext(symbol_tested=lambda n: True, read_post_image=lambda p: "", run_tests=lambda c: summary(results=[res("t.py::a", "failed")]))
    on = asyncio.run(E.review(FILES, 0, on_ctx, Rules(), E.ReviewOptions(use_model=False), None))
    assert "check_affected_tests" not in off.checks_run and "check_affected_tests" in on.checks_run and on.verdict == "blocked"


# ── through the real CLI, a real repository and a real pytest ────────────────

@pytest.fixture
def project(tmp_path):
    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    return tmp_path


def review(project, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = C.run_review(list(argv), workspace=str(project), out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_a_change_that_breaks_a_test_is_blocked_by_the_tests_not_by_an_opinion(project):
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    code, out, err = review(project, "--no-model", "--run-tests")
    assert code == 1 and "BLOCKED" in out and "test_add" in out, (out, err)


def test_a_change_that_keeps_the_tests_green_says_so(project):
    (project / "calc.py").write_text("def add(a, b):\n    return b + a\n")
    code, out, err = review(project, "--no-model", "--run-tests")
    assert code == 0 and "tests-passed" in out, (out, err)


def test_without_the_flag_nothing_is_executed(project):
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    code, out, _ = review(project, "--no-model")
    assert code == 0 and "tests-failing" not in out
