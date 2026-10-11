"""RC7: precision over recall. A recognised claim is found; everything that merely looks like one is not.

The corpora are synthetic (written from patterns models use). The false-positive rate on REAL transcripts is not measured here; that is
the phase-P2 baseline, and it needs a corpus of real sessions."""

from __future__ import annotations

import pytest

from wisp.core.reasoning.claims import ClaimKind, extract

K = ClaimKind

CLAIMS = [
    ("All tests pass.", {K.TESTS_PASS}), ("All the tests now pass.", {K.TESTS_PASS}), ("Every unit test passes.", {K.TESTS_PASS}), ("All 12 tests passed.", {K.TESTS_PASS}),
    ("The tests pass.", {K.TESTS_PASS}), ("Tests are now passing.", {K.TESTS_PASS}), ("The test suite is green.", {K.TESTS_PASS}), ("The suite passes.", {K.TESTS_PASS}),
    ("12/12 tests passed.", {K.TESTS_PASS}), ("pytest passes now.", {K.TESTS_PASS}), ("All existing tests are passing.", {K.TESTS_PASS}), ("All tests are green.", {K.TESTS_PASS}),
    ("The build succeeds.", {K.BUILD_OK}), ("The build now passes.", {K.BUILD_OK}), ("It builds successfully.", {K.BUILD_OK}), ("The project compiles cleanly.", {K.BUILD_OK}),
    ("Type checking passes.", {K.BUILD_OK}), ("mypy passes.", {K.BUILD_OK}), ("tsc reports no errors.", {K.BUILD_OK}), ("The build is green.", {K.BUILD_OK}),
    ("Lint passes.", {K.LINT_OK}), ("ruff reports no issues.", {K.LINT_OK}), ("No lint errors.", {K.LINT_OK}), ("eslint is clean.", {K.LINT_OK}),
    ("I fixed the bug.", {K.FIXED}), ("I've fixed the issue.", {K.FIXED}), ("I have resolved the problem.", {K.FIXED}), ("The bug is now fixed.", {K.FIXED}),
    ("The issue has been resolved.", {K.FIXED}), ("I patched the crash.", {K.FIXED}), ("Fixed the failure.", {K.FIXED}) if False else ("I fixed it.", {K.FIXED}),
    ("I updated `src/app.py`.", {K.FILE_CHANGED}), ("I've modified config.yaml.", {K.FILE_CHANGED}), ("I created tests/test_new.py.", {K.FILE_CHANGED}), ("I added the file utils/helpers.py.", {K.FILE_CHANGED}),
    ("I edited `pyproject.toml`.", {K.FILE_CHANGED}), ("I renamed old_name.py.", {K.FILE_CHANGED}), ("I refactored src/core/engine.py.", {K.FILE_CHANGED}),
    ("I ran `pytest -q`.", {K.COMMAND_RAN}), ("I have run `make test`.", {K.COMMAND_RAN}), ("I executed `npm run build`.", {K.COMMAND_RAN}), ("I then ran `git status`.", {K.COMMAND_RAN}),
    ("I fixed the bug and all tests pass.", {K.FIXED, K.TESTS_PASS}), ("I updated `a.py`. All tests pass.", {K.FILE_CHANGED, K.TESTS_PASS}),
    ("I ran `pytest` and all 5 tests passed.", {K.COMMAND_RAN, K.TESTS_PASS}), ("The build succeeds and lint passes.", {K.BUILD_OK, K.LINT_OK}),
    ("Done. I fixed the bug in the parser. The test suite passes.", {K.FIXED, K.TESTS_PASS}),
    ("- All tests pass\n- The build succeeds", {K.TESTS_PASS, K.BUILD_OK}),
    ("## Summary\nI updated `README.md`.\nAll tests pass.", {K.FILE_CHANGED, K.TESTS_PASS}),
]

