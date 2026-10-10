"""The deterministic checks: real findings on real diffs, never a secret echoed back, nothing flagged that the change did not do."""

from __future__ import annotations

import pytest

from tests.review.helpers import added_file, edited_file, fake_token, files_of, make_diff
from wisp.review import checks
from wisp.review.checks import CheckContext
from wisp.review.types import Severity


def run(check, files, **ctx):
    return check(files, CheckContext(**ctx))


def rules_of(findings):
    return [f.rule for f in findings]


class TestSecrets:
    def test_a_key_added_to_a_file_blocks_and_is_located_at_the_real_line(self):
        files = added_file("config.py", ["import os", "", f'KEY = "{fake_token("openrouter")}"', "x = 1"])
        (finding,) = run(checks.check_secrets, files)
        assert (finding.rule, finding.severity, finding.file, finding.line) == ("secret", Severity.BLOCK, "config.py", 3)

    def test_the_finding_never_contains_the_secret_in_any_field(self):
        token = fake_token("openrouter")
        (finding,) = run(checks.check_secrets, added_file("c.py", [f'KEY = "{token}"']))
        assert token not in repr(finding) and token[:12] not in repr(finding)
        assert "REDACTED" in finding.quote

    @pytest.mark.parametrize("kind", ["openrouter", "github", "stripe"])
    @pytest.mark.parametrize("shape", ["token: {t}", "{t}", 'x = "{t}"', "Authorization: Bearer {t}"])
    def test_vendor_tokens_block_whatever_words_surround_them(self, kind, shape):
        """The gate merges overlapping spans, so `token: <vendor token>` comes back as a generic assignment or as entropy; the review must still block."""
        (finding,) = run(checks.check_secrets, added_file("a.txt", [shape.format(t=fake_token(kind))]))
        assert finding.severity is Severity.BLOCK, finding

    def test_a_secret_that_is_removed_is_not_reported(self):
        files = edited_file("c.py", [f'KEY = "{fake_token("openrouter")}"', "x = 1"], ["x = 1"])
        assert run(checks.check_secrets, files) == []

    def test_a_secret_on_an_unchanged_context_line_is_not_reported(self):
        old = ["a", f'KEY = "{fake_token("openrouter")}"', "b"]
        files = edited_file("c.py", old, ["a", old[1], "b", "c"])
        assert run(checks.check_secrets, files) == []

    def test_a_weak_credential_shape_warns_instead_of_blocking(self):
        (finding,) = run(checks.check_secrets, added_file("app.env", ["PASSWORD = 'hunter22xyz'"]))
        assert finding.severity is Severity.WARN

    def test_a_clean_file_has_no_findings(self):
        assert run(checks.check_secrets, added_file("a.py", ["def f():", "    return 1"])) == []


class TestConflictMarkers:
    def test_a_marker_pair_blocks_at_each_marker(self):
        files = added_file("a.py", ["x = 1", "<<<<<<< HEAD", "y = 2", "=======", "y = 3", ">>>>>>> feature"])
        findings = run(checks.check_conflict_markers, files)
        assert [(f.line, f.severity) for f in findings] == [(2, Severity.BLOCK), (6, Severity.BLOCK)]

    def test_a_markdown_rule_of_equals_is_not_a_marker(self):
        assert run(checks.check_conflict_markers, added_file("README.md", ["Title", "=======", "text"])) == []

    def test_eight_angle_brackets_is_not_a_marker(self):
        assert run(checks.check_conflict_markers, added_file("a.txt", ["<<<<<<<< not git"])) == []


class TestSyntax:
    def test_a_python_file_that_does_not_parse_blocks_with_the_real_line(self):
        files = added_file("a.py", ["def f(:", "    pass"])
        (finding,) = run(checks.check_syntax, files, read_post_image={"a.py": "def f(:\n    pass\n"}.get)
        assert (finding.rule, finding.severity, finding.line) == ("syntax-error", Severity.BLOCK, 1)

    def test_a_valid_python_file_is_clean(self):
        files = added_file("a.py", ["def f():", "    pass"])
        assert run(checks.check_syntax, files, read_post_image={"a.py": "def f():\n    pass\n"}.get) == []

    def test_broken_json_and_toml_block(self):
        files = files_of(make_diff("a.json", None, ['{"a": 1,']), make_diff("b.toml", None, ["a = "]))
        found = run(checks.check_syntax, files, read_post_image={"a.json": '{"a": 1,', "b.toml": "a = "}.get)
        assert sorted(f.file for f in found) == ["a.json", "b.toml"] and all(f.severity is Severity.BLOCK for f in found)

    def test_a_file_whose_content_cannot_be_read_is_not_claimed_to_be_valid_or_broken(self):
        assert run(checks.check_syntax, added_file("a.py", ["def f(:"]), read_post_image=lambda p: None) == []

    def test_deleted_and_binary_files_are_skipped(self):
        files = files_of(make_diff("gone.py", ["x"], None))
        assert run(checks.check_syntax, files, read_post_image=lambda p: "def f(:") == []

    def test_a_huge_file_is_skipped_and_the_skip_is_reported(self):
        big = "x = 1\n" * 300_000
        findings = run(checks.check_syntax, added_file("big.py", ["x = 1"]), read_post_image=lambda p: big)
        assert [f.severity for f in findings] == [Severity.INFO] and "not parsed" in findings[0].message


