"""`wisp review` and `wisp triage` as functions: real git repositories, an injected model, output captured from the streams the REPL also uses."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from tests.review.helpers import fake_token
from wisp.review import cli as C


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("def load(path):\n    return open(path).read()\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def review(repo, *argv, runner=None, **kw):
    out, err = io.StringIO(), io.StringIO()
    code = C.run_review(list(argv), workspace=str(repo), runner=runner, out=out, err=err, **kw)
    return code, out.getvalue(), err.getvalue()


def reply(*findings):
    async def runner(prompt):
        return json.dumps({"findings": list(findings), "summary": "s"})

    return runner


class TestSources:
    def test_the_default_is_the_uncommitted_work_and_a_clean_change_exits_zero(self, repo):
        (repo / "app.py").write_text("def load(path):\n    return open(path).read().strip()\n")
        code, out, _ = review(repo, "--no-model")
        assert code == 0 and "CLEAN" in out and "uncommitted changes" in out

    def test_a_secret_blocks_exits_one_and_is_never_printed(self, repo):
        token = fake_token("openrouter")
        (repo / "cfg.py").write_text(f"KEY = '{token}'\n")
        code, out, err = review(repo, "--no-model")
        assert code == 1 and "BLOCKED" in out and token not in out + err

    def test_staged_ignores_unstaged_edits(self, repo):
        (repo / "a.txt").write_text("staged\n")
        git(repo, "add", "a.txt")
        (repo / "b.txt").write_text(f"{fake_token('github')}\n")
        code, out, _ = review(repo, "--staged", "--no-model")
        assert code == 0 and "b.txt" not in out

    def test_a_branch_range(self, repo):
        git(repo, "checkout", "-q", "-b", "feature")
        (repo / "x.py").write_text(f"K = '{fake_token('github')}'\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "oops")
        code, out, _ = review(repo, "--base", "main", "--no-model")
        assert code == 1 and "main...HEAD" in out
        assert review(repo, "--base", "main", "--head", "feature", "--no-model")[0] == 1

    def test_one_commit(self, repo):
        sha = git(repo, "rev-parse", "HEAD").strip()
        code, out, _ = review(repo, "--commit", sha, "--no-model")
        assert code == 0 and "commit" in out

    def test_a_pull_request_comes_from_gh(self, repo, monkeypatch, tmp_path_factory):
        import os
        import stat
        import sys

        bin_dir = tmp_path_factory.mktemp("bin")
        diff = "diff --git a/p.py b/p.py\nnew file mode 100644\n--- /dev/null\n+++ b/p.py\n@@ -0,0 +1 @@\n+print('pr')\n"
        gh = bin_dir / "gh"
        gh.write_text(f"#!{sys.executable}\nimport sys\nif sys.argv[1:3] == ['pr', 'diff']:\n    sys.stdout.write({diff!r})\nelse:\n    sys.exit(2)\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        code, out, _ = review(repo, "--pr", "7", "--no-model")
        assert "pull request #7" in out and "p.py" in out
        assert code == 0

    def test_a_pr_cannot_be_combined_with_running_its_tests(self, repo):
        code, _, err = review(repo, "--pr", "7", "--run-tests", "--no-model")
        assert code == 2 and "--run-tests" in err and "pull request" in err

    @pytest.mark.parametrize("argv", [["--staged", "--base", "main"], ["--commit", "abc", "--pr", "1"], ["--head", "x"], ["--pr", "1", "--staged"]])
    def test_sources_are_mutually_exclusive(self, repo, argv):
        code, _, err = review(repo, *argv, "--no-model")
        assert code == 2 and err

    def test_an_invalid_ref_is_a_usage_error_not_a_crash(self, repo):
        code, _, err = review(repo, "--base=--output=/tmp/x", "--no-model")
        assert code == 2 and "invalid git ref" in err

    def test_a_missing_ref_is_an_error(self, repo):
        code, _, err = review(repo, "--base", "main", "--head", "nope", "--no-model")
        assert code == 2 and "not found" in err

    def test_outside_a_repository(self, tmp_path):
        code, _, err = review(tmp_path, "--no-model")
        assert code == 2 and "not a git repository" in err


class TestOutputAndExitCodes:
    def test_json_output_is_one_parseable_object(self, repo):
        (repo / "cfg.py").write_text(f"KEY = '{fake_token('openrouter')}'\n")
        code, out, _ = review(repo, "--no-model", "--json")
        data = json.loads(out)
        assert code == 1 and data["verdict"] == "blocked" and data["findings"][0]["rule"] == "secret"

    def test_fail_on_attention_fails_on_a_warning(self, repo):
        (repo / "new.py").write_text("def brand_new():\n    breakpoint()\n")
        assert review(repo, "--no-model")[0] == 0
        assert review(repo, "--no-model", "--fail-on", "attention")[0] == 1

    def test_fail_on_incomplete_fails_when_the_model_was_asked_for_and_failed(self, repo):
        (repo / "a.py").write_text("x = 1\n")

        async def broken(prompt):
            raise ConnectionError("down")

        code, out, _ = review(repo, "--fail-on", "incomplete", runner=broken)
        assert code == 1 and "INCOMPLETE" in out and "down" in out
        assert review(repo, runner=broken)[0] == 0

    def test_an_unknown_fail_on_is_a_usage_error(self, repo):
        assert review(repo, "--fail-on", "sometimes", "--no-model")[0] == 2

    def test_help_exits_zero_and_prints_usage(self, repo):
        code, out, _ = review(repo, "--help")
        assert code == 0 and "--staged" in out and "--no-model" in out


class TestTheModelLens:
    def test_a_grounded_model_finding_appears_with_the_harness_line(self, repo):
        (repo / "app.py").write_text("def load(path):\n    return eval(open(path).read())\n")
        claim = {"file": "app.py", "quote": "return eval(open(path).read())", "severity": "warn", "message": "eval on file content", "suggestion": "json.loads", "rule": ""}
        code, out, _ = review(repo, runner=reply(claim))
        assert code == 0 and "ATTENTION" in out and "app.py:2" in out and "eval on file content" in out

    def test_only_the_chosen_lenses_run(self, repo):
        (repo / "a.py").write_text("x = 1\n")
        prompts = []

        async def runner(prompt):
            prompts.append(prompt)
            return json.dumps({"findings": []})

        review(repo, "--lens", "security", runner=runner)
        assert len(prompts) == 1 and "security reviewer" in prompts[0]

    def test_no_model_never_builds_a_model_runner(self, repo, monkeypatch):
        def refuse(*a, **k):
            raise AssertionError("a model runner was built with --no-model")

        monkeypatch.setattr(C, "default_model_runner", refuse)
        (repo / "a.py").write_text("x = 1\n")
        assert review(repo, "--no-model")[0] == 0

    def test_an_unknown_lens_is_a_usage_error(self, repo):
        assert review(repo, "--lens", "vibes", "--no-model")[0] == 2

    def test_the_default_runner_reads_only_and_uses_a_fresh_session_per_call(self, repo, monkeypatch):
        calls = []

        async def fake_headless(**kw):
            calls.append(kw)
            return {"ok": True, "content": json.dumps({"findings": [], "summary": ""}), "errors": []}

        monkeypatch.setattr("wisp.headless.run_headless", fake_headless)
        (repo / "a.py").write_text("x = 1\n")
        code, out, _ = review(repo, "--lens", "security", "--lens", "tests", model="m1", provider="p1")
        assert code == 0 and len(calls) == 2
        assert all(c["permission_mode"] == "read_only" and c["model"] == "m1" and c["provider"] == "p1" and c["workspace"] == str(repo) for c in calls)
        assert len({c["session_id"] for c in calls}) == 2

    def test_a_failed_headless_run_is_a_lens_error_not_a_clean_review(self, repo, monkeypatch):
        async def fake_headless(**kw):
            return {"ok": False, "content": "", "errors": [{"message": "provider unreachable"}]}

        monkeypatch.setattr("wisp.headless.run_headless", fake_headless)
        (repo / "a.py").write_text("x = 1\n")
        code, out, _ = review(repo, "--lens", "tests")
        assert "INCOMPLETE" in out and "provider unreachable" in out


class TestRules:
    RULES = '[[rule]]\nid = "no-eval"\ntext = "No eval."\nseverity = "block"\nforbid = "eval\\\\("\n'

    def test_the_repos_default_rules_file_is_applied(self, repo):
        (repo / ".wisp").mkdir()
        (repo / ".wisp" / "review-rules.toml").write_text(self.RULES)
        (repo / "a.py").write_text("x = eval(y)\n")
        code, out, _ = review(repo, "--no-model")
        assert code == 1 and "rule:no-eval" in out

    def test_an_explicit_rules_file(self, repo, tmp_path_factory):
        rules = tmp_path_factory.mktemp("r") / "mine.toml"
        rules.write_text(self.RULES)
        (repo / "a.py").write_text("x = eval(y)\n")
        assert review(repo, "--no-model", "--rules", str(rules))[0] == 1

    def test_an_explicit_rules_file_that_does_not_exist_is_an_error(self, repo):
        code, _, err = review(repo, "--no-model", "--rules", str(repo / "nope.toml"))
        assert code == 2 and "nope.toml" in err

    def test_a_broken_rules_file_is_an_error_naming_the_problem_not_ignored(self, repo):
        (repo / ".wisp").mkdir()
        (repo / ".wisp" / "review-rules.toml").write_text('[[rule]]\nid = "x"\ntext = "t"\nbogus = 1\n')
        (repo / "a.py").write_text("x = eval(y)\n")
        code, out, err = review(repo, "--no-model")
        assert code == 2 and "bogus" in err and out == ""


class TestTriageCommand:
    def test_it_prints_a_table_and_exits_zero(self, repo):
        class Client:
            def list_open_prs(self, limit):
                return [{"number": 4, "title": "t", "baseRefName": "main", "headRefName": "b", "isDraft": True, "mergeable": "MERGEABLE", "statusCheckRollup": [], "files": [], "body": "x" * 30}]

            def default_branch(self):
                return "main"

        out, err = io.StringIO(), io.StringIO()
        code = C.run_triage([], workspace=str(repo), client=Client(), out=out, err=err)
        assert code == 0 and "#4" in out.getvalue() and "draft" in out.getvalue()

    def test_json_and_limit(self, repo):
        seen = {}

        class Client:
            def list_open_prs(self, limit):
                seen["limit"] = limit
                return []

            def default_branch(self):
                return "main"

        out, err = io.StringIO(), io.StringIO()
        assert C.run_triage(["--json", "--limit", "7"], workspace=str(repo), client=Client(), out=out, err=err) == 0
        assert json.loads(out.getvalue()) == [] and seen["limit"] == 7

    def test_a_gh_failure_is_an_error_not_an_empty_report(self, repo):
        from wisp.review.triage import TriageError

        class Client:
            def list_open_prs(self, limit):
                raise TriageError("gh is not installed")

            def default_branch(self):
                return "main"

        out, err = io.StringIO(), io.StringIO()
        assert C.run_triage([], workspace=str(repo), client=Client(), out=out, err=err) == 2
        assert "gh is not installed" in err.getvalue() and out.getvalue() == ""

    def test_a_bad_limit_is_a_usage_error_before_anything_is_read(self, repo):
        class Client:
            def list_open_prs(self, limit):
                raise AssertionError("gh must not be called with a bad limit")

            def default_branch(self):
                return "main"

        err = io.StringIO()
        assert C.run_triage(["--limit", "0"], workspace=str(repo), client=Client(), out=io.StringIO(), err=err) == 2 and "--limit" in err.getvalue()
        assert C.run_triage(["--limit", "100000"], workspace=str(repo), client=Client(), out=io.StringIO(), err=io.StringIO()) == 2


class TestCtrlC:
    def test_a_real_sigint_stops_a_slow_review_and_cancels_the_model_call(self, repo):
        import os
        import signal
        import threading
        import time

        (repo / "a.py").write_text("x = 1\n")
        started, cancelled = threading.Event(), threading.Event()
        # A background job (nohup, `&`, some CI runners) starts with SIGINT ignored and Python then keeps it ignored: install the handler this test is about.
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)

        async def slow(prompt):
            import asyncio

            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "{}"

        def interrupt():
            started.wait(10)
            time.sleep(0.2)
            os.kill(os.getpid(), signal.SIGINT)

        threading.Thread(target=interrupt, daemon=True).start()
        began = time.monotonic()
        try:
            code, out, err = review(repo, "--lens", "tests", runner=slow)
        finally:
            signal.signal(signal.SIGINT, previous)
        assert code == 2 and "stopped" in err and time.monotonic() - began < 20
        assert cancelled.wait(5), "the model call was left running"

    def test_a_second_review_in_the_same_process_still_works(self, repo):
        (repo / "a.py").write_text("x = 1\n")
        assert review(repo, "--no-model")[0] == 0
        assert review(repo, "--lens", "tests", runner=reply())[0] == 0
        assert review(repo, "--lens", "tests", runner=reply())[0] == 0
