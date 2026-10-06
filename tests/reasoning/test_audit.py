"""The verdict matrix: a claim is SUPPORTED only by a fresh, successful observation; stale, failed or absent evidence is never support."""

from __future__ import annotations

import pytest

from tests.reasoning.conftest import FAIL, ev
from wisp.core.reasoning.claims import ClaimKind, Verdict, audit, extract

V = Verdict


def verdicts(text, ledger):
    return [a.verdict for a in audit(text, ledger)]


def run(ledger, command, text="", failed=False):
    ledger.observe(ev("run_bash", "tool", command=command, text=text, failed=failed))


def edit(ledger, path, **kw):
    ledger.observe(ev("edit_file", "tool", paths=(path,), **kw))


class TestTestsPass:
    def test_unsupported_with_no_run(self, ledger):
        assert verdicts("All tests pass.", ledger) == [V.UNSUPPORTED]

    def test_supported_after_a_passing_run(self, ledger):
        run(ledger, "pytest -q", "5 passed in 0.1s")
        assert verdicts("All tests pass.", ledger) == [V.SUPPORTED]

    def test_contradicted_by_a_failing_run(self, ledger):
        run(ledger, "pytest -q", FAIL, failed=True)
        assert verdicts("All tests pass.", ledger) == [V.CONTRADICTED]

    def test_a_later_pass_replaces_an_earlier_failure(self, ledger):
        run(ledger, "pytest -q", FAIL, failed=True)
        run(ledger, "pytest -q", "5 passed")
        assert verdicts("All tests pass.", ledger) == [V.SUPPORTED]

    def test_an_edit_after_the_run_makes_it_stale(self, ledger):
        run(ledger, "pytest -q", "5 passed")
        edit(ledger, "src/a.py")
        assert verdicts("All tests pass.", ledger) == [V.UNSUPPORTED]

    def test_a_run_after_the_edit_is_fresh(self, ledger):
        edit(ledger, "src/a.py")
        run(ledger, "pytest -q", "5 passed")
        assert verdicts("All tests pass.", ledger) == [V.SUPPORTED]

    def test_a_non_test_command_is_not_test_evidence(self, ledger):
        run(ledger, "echo all tests pass", "all tests pass")
        assert verdicts("All tests pass.", ledger) == [V.UNSUPPORTED]

    def test_a_denied_run_is_not_evidence(self, ledger):
        ledger.observe(ev("run_bash", "gate", command="pytest -q", denied=True))
        assert verdicts("All tests pass.", ledger) == [V.UNSUPPORTED]

    def test_a_build_is_not_test_evidence(self, ledger):
        run(ledger, "python3 -m compileall -q .", "")
        assert verdicts("All tests pass.", ledger) == [V.UNSUPPORTED]


class TestBuildAndLint:
    def test_lint_is_judged_by_a_lint_run_only(self, ledger):
        run(ledger, "pytest -q", "5 passed")
        assert verdicts("Lint passes.", ledger) == [V.UNSUPPORTED]

    def test_lint_supported_by_ruff(self, ledger):
        run(ledger, "ruff check .", "All checks passed!")
        assert verdicts("Lint passes.", ledger) == [V.SUPPORTED]

    def test_lint_contradicted_by_a_failing_ruff(self, ledger):
        run(ledger, "ruff check .", "Found 3 errors.", failed=True)
        assert verdicts("Lint passes.", ledger) == [V.CONTRADICTED]

    def test_build_supported_by_a_real_compile(self, ledger):
        run(ledger, "python3 -m compileall -q .", "")
        assert verdicts("The build succeeds.", ledger) == [V.SUPPORTED]

    def test_each_claim_is_judged_on_its_own_kind(self, ledger):
        run(ledger, "pytest -q", "5 passed")
        got = dict(zip([a.claim.kind for a in audit("All tests pass. Lint passes.", ledger)], verdicts("All tests pass. Lint passes.", ledger)))
        assert got == {ClaimKind.TESTS_PASS: V.SUPPORTED, ClaimKind.LINT_OK: V.UNSUPPORTED}