NOT_CLAIMS = [
    # questions, plans, conditions, purposes
    "Do all tests pass?", "Should the tests pass after this change?", "I'll run the tests next.", "I will fix the bug in the next step.", "Let me check whether the tests pass.",
    "Let's make sure the build succeeds.", "I'm going to update `src/app.py`.", "Next I need to fix the failing test.", "I want to verify that all tests pass.",
    "If the tests pass, we can merge.", "Once the build succeeds we can deploy.", "The tests should pass now.", "The build would succeed with this change.", "This might fix the bug.",
    "This could resolve the issue.", "You can run `pytest` to check that all tests pass.", "To verify, run the tests and confirm they pass.", "To make sure the build succeeds, run `make`.",
    "Run `pytest -q` and check the output.", "You should see all tests pass.", "Whether all tests pass depends on the environment.", "Unless the build succeeds we cannot ship.",
    "I need to fix the bug before the tests can pass.", "The goal is for all tests to pass.", "We want the build to succeed.", "I'm trying to fix the issue.",
    # negations, failures, exceptions
    "Tests still fail.", "All tests do not pass.", "The tests don't pass yet.", "Not all tests pass.", "The build fails.", "The build did not succeed.", "Some tests failed.", "There are test failures.",
    "The error persists.", "The bug is not fixed.", "I couldn't fix the issue.", "I was unable to resolve the problem.", "I haven't fixed it yet.", "All tests pass except test_foo.",
    "All tests pass but the build fails.", "The suite passes, however lint reports errors.", "It compiles, though there are warnings and errors.", "The failure is still there.",
    "No tests were run.", "Tests are broken.", "This is a regression.", "I never ran the tests.", "The type check does not pass.",
    # disclaimers and hedges
    "I did not run the tests.", "I haven't run the build.", "This is untested.", "The change is unverified.", "I could not verify the fix.", "I was unable to run the test suite.",
    # past events by someone/something else, or descriptions
    "The old version had a bug that crashed the app.", "The previous build succeeded, but this one needs work.", "Tests passed on CI last week.", "The user reported that tests pass locally.",
    "The README says all tests pass.", "A test named test_all_tests_pass exists.", "The function check_build_ok returns True.", "This test suite covers the parser.", "The build step runs in CI.",
    "Fixing the bug requires changing the parser.", "To fix the issue, edit the config.", "The fix is straightforward.", "A fix is in progress.", "This fixes nothing on its own.",
    # file and command mentions that are not first-person completed actions
    "The file `src/app.py` contains the entry point.", "You should update `config.yaml`.", "Please create tests/test_new.py.", "We could add the file utils/helpers.py.", "src/app.py was already updated.",
    "Run `pytest` to see.", "You can execute `make test` locally.", "The command `npm run build` is defined in package.json.",
    # prose about testing in general
    "Testing is important for quality.", "Passing a flag to pytest changes its output.", "The build system is Bazel.", "Lint rules are configured in ruff.toml.",
    "Each test should be independent.", "We use pytest for the suite.", "The word fixed appears in the docs.", "Mypy is the type checker used here.",
    "The CI pipeline reports that the build succeeds.", "According to the docs the build succeeds.", "He said he fixed the bug.", "Yesterday the build succeeded.",
    "The build on CI succeeds.", "Previously all tests passed.", "The earlier fix resolved the bug.", "The changelog mentions that the issue was resolved.",
    "All tests pass, which should be fine.", "All tests pass, which would be nice.", "If all tests pass we are done.", "Unless all tests pass this stays open.", "The tests pass but the docs are stale.", "The tests pass, however the docs are stale.",
    "There are no errors but the tests fail.", "No lint errors, but the build fails.", "The upstream suite passes.", "Was the bug fixed?", "A passing test would show the fix.",
    # code fences are not prose
    "```\nAll tests pass.\nThe build succeeds.\n```", "Output:\n```\n12 passed in 0.5s\n```",
]


@pytest.mark.parametrize("text,expected", CLAIMS)
def test_plain_affirmative_claims_are_found(text, expected):
    got = {c.kind for c in extract(text).claims}
    assert got == expected, (text, got)


@pytest.mark.parametrize("text", NOT_CLAIMS)
def test_look_alikes_are_never_claims(text):
    found = extract(text).claims
    assert not found, (text, [(c.kind.value, c.text) for c in found])


class TestSubjects:
    def test_a_file_claim_names_its_file(self):
        (c,) = extract("I updated `src/app.py`.").claims
        assert c.kind is K.FILE_CHANGED and c.subject == "src/app.py"

    def test_a_command_claim_names_its_command(self):
        (c,) = extract("I ran `pytest -q tests/`.").claims
        assert c.kind is K.COMMAND_RAN and c.subject == "pytest -q tests/"

    def test_a_claim_records_where_it_is(self):
        text = "Done. All tests pass."
        (c,) = extract(text).claims
        assert text[c.start:c.end].lower().startswith("all tests pass")


class TestDisclaimers:
    def test_a_disclaimer_drops_the_verification_claims_in_the_same_message(self):
        e = extract("I fixed the bug and the tests pass, but I did not run the full suite.")
        assert e.claims == ()
        assert {K.TESTS_PASS, K.FIXED} <= e.disclaimed

    def test_a_disclaimer_in_its_own_sentence_drops_the_claim_in_another(self):
        assert extract("All tests pass. Note: this is untested.").claims == ()

    def test_a_disclaimer_does_not_drop_file_or_command_claims(self):
        e = extract("I updated `a.py`. This is untested.")
        assert [c.kind for c in e.claims] == [K.FILE_CHANGED]

    def test_a_disclaimer_inside_a_code_fence_does_not_count(self):
        e = extract("All tests pass.\n```\nnote: I did not run it\n```")
        assert [c.kind for c in e.claims] == [K.TESTS_PASS]


class TestRobustness:
    @pytest.mark.parametrize("bad", ["", "   ", "\n\n", None, 5, [], "x" * 100_000])
    def test_extract_never_raises(self, bad):
        e = extract(bad)  # type: ignore[arg-type]
        assert e.claims == () or isinstance(e.claims, tuple)

    def test_extract_is_deterministic(self):
        t = "I fixed the bug in `a.py`. All tests pass. The build succeeds."
        assert extract(t) == extract(t)

    def test_the_corpora_are_large_enough_to_mean_something(self):
        assert len(CLAIMS) >= 45 and len(NOT_CLAIMS) >= 100
