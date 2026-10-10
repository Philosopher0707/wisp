"""PR triage: deterministic, read-only, and fail-closed. The inputs mirror the JSON `gh pr list --json ...` returns; no model is involved and nothing is changed on GitHub."""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import datetime, timedelta, timezone

import pytest

from wisp.review import triage as T

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def check(conclusion="SUCCESS", name="ci", status="COMPLETED"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def pr(number, **kw):
    base = {
        "number": number, "title": f"change {number}", "url": f"https://github.com/o/r/pull/{number}", "author": {"login": "dev"}, "isDraft": False,
        "headRefName": f"branch-{number}", "baseRefName": "main", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "reviewDecision": "",
        "statusCheckRollup": [check()], "files": [{"path": f"src/mod{number}.py", "additions": 10, "deletions": 1}, {"path": f"tests/test_mod{number}.py", "additions": 5, "deletions": 0}],
        "additions": 15, "deletions": 1, "changedFiles": 2, "labels": [], "updatedAt": iso(1), "createdAt": iso(3), "body": "Adds the thing the issue asked for, with tests.",
        "closingIssuesReferences": [{"number": 1}],
    }
    base.update(kw)
    return base


def one(**kw):
    (row,) = T.classify([pr(1, **kw)], now=NOW, default_branch="main")
    return row


def test_a_plain_pr_with_green_ci_tests_and_a_description_is_ready_for_review():
    row = one()
    assert row.outcome == "ready_for_review" and row.flags == [] and row.ci == "passing"


@pytest.mark.parametrize("rollup", [
    [check("FAILURE", "unit")], [check("TIMED_OUT")], [check("CANCELLED")], [check("ACTION_REQUIRED")],
    [{"__typename": "StatusContext", "context": "legacy", "state": "FAILURE"}], [{"__typename": "StatusContext", "context": "legacy", "state": "ERROR"}],
    [check(), check("FAILURE", "lint")],
])
def test_any_failing_check_is_a_ci_failure_naming_the_check(rollup):
    row = one(statusCheckRollup=rollup)
    assert row.outcome == "ci_failure" and row.ci == "failing" and any("failing" in r for r in row.reasons)


def test_the_failing_checks_are_named():
    row = one(statusCheckRollup=[check(), check("FAILURE", "lint")])
    assert any("lint" in r for r in row.reasons)


@pytest.mark.parametrize("rollup", [[check(status="IN_PROGRESS", conclusion=None)], [check(status="QUEUED", conclusion=None)], [{"__typename": "StatusContext", "context": "x", "state": "PENDING"}]])
def test_checks_still_running_mean_waiting_for_ci(rollup):
    assert one(statusCheckRollup=rollup).outcome == "waiting_for_ci"


def test_skipped_and_neutral_checks_are_not_failures():
    assert one(statusCheckRollup=[check("SKIPPED"), check("NEUTRAL"), check()]).ci == "passing"


def test_a_repo_with_no_checks_is_ready_but_flagged_not_passing():
    row = one(statusCheckRollup=[])
    assert row.ci == "none" and row.outcome == "ready_for_review" and "no-ci" in row.flags


def test_a_missing_ci_field_is_unknown_and_never_ready():
    row = one(statusCheckRollup=None)
    assert row.ci == "unknown" and row.outcome == "human_decision" and any("CI" in r for r in row.reasons)


@pytest.mark.parametrize("kw", [{"mergeable": "CONFLICTING"}, {"mergeStateStatus": "DIRTY"}])
def test_conflicts_are_their_own_outcome(kw):
    assert one(**kw).outcome == "conflicting"


def test_an_unknown_mergeability_is_a_human_decision_not_ready():
    assert one(mergeable="UNKNOWN").outcome == "human_decision"


def test_requested_changes():
    assert one(reviewDecision="CHANGES_REQUESTED").outcome == "changes_requested"


def test_a_draft_is_a_draft_whatever_else_is_true():
    assert one(isDraft=True, statusCheckRollup=[check("FAILURE")]).outcome == "draft"


@pytest.mark.parametrize("body,issues", [("", []), ("fix", []), (None, [])])
def test_no_description_and_no_linked_issue_is_missing_requirements(body, issues):
    assert one(body=body, closingIssuesReferences=issues).outcome == "missing_requirements"


def test_a_linked_issue_is_enough_without_a_long_description():
    assert one(body="", closingIssuesReferences=[{"number": 5}]).outcome == "ready_for_review"


@pytest.mark.parametrize("path", ["src/auth/login.py", "app/security/policy.py", ".github/workflows/ci.yml", "package.json", "src/secrets_store.py", "Dockerfile", ".env.example", "deploy/permissions.yaml"])
def test_security_sensitive_paths_need_a_security_review(path):
    row = one(files=[{"path": path, "additions": 3, "deletions": 0}, {"path": "tests/test_x.py", "additions": 1, "deletions": 0}])
    assert row.outcome == "security_review" and any(path in r for r in row.reasons)


def test_docs_about_security_do_not_need_a_security_review():
    assert one(files=[{"path": "docs/security.md", "additions": 3, "deletions": 0}]).outcome != "security_review"


def test_source_changed_without_tests_is_flagged_not_blocked():
    row = one(files=[{"path": "src/a.py", "additions": 3, "deletions": 0}])
    assert "no-tests" in row.flags and row.outcome == "ready_for_review"


def test_docs_only_changes_do_not_need_tests():
    assert "no-tests" not in one(files=[{"path": "docs/a.md", "additions": 3, "deletions": 0}]).flags


def test_a_large_change_is_flagged():
    assert "large" in one(additions=3000, deletions=100, changedFiles=60).flags


def test_a_stale_pr_is_flagged():
    row = one(updatedAt=iso(45))
    assert "stale" in row.flags and any("45" in r or "days" in r for r in row.reasons)


class TestRelationsBetweenPRs:
    def test_prs_that_change_mostly_the_same_files_are_possible_duplicates_of_each_other(self):
        shared = [{"path": f"src/shared{i}.py", "additions": 1, "deletions": 0} for i in range(4)]
        a, b, c = pr(1, files=shared + [{"path": "src/only_a.py", "additions": 1, "deletions": 0}]), pr(2, files=shared), pr(3)
        rows = {r.number: r for r in T.classify([a, b, c], now=NOW, default_branch="main")}
        assert rows[1].outcome == "possible_duplicate" and rows[1].overlaps == [2]
        assert rows[2].outcome == "possible_duplicate" and rows[2].overlaps == [1]
        assert rows[3].outcome == "ready_for_review" and rows[3].overlaps == []

    def test_identical_titles_count_too(self):
        rows = T.classify([pr(1, title="Fix the login bug"), pr(2, title="fix the  LOGIN bug")], now=NOW, default_branch="main")
        assert [r.outcome for r in rows] == ["possible_duplicate", "possible_duplicate"]

    def test_two_single_file_prs_on_the_same_hot_file_are_not_duplicates(self):
        a = pr(1, title="a", files=[{"path": "src/hot.py", "additions": 1, "deletions": 0}])
        b = pr(2, title="b", files=[{"path": "src/hot.py", "additions": 1, "deletions": 0}])
        assert [r.overlaps for r in T.classify([a, b], now=NOW, default_branch="main")] == [[], []]

    def test_a_small_overlap_inside_big_changes_is_not_a_duplicate(self):
        shared = [{"path": "src/shared1.py", "additions": 1, "deletions": 0}, {"path": "src/shared2.py", "additions": 1, "deletions": 0}]
        a = pr(1, title="a", files=[{"path": f"src/f{i}.py", "additions": 1, "deletions": 0} for i in range(8)] + shared)
        b = pr(2, title="b", files=[{"path": f"lib/g{i}.py", "additions": 1, "deletions": 0} for i in range(8)] + shared)
        assert [r.overlaps for r in T.classify([a, b], now=NOW, default_branch="main")] == [[], []]

    def test_one_shared_file_is_not_a_duplicate(self):
        a = pr(1, files=[{"path": "src/x.py", "additions": 1, "deletions": 0}, {"path": "src/a.py", "additions": 1, "deletions": 0}])
        b = pr(2, files=[{"path": "src/x.py", "additions": 1, "deletions": 0}, {"path": "src/b.py", "additions": 1, "deletions": 0}])
        assert [r.overlaps for r in T.classify([a, b], now=NOW, default_branch="main")] == [[], []]

    def test_a_stack_overlaps_by_construction_and_is_not_a_duplicate(self):
        shared = [{"path": f"src/shared{i}.py", "additions": 1, "deletions": 0} for i in range(4)]
        rows = {r.number: r for r in T.classify([pr(1, title="base", files=shared), pr(2, title="on top", baseRefName="branch-1", files=shared)], now=NOW, default_branch="main")}
        assert rows[1].overlaps == [] and rows[2].overlaps == [] and rows[2].outcome == "ready_for_review" and "stacked" in rows[2].flags

    def test_the_whole_chain_of_a_stack_is_exempt_not_only_neighbours(self):
        shared = [{"path": f"src/shared{i}.py", "additions": 1, "deletions": 0} for i in range(4)]
        prs = [pr(1, title="a", files=shared), pr(2, title="b", baseRefName="branch-1", files=shared), pr(3, title="c", baseRefName="branch-2", files=shared)]
        assert all(r.overlaps == [] for r in T.classify(prs, now=NOW, default_branch="main"))

    def test_two_stacks_on_the_same_parent_still_overlap_with_each_other(self):
        shared = [{"path": f"src/shared{i}.py", "additions": 1, "deletions": 0} for i in range(4)]
        prs = [pr(1, title="parent", files=[{"path": "src/p.py", "additions": 1, "deletions": 0}]), pr(2, title="x", baseRefName="branch-1", files=shared), pr(3, title="y", baseRefName="branch-1", files=shared)]
        rows = {r.number: r for r in T.classify(prs, now=NOW, default_branch="main")}
        assert rows[2].overlaps == [3] and rows[3].overlaps == [2] and rows[1].overlaps == []

    def test_a_pr_whose_base_is_its_own_branch_is_not_stacked_on_itself(self):
        row = one(headRefName="odd", baseRefName="odd")
        assert row.stacked_on is None and "stacked" not in row.flags

    def test_a_cycle_of_bases_does_not_loop_forever(self):
        rows = T.classify([pr(1, baseRefName="branch-2"), pr(2, baseRefName="branch-1"), pr(3, baseRefName="branch-3")], now=NOW, default_branch="main")
        assert len(rows) == 3 and [r.number for r in rows] == [1, 2, 3]

    def test_a_pr_whose_base_is_another_prs_branch_is_stacked_on_it(self):
        rows = {r.number: r for r in T.classify([pr(1), pr(2, baseRefName="branch-1")], now=NOW, default_branch="main")}
        assert rows[2].stacked_on == 1 and "stacked" in rows[2].flags and rows[1].stacked_on is None

    def test_a_fork_branch_that_happens_to_be_called_main_does_not_make_the_default_branch_a_stack(self):
        rows = {r.number: r for r in T.classify([pr(1), pr(2, headRefName="main")], now=NOW, default_branch="main")}
        assert rows[1].stacked_on is None and "stacked" not in rows[1].flags

    def test_a_base_that_is_not_the_default_and_not_another_pr_is_noted(self):
        row = one(baseRefName="release/1.0")
        assert row.stacked_on is None and "non-default-base" in row.flags


class TestOrdering:
    def test_the_outcome_priority_is_the_documented_one(self):
        assert T.OUTCOME_ORDER == ["draft", "ci_failure", "conflicting", "changes_requested", "missing_requirements", "possible_duplicate", "security_review", "waiting_for_ci", "human_decision", "ready_for_review"]

    def test_several_problems_report_the_most_urgent_as_the_outcome_and_keep_the_rest_as_reasons(self):
        row = one(statusCheckRollup=[check("FAILURE")], mergeable="CONFLICTING", reviewDecision="CHANGES_REQUESTED")
        assert row.outcome == "ci_failure" and any("conflict" in r for r in row.reasons) and any("changes" in r for r in row.reasons)

    def test_rows_come_out_most_urgent_first_then_by_number(self):
        rows = T.classify([pr(3), pr(2, mergeable="CONFLICTING"), pr(1, statusCheckRollup=[check("FAILURE")])], now=NOW, default_branch="main")
        assert [r.number for r in rows] == [1, 2, 3]

    def test_every_row_has_a_next_action(self):
        rows = T.classify([pr(1), pr(2, isDraft=True), pr(3, mergeable="CONFLICTING")], now=NOW, default_branch="main")
        assert all(r.next_action for r in rows)


def test_a_malformed_pr_entry_is_reported_as_unreadable_not_dropped():
    rows = T.classify([pr(1), {"number": "x"}, "garbage"], now=NOW, default_branch="main")
    assert len(rows) == 3 and sum(r.outcome == "human_decision" and "unreadable" in " ".join(r.reasons) for r in rows) == 2


class TestRendering:
    def test_the_table_lists_every_pr_with_outcome_and_reasons(self):
        text = T.render_markdown(T.classify([pr(1), pr(2, mergeable="CONFLICTING")], now=NOW, default_branch="main"), repo="o/r")
        assert "#1" in text and "#2" in text and "conflicting" in text and "ready_for_review" in text
        assert "never merges, closes, comments, labels or approves" in text

    def test_no_open_prs(self):
        assert "No open pull requests" in T.render_markdown([], repo="o/r")

    def test_json_is_stable(self):
        data = json.loads(T.render_json(T.classify([pr(1)], now=NOW, default_branch="main")))
        assert data[0]["number"] == 1 and data[0]["outcome"] == "ready_for_review" and {"flags", "reasons", "ci", "next_action", "overlaps"} <= set(data[0])


class TestTheGhClientIsReadOnly:
    @pytest.fixture
    def fake_gh(self, tmp_path, monkeypatch):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        log = tmp_path / "calls.log"
        gh = bin_dir / "gh"
        gh.write_text(
            f"#!{sys.executable}\nimport json, sys\nopen({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\nargs = sys.argv[1:]\n"
            "if args[:2] == ['pr', 'list']:\n    print(json.dumps([{'number': 1, 'title': 't', 'baseRefName': 'main', 'headRefName': 'b', 'isDraft': False}]))\n"
            "elif args[:2] == ['repo', 'view']:\n    print('main')\nelse:\n    sys.exit(3)\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return log

    def test_only_listing_and_viewing_commands_are_ever_run(self, tmp_path, fake_gh):
        client = T.GhClient(str(tmp_path))
        assert client.list_open_prs(limit=10)[0]["number"] == 1 and client.default_branch() == "main"
        calls = [line.split()[:2] for line in fake_gh.read_text().splitlines()]
        assert calls and all(call in (["pr", "list"], ["repo", "view"]) for call in calls)

    def test_the_limit_is_bounded(self, tmp_path, fake_gh):
        T.GhClient(str(tmp_path)).list_open_prs(limit=10_000)
        assert f"--limit {T.MAX_LIMIT}" in fake_gh.read_text()

    def test_a_missing_gh_is_an_error_that_says_so(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        with pytest.raises(T.TriageError, match="gh"):
            T.GhClient(str(tmp_path)).list_open_prs(limit=5)