class TestWeakenedTests:
    def test_an_added_skip_in_a_test_file_warns_at_the_real_line(self):
        old = ["def test_a():", "    assert f() == 1"]
        new = ["import pytest", "@pytest.mark.skip(reason='later')", "def test_a():", "    assert f() == 1"]
        (finding,) = run(checks.check_weakened_tests, edited_file("tests/test_a.py", old, new))
        assert (finding.rule, finding.severity, finding.line) == ("test-weakened", Severity.WARN, 2)

    @pytest.mark.parametrize("line", ["    assert True", "    assert 1", "@pytest.mark.xfail", "    pytest.skip('x')", "it.skip('a', () => {})", "xit('a', () => {})", "    t.Skip(\"later\")"])
    def test_each_weakening_pattern_is_found(self, line):
        path = "tests/test_a.py" if "pytest" in line or "assert" in line else ("a.test.ts" if "it" in line else "a_test.go")
        assert rules_of(run(checks.check_weakened_tests, added_file(path, [line]))) == ["test-weakened"]

    def test_net_removed_assertions_warn_with_the_counts(self):
        old = ["def test_a():", "    assert a() == 1", "    assert b() == 2", "    assert c() == 3"]
        new = ["def test_a():", "    assert a() == 1"]
        (finding,) = run(checks.check_weakened_tests, edited_file("tests/test_a.py", old, new))
        assert finding.rule == "test-weakened" and "2 assertion(s) removed" in finding.message and "0 added" in finding.message

    def test_assertions_replaced_one_for_one_are_not_weakening(self):
        old = ["def test_a():", "    assert a() == 1"]
        new = ["def test_a():", "    assert a() == 2"]
        assert run(checks.check_weakened_tests, edited_file("tests/test_a.py", old, new)) == []

    def test_a_deleted_test_file_warns(self):
        (finding,) = run(checks.check_weakened_tests, files_of(make_diff("tests/test_a.py", ["def test_a():", "    assert 1 == 1"], None)))
        assert finding.rule == "test-removed" and finding.severity is Severity.WARN

    def test_a_renamed_test_file_is_not_a_deletion(self):
        text = "diff --git a/tests/test_a.py b/tests/test_b.py\nsimilarity index 100%\nrename from tests/test_a.py\nrename to tests/test_b.py\n"
        assert run(checks.check_weakened_tests, files_of(text)) == []

    def test_non_test_files_are_never_flagged(self):
        assert run(checks.check_weakened_tests, added_file("src/app.py", ["    assert True", "@pytest.mark.skip"])) == []


class TestMissingTests:
    def test_a_new_public_function_with_no_test_anywhere_warns(self):
        files = added_file("src/app.py", ["def compute(x):", "    return x"])
        (finding,) = run(checks.check_missing_tests, files, symbol_tested=lambda name: False)
        assert (finding.rule, finding.severity, finding.line) == ("no-test", Severity.WARN, 1) and "compute" in finding.message

    def test_a_private_function_is_not_required_to_have_a_test(self):
        assert run(checks.check_missing_tests, added_file("src/app.py", ["def _helper():", "    pass"]), symbol_tested=lambda n: False) == []

    def test_a_symbol_a_test_already_mentions_is_fine(self):
        assert run(checks.check_missing_tests, added_file("src/app.py", ["def compute(x):", "    return x"]), symbol_tested=lambda n: True) == []

    def test_a_change_that_also_touches_a_test_file_is_assumed_tested(self):
        files = files_of(make_diff("src/app.py", None, ["def compute(x):", "    return x"]), make_diff("tests/test_app.py", None, ["def test_compute():", "    pass"]))
        assert run(checks.check_missing_tests, files, symbol_tested=lambda n: False) == []

    @pytest.mark.parametrize("path,line", [("web/api.ts", "export function load() {}"), ("web/api.ts", "export class Client {}"), ("go/x.go", "func Serve() {}"), ("rs/x.rs", "pub fn run() {}")])
    def test_other_languages_public_symbols(self, path, line):
        assert rules_of(run(checks.check_missing_tests, added_file(path, [line]), symbol_tested=lambda n: False)) == ["no-test"]

    def test_test_files_and_docs_are_not_source(self):
        assert run(checks.check_missing_tests, added_file("tests/test_a.py", ["def helper_for_tests():", "    pass"]), symbol_tested=lambda n: False) == []
        assert run(checks.check_missing_tests, added_file("docs/a.md", ["def compute(x):"]), symbol_tested=lambda n: False) == []

    def test_at_most_a_few_symbols_per_file_are_reported(self):
        lines = [f"def f{i}():\n    pass" for i in range(20)]
        findings = run(checks.check_missing_tests, added_file("src/m.py", [x for pair in lines for x in pair.split("\n")]), symbol_tested=lambda n: False)
        assert len(findings) == checks.MAX_MISSING_TEST_FINDINGS_PER_FILE

    def test_without_a_way_to_look_at_the_tests_nothing_is_claimed(self):
        assert run(checks.check_missing_tests, added_file("src/app.py", ["def compute(x):", "    return x"])) == []


