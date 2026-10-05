"""wisp fleet ci: read-only CI status for the open PRs and the default branch of every fleet repo.

`gh` is replaced by a real executable on PATH that serves JSON fixtures, so the code under test makes real
subprocess calls and parses real output; only GitHub itself is absent.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from wisp.fleet_ci import classify_checks, github_slug, run_ci

FAKE_GH = """#!/bin/sh
d="$FAKE_GH_DIR"
case "$1 $2" in
  "pr list")
    c=$(cat "$d/pr_count" 2>/dev/null || echo 0); c=$((c+1)); echo $c > "$d/pr_count"
    f="$d/prs.$c.json"; [ -f "$f" ] || f="$d/prs.json" ;;
  "run list") f="$d/runs.json" ;;
  *) echo "fake gh: unexpected $*" >&2; exit 9 ;;
esac
[ -f "$f" ] || { echo "[]"; exit 0; }
cat "$f"
"""


def _run_check(name: str, status: str = "COMPLETED", conclusion: str = "SUCCESS") -> dict:
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def _pr(number: int, *checks: dict, branch: str = "feat/x") -> dict:
    return {
        "number": number, "title": f"pr {number}", "headRefName": branch,
        "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "statusCheckRollup": list(checks),
    }


@pytest.fixture
def gh(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "gh"
    exe.write_text(FAKE_GH)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    data = tmp_path / "ghdata"
    data.mkdir()
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_GH_DIR", str(data))
    return data


def _manifest(tmp_path: Path, url: str = "https://github.com/o/r.git") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["remote", "add", "origin", url]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    m = tmp_path / "wisp.fleet.toml"
    m.write_text(f'[[repo]]\nname = "r"\npath = "{repo}"\nrole = "orchestrator"\n')
    return m


class TestGithubSlug:
    @pytest.mark.parametrize("url", [
        "https://github.com/o/r.git", "https://github.com/o/r", "git@github.com:o/r.git", "ssh://git@github.com/o/r.git",
    ])
    def test_github_urls_resolve_to_owner_and_name(self, url):
        assert github_slug(url) == "o/r"

    @pytest.mark.parametrize("url", ["", "/local/bare/repo.git", "https://gitlab.com/o/r.git", "https://github.com/onlyowner"])
    def test_anything_else_is_not_a_github_repo(self, url):
        assert github_slug(url) is None


class TestClassify:
    def test_all_passing(self):
        assert classify_checks([_run_check("a"), _run_check("b")]).state == "pass"

    def test_any_failure_wins_over_pending(self):
        c = classify_checks([_run_check("a", conclusion="FAILURE"), _run_check("b", status="IN_PROGRESS", conclusion="")])
        assert c.state == "fail" and c.failing == ["a"] and c.pending == 1

    def test_pending_when_nothing_failed(self):
        assert classify_checks([_run_check("a"), _run_check("b", status="QUEUED", conclusion="")]).state == "pending"

    @pytest.mark.parametrize("conclusion", ["TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"])
    def test_non_success_conclusions_count_as_failures(self, conclusion):
        assert classify_checks([_run_check("a", conclusion=conclusion)]).state == "fail"

    def test_skipped_and_neutral_are_not_failures(self):
        assert classify_checks([_run_check("a", conclusion="SKIPPED"), _run_check("b", conclusion="NEUTRAL")]).state == "pass"

    def test_status_contexts_use_their_state_field(self):
        ok = {"__typename": "StatusContext", "context": "ci/x", "state": "SUCCESS"}
        bad = {"__typename": "StatusContext", "context": "ci/y", "state": "FAILURE"}
        wait = {"__typename": "StatusContext", "context": "ci/z", "state": "PENDING"}
        assert classify_checks([ok]).state == "pass"
        assert classify_checks([ok, bad]).state == "fail"
        assert classify_checks([ok, wait]).state == "pending"

    def test_no_checks_is_none_not_pass(self):
        assert classify_checks([]).state == "none"


class TestRunCi:
    def test_reports_each_open_pr_and_the_default_branch(self, gh, tmp_path, capsys):
        (gh / "prs.json").write_text(json.dumps([
            _pr(7, _run_check("test-python", conclusion="FAILURE"), _run_check("qa")),
            _pr(8, _run_check("test-python")),
        ]))
        (gh / "runs.json").write_text(json.dumps([{"workflowName": "CI", "status": "completed", "conclusion": "success"}]))
        assert run_ci(["--manifest", str(_manifest(tmp_path))]) == 0
        out = capsys.readouterr().out
        assert "#7" in out and "fail" in out and "test-python" in out
        assert "#8" in out and "pass" in out
        assert "main" in out

    def test_strict_fails_on_a_failing_pr(self, gh, tmp_path):
        (gh / "prs.json").write_text(json.dumps([_pr(7, _run_check("t", conclusion="FAILURE"))]))
        assert run_ci(["--strict", "--manifest", str(_manifest(tmp_path))]) == 1

    def test_strict_fails_when_the_default_branch_is_red_even_with_no_prs(self, gh, tmp_path):
        """A red main is what made every PR look broken; it must not hide behind an empty PR list."""
        (gh / "runs.json").write_text(json.dumps([{"workflowName": "CI", "status": "completed", "conclusion": "failure"}]))
        assert run_ci(["--strict", "--manifest", str(_manifest(tmp_path))]) == 1

    def test_strict_passes_when_everything_is_green(self, gh, tmp_path):
        (gh / "prs.json").write_text(json.dumps([_pr(7, _run_check("t"))]))
        (gh / "runs.json").write_text(json.dumps([{"workflowName": "CI", "status": "completed", "conclusion": "success"}]))
        assert run_ci(["--strict", "--manifest", str(_manifest(tmp_path))]) == 0

    def test_json_output(self, gh, tmp_path, capsys):
        (gh / "prs.json").write_text(json.dumps([_pr(7, _run_check("t", conclusion="FAILURE"))]))
        run_ci(["--json", "--manifest", str(_manifest(tmp_path))])
        data = json.loads(capsys.readouterr().out)
        assert data["repos"][0]["name"] == "r"
        assert data["repos"][0]["prs"][0]["state"] == "fail"
        assert data["repos"][0]["prs"][0]["failing"] == ["t"]

    def test_non_github_remotes_are_skipped_and_said_so(self, gh, tmp_path, capsys):
        assert run_ci(["--manifest", str(_manifest(tmp_path, "/some/bare/repo.git"))]) == 0
        assert "not a github repo" in capsys.readouterr().out.lower()

    def test_a_repo_with_no_remote_is_skipped_not_a_crash(self, gh, tmp_path, capsys):
        repo = tmp_path / "bare"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True, capture_output=True)
        m = tmp_path / "m.toml"
        m.write_text(f'[[repo]]\nname = "r"\npath = "{repo}"\nrole = "orchestrator"\n')
        assert run_ci(["--manifest", str(m)]) == 0
        assert "no remote" in capsys.readouterr().out.lower()

    def test_missing_gh_exits_three_with_a_message(self, tmp_path, monkeypatch, capsys):
        manifest = _manifest(tmp_path)  # built first: it needs git, which an emptied PATH would hide
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
        assert run_ci(["--manifest", str(manifest)]) == 3
        assert "gh" in capsys.readouterr().err

    def test_a_bad_manifest_exits_two(self, tmp_path, capsys):
        bad = tmp_path / "m.toml"
        bad.write_text("[[repo]]\nname='a'\n")
        assert run_ci(["--manifest", str(bad)]) == 2


class TestWatch:
    def test_waits_through_pending_then_reports_the_final_state(self, gh, tmp_path, capsys):
        (gh / "prs.1.json").write_text(json.dumps([_pr(7, _run_check("t", status="IN_PROGRESS", conclusion=""))]))
        (gh / "prs.2.json").write_text(json.dumps([_pr(7, _run_check("t"))]))
        code = run_ci(["--watch", "0", "--timeout", "10", "--manifest", str(_manifest(tmp_path))])
        assert code == 0
        assert (gh / "pr_count").read_text().strip() == "2"
        assert "pass" in capsys.readouterr().out

    def test_stops_early_on_a_failure_without_waiting_for_the_rest(self, gh, tmp_path):
        (gh / "prs.json").write_text(json.dumps([
            _pr(7, _run_check("a", conclusion="FAILURE"), _run_check("b", status="IN_PROGRESS", conclusion="")),
        ]))
        assert run_ci(["--watch", "0", "--timeout", "10", "--manifest", str(_manifest(tmp_path))]) == 1
        assert (gh / "pr_count").read_text().strip() == "1"

    def test_times_out_while_still_pending_with_exit_four(self, gh, tmp_path):
        (gh / "prs.json").write_text(json.dumps([_pr(7, _run_check("t", status="QUEUED", conclusion=""))]))
        assert run_ci(["--watch", "0", "--timeout", "0", "--manifest", str(_manifest(tmp_path))]) == 4
