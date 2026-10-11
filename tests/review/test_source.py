"""Reading a diff from real git. The inputs are untrusted (a ref, a repository's own configuration, paths named in a diff), so the tests include a repository that
tries to run a program through `diff.external` and a textconv filter, refs shaped like options, and a path that climbs out of the workspace."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from wisp.review import source as S
from wisp.review.diff import parse_unified_diff


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    (tmp_path / "gone.py").write_text("bye\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def paths(text):
    return {fd.path: fd for fd in parse_unified_diff(text)}


class TestUncommittedAndStaged:
    def test_tracked_edits_deletions_and_new_untracked_files_are_all_in_the_diff(self, repo):
        (repo / "a.py").write_text("x = 1\ny = 3\n")
        (repo / "gone.py").unlink()
        (repo / "new.py").write_text("print('new')\n")
        found = paths(S.read_diff(str(repo), S.DiffSource("uncommitted")))
        assert set(found) == {"a.py", "gone.py", "new.py"} and found["new.py"].status == "added" and found["gone.py"].status == "deleted"

    def test_staged_means_only_what_is_in_the_index(self, repo):
        (repo / "a.py").write_text("x = 9\ny = 2\n")
        git(repo, "add", "a.py")
        (repo / "a.py").write_text("x = 9\ny = 8\n")
        found = paths(S.read_diff(str(repo), S.DiffSource("staged")))
        assert [t for _n, t in found["a.py"].added_lines()] == ["x = 9"]

    def test_a_repository_with_no_commit_yet_is_reviewable(self, tmp_path):
        git(tmp_path, "init", "-q", "-b", "main")
        (tmp_path / "first.py").write_text("a = 1\n")
        git(tmp_path, "add", "first.py")
        (tmp_path / "second.py").write_text("b = 2\n")
        assert set(paths(S.read_diff(str(tmp_path), S.DiffSource("uncommitted")))) == {"first.py", "second.py"}

    def test_untracked_files_are_bounded(self, repo):
        for i in range(S.MAX_UNTRACKED_FILES + 5):
            (repo / f"u{i}.txt").write_text("x\n")
        text = S.read_diff(str(repo), S.DiffSource("uncommitted"))
        assert len(paths(text)) == S.MAX_UNTRACKED_FILES

    def test_an_untracked_file_that_is_too_big_is_skipped_not_read(self, repo):
        (repo / "big.bin").write_bytes(b"x" * (S.MAX_UNTRACKED_BYTES + 1))
        assert "big.bin" not in paths(S.read_diff(str(repo), S.DiffSource("uncommitted")))


class TestWispsOwnStateIsNotTheUsersChange:
    def test_state_directories_are_left_out_of_the_uncommitted_review(self, repo):
        (repo / ".wisp").mkdir()
        (repo / ".wisp" / "wisp.db").write_bytes(b"\0\1\2")
        (repo / ".agent").mkdir()
        (repo / ".agent" / "runtime.log").write_text("log line\n")
        (repo / "real.py").write_text("x = 1\n")
        assert set(paths(S.read_diff(str(repo), S.DiffSource("uncommitted")))) == {"real.py"}

    def test_a_tracked_file_under_a_state_directory_is_left_out_too(self, repo):
        (repo / ".wisp").mkdir()
        (repo / ".wisp" / "notes.md").write_text("one\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "tracked state")
        (repo / ".wisp" / "notes.md").write_text("two\n")
        (repo / "a.py").write_text("x = 2\ny = 2\n")
        assert set(paths(S.read_diff(str(repo), S.DiffSource("uncommitted")))) == {"a.py"}

    def test_the_same_applies_to_staged(self, repo):
        (repo / ".agent").mkdir()
        (repo / ".agent" / "runtime.log").write_text("x\n")
        (repo / "b.py").write_text("y = 1\n")
        git(repo, "add", "-A", "-f")
        assert set(paths(S.read_diff(str(repo), S.DiffSource("staged")))) == {"b.py"}

    def test_a_committed_change_to_them_is_still_part_of_a_commit_review(self, repo):
        (repo / ".wisp").mkdir()
        (repo / ".wisp" / "notes.md").write_text("one\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "adds it")
        sha = git(repo, "rev-parse", "HEAD").strip()
        assert ".wisp/notes.md" in paths(S.read_diff(str(repo), S.DiffSource("commit", commit=sha)))


class TestRangesAndCommits:
    def test_a_branch_against_its_base(self, repo):
        git(repo, "checkout", "-q", "-b", "feature")
        (repo / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
        git(repo, "commit", "-q", "-am", "feature work")
        found = paths(S.read_diff(str(repo), S.DiffSource("range", base="main", head="feature")))
        assert set(found) == {"a.py"} and [t for _n, t in found["a.py"].added_lines()] == ["z = 3"]

    def test_the_range_is_the_branchs_own_work_not_the_bases_later_commits(self, repo):
        git(repo, "checkout", "-q", "-b", "feature")
        (repo / "f.py").write_text("f = 1\n")
        git(repo, "add", "f.py")
        git(repo, "commit", "-q", "-m", "f")
        git(repo, "checkout", "-q", "main")
        (repo / "m.py").write_text("m = 1\n")
        git(repo, "add", "m.py")
        git(repo, "commit", "-q", "-m", "m")
        assert set(paths(S.read_diff(str(repo), S.DiffSource("range", base="main", head="feature")))) == {"f.py"}

    def test_one_commit_including_the_root_commit(self, repo):
        sha = git(repo, "rev-parse", "HEAD").strip()
        assert set(paths(S.read_diff(str(repo), S.DiffSource("commit", commit=sha)))) == {"a.py", "gone.py"}

    def test_a_ref_that_does_not_exist_is_an_error(self, repo):
        with pytest.raises(S.SourceError, match="not found"):
            S.read_diff(str(repo), S.DiffSource("range", base="main", head="nope"))

    def test_a_diff_over_the_size_bound_is_an_error_not_a_partial_read(self, repo, monkeypatch):
        monkeypatch.setattr(S, "MAX_DIFF_BYTES", 50)
        (repo / "a.py").write_text("x = 1\ny = 2\nz = 3\n" * 20)
        with pytest.raises(S.SourceError, match="over"):
            S.read_diff(str(repo), S.DiffSource("uncommitted"))

    def test_outside_a_repository_is_an_error(self, tmp_path):
        with pytest.raises(S.SourceError, match="not a git repository"):
            S.read_diff(str(tmp_path), S.DiffSource("uncommitted"))


class TestRefsAreValidated:
    @pytest.mark.parametrize("ref", ["--output=/tmp/x", "-p", "a b", "x;rm -rf", "$(id)", "a`id`", "", "x" * 200, "a..b", "a@{1}", "main\nfeature", "-"])
    def test_a_ref_that_could_be_an_option_or_a_command_is_refused(self, repo, ref):
        with pytest.raises(S.SourceError, match="invalid git ref"):
            S.read_diff(str(repo), S.DiffSource("range", base="main", head=ref))

    @pytest.mark.parametrize("ref", ["main", "origin/main", "feature/x-1.2", "HEAD~2", "v1.0", "HEAD^"])
    def test_ordinary_refs_are_accepted_by_the_validator(self, ref):
        assert S.validate_ref(ref, "head") == ref


class TestAHostileRepositoryCannotRunProgramsThroughTheDiff:
    def test_diff_external_and_textconv_are_not_executed(self, repo, tmp_path_factory):
        marker = tmp_path_factory.mktemp("marker") / "ran"
        script = repo.parent / "evil.sh"
        script.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        git(repo, "config", "diff.external", str(script))
        git(repo, "config", "diff.evil.textconv", str(script))
        git(repo, "config", "core.fsmonitor", str(script))
        (repo / ".gitattributes").write_text("*.py diff=evil\n")
        (repo / "a.py").write_text("x = 1\ny = 99\n")
        S.read_diff(str(repo), S.DiffSource("uncommitted"))
        assert not marker.exists()


class TestPullRequests:
    @pytest.fixture
    def fake_gh(self, tmp_path, monkeypatch, repo):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        diff = "diff --git a/p.py b/p.py\nnew file mode 100644\n--- /dev/null\n+++ b/p.py\n@@ -0,0 +1 @@\n+print('pr')\n"
        gh = bin_dir / "gh"
        gh.write_text(f"#!{sys.executable}\nimport sys\nargs = sys.argv[1:]\nif args[:2] == ['pr', 'diff']:\n    sys.stdout.write({diff!r})\nelif args[:2] == ['pr', 'view']:\n    print('{{\"headRefOid\": \"0000000000000000000000000000000000000000\", \"baseRefName\": \"main\", \"title\": \"t\"}}')\nelse:\n    sys.exit(2)\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
        return gh

    def test_a_pr_diff_comes_from_gh_read_only(self, repo, fake_gh):
        assert set(paths(S.read_diff(str(repo), S.DiffSource("pr", pr=7)))) == {"p.py"}

    def test_without_gh_the_error_says_so(self, repo, monkeypatch):
        monkeypatch.setenv("PATH", "/nonexistent")
        with pytest.raises(S.SourceError, match="gh"):
            S.read_diff(str(repo), S.DiffSource("pr", pr=7))

    def test_a_pr_number_must_be_a_positive_integer(self, repo):
        with pytest.raises(S.SourceError, match="pull request number"):
            S.read_diff(str(repo), S.DiffSource("pr", pr=0))

    def test_post_images_of_a_pr_whose_head_is_not_local_are_unavailable_not_guessed(self, repo, fake_gh):
        reader = S.post_image_reader(str(repo), S.DiffSource("pr", pr=7))
        assert reader("p.py") is None


class TestPostImages:
    def test_the_working_tree_is_read_for_uncommitted(self, repo):
        (repo / "a.py").write_text("x = 'new'\n")
        assert S.post_image_reader(str(repo), S.DiffSource("uncommitted"))("a.py") == "x = 'new'\n"

    def test_the_index_is_read_for_staged(self, repo):
        (repo / "a.py").write_text("x = 'staged'\n")
        git(repo, "add", "a.py")
        (repo / "a.py").write_text("x = 'unstaged'\n")
        assert S.post_image_reader(str(repo), S.DiffSource("staged"))("a.py") == "x = 'staged'\n"

    def test_the_heads_content_is_read_for_a_range_without_checking_it_out(self, repo):
        git(repo, "checkout", "-q", "-b", "feature")
        (repo / "a.py").write_text("x = 'feature'\n")
        git(repo, "commit", "-q", "-am", "f")
        git(repo, "checkout", "-q", "main")
        assert S.post_image_reader(str(repo), S.DiffSource("range", base="main", head="feature"))("a.py") == "x = 'feature'\n"
        assert (repo / "a.py").read_text() == "x = 1\ny = 2\n"

    def test_a_missing_file_is_none(self, repo):
        assert S.post_image_reader(str(repo), S.DiffSource("uncommitted"))("nope.py") is None

    def test_a_symlink_to_another_file_inside_the_workspace_is_not_read_either(self, repo):
        (repo / "link.py").symlink_to(repo / "a.py")
        assert S.post_image_reader(str(repo), S.DiffSource("uncommitted"))("link.py") is None

    def test_a_path_with_a_parent_segment_is_refused_even_when_it_lands_inside(self, repo):
        (repo / "sub").mkdir()
        assert S.post_image_reader(str(repo), S.DiffSource("uncommitted"))("sub/../a.py") is None

    @pytest.mark.parametrize("path", ["../outside.py", "/etc/passwd", "a/../../x.py", "sub/../../x.py"])
    def test_a_path_that_leaves_the_workspace_is_refused(self, repo, path):
        (repo.parent / "outside.py").write_text("secret = 1\n")
        assert S.post_image_reader(str(repo), S.DiffSource("uncommitted"))(path) is None

    def test_a_symlink_out_of_the_workspace_is_not_followed(self, repo):
        (repo.parent / "target.txt").write_text("outside\n")
        (repo / "link.py").symlink_to(repo.parent / "target.txt")
        assert S.post_image_reader(str(repo), S.DiffSource("uncommitted"))("link.py") is None

    def test_a_file_over_the_size_bound_is_none(self, repo):
        (repo / "big.py").write_text("x = 1\n" * 1_000_000)
        assert S.post_image_reader(str(repo), S.DiffSource("uncommitted"))("big.py") is None


class TestSymbolSearch:
    def test_a_name_mentioned_by_a_test_is_found_and_others_are_not(self, repo):
        (repo / "tests").mkdir()
        (repo / "tests" / "test_a.py").write_text("from a import compute\n\ndef test_compute():\n    assert compute(1)\n")
        (repo / "src.py").write_text("def other_symbol(): pass\n")
        search = S.symbol_searcher(str(repo))
        assert search("compute") is True and search("other_symbol") is False

    def test_only_whole_words_count(self, repo):
        (repo / "tests").mkdir()
        (repo / "tests" / "test_a.py").write_text("def test_x():\n    assert compute_all(1)\n")
        assert S.symbol_searcher(str(repo))("compute") is False

    def test_a_search_that_hit_its_bound_assumes_tested_and_says_so(self, repo):
        (repo / "tests").mkdir()
        for i in range(S.MAX_TEST_FILES + 3):
            (repo / "tests" / f"test_{i}.py").write_text("x = 1\n")
        search = S.symbol_searcher(str(repo))
        assert search("never_mentioned") is True and search.truncated is True


def test_a_truncated_test_search_is_recorded_as_a_gap_not_only_remembered(repo):
    (repo / "tests").mkdir()
    for i in range(S.MAX_TEST_FILES + 3):
        (repo / "tests" / f"test_{i}.py").write_text("x = 1\n")
    gaps: list[str] = []
    search = S.symbol_searcher(str(repo), gaps=gaps)
    search("anything")
    search("anything_else")
    assert len(gaps) == 1 and "missing-tests" in gaps[0] and "bound" in gaps[0]