class TestConfigAndDependencies:
    @pytest.mark.parametrize("path", [".github/workflows/ci.yml", ".gitlab-ci.yml", ".circleci/config.yml", "Jenkinsfile", ".gitignore"])
    def test_ci_and_project_config_warn(self, path):
        (finding,) = run(checks.check_config_and_dependencies, added_file(path, ["x"]))
        assert (finding.rule, finding.severity) == ("ci-config-changed", Severity.WARN)

    @pytest.mark.parametrize("path", ["package.json", "pyproject.toml", "requirements.txt", "go.sum", "Cargo.lock"])
    def test_manifests_and_lockfiles_are_noted(self, path):
        (finding,) = run(checks.check_config_and_dependencies, added_file(path, ["x"]))
        assert (finding.rule, finding.severity) == ("manifest-changed", Severity.INFO)

    def test_ordinary_source_is_not_flagged(self):
        assert run(checks.check_config_and_dependencies, added_file("src/a.py", ["x"])) == []

    def test_the_two_classes_agree_with_the_gate_the_agent_runs_under(self):
        from wisp.core.gates.deps import is_manifest_path

        for path in (".github/workflows/ci.yml", ".gitignore", "package.json", "requirements/base.txt"):
            assert is_manifest_path(path)
            assert run(checks.check_config_and_dependencies, added_file(path, ["x"])), path


class TestDebugLeftovers:
    @pytest.mark.parametrize("line", ["    breakpoint()", "    import pdb; pdb.set_trace()", "    pdb.set_trace()", "import ipdb", "    debugger;"])
    def test_each_leftover_warns(self, line):
        assert rules_of(run(checks.check_debug_leftovers, added_file("a.py" if "debugger" not in line else "a.js", [line]))) == ["debug-leftover"]

    def test_the_word_in_a_comment_or_string_about_it_is_still_a_leftover_candidate_but_a_removal_is_not(self):
        assert run(checks.check_debug_leftovers, edited_file("a.py", ["    breakpoint()", "x"], ["x"])) == []


class TestSize:
    def test_a_change_over_the_bound_is_noted(self):
        files = [f for i in range(checks.MAX_FILES + 1) for f in added_file(f"f{i}.txt", ["x"])]
        (finding,) = run(checks.check_size, files)
        assert (finding.rule, finding.severity) == ("large-change", Severity.INFO)

    def test_a_change_with_few_files_but_very_many_lines_is_noted_too(self):
        (finding,) = run(checks.check_size, added_file("big.txt", [f"line {i}" for i in range(checks.MAX_CHANGED_LINES + 1)]))
        assert finding.rule == "large-change"

    def test_a_small_change_is_not(self):
        assert run(checks.check_size, added_file("a.txt", ["x"])) == []


def test_the_default_check_set_is_the_documented_one():
    assert [c.__name__ for c in checks.ALL_CHECKS] == [
        "check_secrets", "check_conflict_markers", "check_syntax", "check_weakened_tests", "check_missing_tests",
        "check_config_and_dependencies", "check_debug_leftovers", "check_size",
    ]


def test_run_all_isolates_a_check_that_raises_and_says_so():
    def broken(files, ctx):
        raise RuntimeError("boom")

    findings, errors = checks.run_checks(added_file("a.py", ["x = 1"]), CheckContext(), checks=(broken, checks.check_secrets))
    assert findings == [] and errors == ["broken: RuntimeError: boom"]


def test_unreadable_post_images_are_recorded_as_a_gap_not_as_a_pass():
    ctx = CheckContext(read_post_image=lambda p: None)
    assert checks.check_syntax(added_file("a.py", ["def f(:"]), ctx) == []
    assert ctx.gaps and "could not be read" in ctx.gaps[0]


def test_tests_that_could_not_be_searched_are_a_gap():
    ctx = CheckContext()
    assert checks.check_missing_tests(added_file("src/a.py", ["def f():", "    pass"]), ctx) == []
    assert ctx.gaps and "could not be searched" in ctx.gaps[0]


def test_every_finding_stands_on_a_scrubbed_quote():
    token = fake_token("github")
    findings, errors = checks.run_checks(
        files_of(make_diff("a.py", None, [f"x = '{token}'", "<<<<<<< HEAD"]), make_diff("tests/test_a.py", None, ["assert True"])),
        CheckContext(read_post_image=lambda p: f"x = '{token}'\n", symbol_tested=lambda n: False),
    )
    assert errors == [] and findings
    assert all(token not in repr(f) for f in findings)