class TestFixed:
    def test_unsupported_without_any_edit(self, ledger):
        run(ledger, "pytest -q", "5 passed")
        assert verdicts("I fixed the bug.", ledger) == [V.UNSUPPORTED]

    def test_unsupported_with_an_edit_and_no_verification(self, ledger):
        edit(ledger, "src/a.py")
        assert verdicts("I fixed the bug.", ledger) == [V.UNSUPPORTED]

    def test_supported_with_an_edit_then_a_passing_run(self, ledger):
        edit(ledger, "src/a.py")
        run(ledger, "pytest -q", "5 passed")
        assert verdicts("I fixed the bug.", ledger) == [V.SUPPORTED]

    def test_contradicted_when_the_run_after_the_edit_fails(self, ledger):
        edit(ledger, "src/a.py")
        run(ledger, "pytest -q", FAIL, failed=True)
        assert verdicts("I fixed the bug.", ledger) == [V.CONTRADICTED]

    def test_a_failed_edit_is_not_an_edit(self, ledger):
        edit(ledger, "src/a.py", failed=True)
        run(ledger, "pytest -q", "5 passed")
        assert verdicts("I fixed the bug.", ledger) == [V.UNSUPPORTED]


    def test_a_lint_pass_alone_does_not_support_a_fix(self, ledger):
        edit(ledger, "src/a.py")
        run(ledger, "ruff check .", "All checks passed!")
        assert verdicts("I fixed the bug.", ledger) == [V.UNSUPPORTED]


class TestFileChanged:
    def test_unsupported_with_no_edit(self, ledger):
        assert verdicts("I updated `src/app.py`.", ledger) == [V.UNSUPPORTED]

    def test_supported_by_an_edit_to_that_file(self, ledger):
        edit(ledger, "/ws/src/app.py")
        assert verdicts("I updated `src/app.py`.", ledger) == [V.SUPPORTED]

    def test_an_edit_to_another_file_does_not_support_it(self, ledger):
        edit(ledger, "/ws/src/other.py")
        assert verdicts("I updated `src/app.py`.", ledger) == [V.UNSUPPORTED]

    def test_a_failed_attempt_contradicts_it(self, ledger):
        edit(ledger, "/ws/src/app.py", failed=True)
        assert verdicts("I updated `src/app.py`.", ledger) == [V.CONTRADICTED]

    def test_a_gate_denied_attempt_contradicts_it(self, ledger):
        ledger.observe(ev("write_file", "gate", paths=("/ws/src/app.py",), denied=True))
        assert verdicts("I updated `src/app.py`.", ledger) == [V.CONTRADICTED]


class TestCommandRan:
    def test_unsupported_when_never_run(self, ledger):
        assert verdicts("I ran `make test`.", ledger) == [V.UNSUPPORTED]

    def test_supported_when_observed(self, ledger):
        run(ledger, "make test", "ok")
        assert verdicts("I ran `make test`.", ledger) == [V.SUPPORTED]

    def test_whitespace_is_normalised(self, ledger):
        run(ledger, "make   test", "ok")
        assert verdicts("I ran `make test`.", ledger) == [V.SUPPORTED]

    def test_a_different_command_does_not_support_it(self, ledger):
        run(ledger, "make lint", "ok")
        assert verdicts("I ran `make test`.", ledger) == [V.UNSUPPORTED]


class TestAbstention:
    def test_a_message_with_no_claims_yields_no_audits(self, ledger):
        assert audit("Here is what I found in the parser.", ledger) == ()

    def test_a_disclaimed_message_yields_no_verification_audit(self, ledger):
        assert audit("All tests pass, but I did not run them.", ledger) == ()

    def test_audit_is_deterministic(self, ledger):
        run(ledger, "pytest -q", "5 passed")
        t = "I fixed the bug. All tests pass. I ran `pytest -q`."
        assert audit(t, ledger) == audit(t, ledger)

    def test_audit_does_not_mutate_the_ledger(self, ledger):
        run(ledger, "pytest -q", "5 passed")
        before = ledger.snapshot_hash()
        audit("All tests pass. I fixed the bug.", ledger)
        assert ledger.snapshot_hash() == before

    @pytest.mark.parametrize("bad", ["", None, 3])
    def test_audit_tolerates_junk(self, ledger, bad):
        assert audit(bad, ledger) == ()  # type: ignore[arg-type]
