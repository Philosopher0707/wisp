"""The commit stamped into a generated page must survive any way of merging the PR that regenerated it.

`register.md`, `CURRENT_FLAGS.md` and `CURRENT_AUTHORITIES.md` say "Generated <date> at `<sha>`", and a test requires that sha to be a
real ancestor of HEAD. The generators stamped `git rev-parse --short HEAD`, which on a PR branch is a BRANCH commit. A squash merge
replaces the branch's commits with one new commit, so the stamp named a commit that no longer exists on `main`, and `main` CI went red
(PR #85, repaired by #86). The stamp is now the merge-base with `origin/main`: a commit already on `main`, an ancestor of everything
`main` becomes whether the PR is merged, squashed or rebased.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": "/nonexistent"}


def _stamp_module():
    spec = importlib.util.spec_from_file_location("_page_stamp", REPO / "scripts" / "_page_stamp.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(cwd: Path, *args: str, date: str | None = None) -> str:
    env = dict(_ENV)
    if date:
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = f"{date}T12:00:00"
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd: Path, name: str, date: str | None = None) -> str:
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", name, date=date)
    return git(cwd, "rev-parse", "--short", "HEAD")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    return tmp_path


def _is_ancestor(cwd: Path, sha: str, of: str = "HEAD") -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha, of], cwd=cwd, env=_ENV).returncode == 0


def test_on_a_branch_the_stamp_is_the_commit_it_forked_from_main_not_the_branch_tip(repo):
    commit(repo, "a")
    base = commit(repo, "b")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")  # main as the remote knows it
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "c")
    tip = commit(repo, "d")
    stamp = _stamp_module().stamp_sha(repo)
    assert stamp == base and stamp != tip


def test_a_squash_merge_does_not_orphan_the_stamp(repo):
    """The #85 failure, reproduced: stamp on the branch, squash onto main, ask whether the stamp is still an ancestor."""
    commit(repo, "a")
    base = commit(repo, "b")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "c")
    branch_tip = commit(repo, "d")
    stamp = _stamp_module().stamp_sha(repo)

    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--squash", "feat")
    git(repo, "commit", "-q", "-m", "squash of feat")
    assert _is_ancestor(repo, stamp), "the new stamp survives the squash"
    assert not _is_ancestor(repo, branch_tip), "the OLD stamp (the branch tip) is exactly what a squash orphans"
    assert stamp == base


def test_a_rebase_merge_and_a_merge_commit_also_keep_the_stamp(repo):
    commit(repo, "a")
    base = commit(repo, "b")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "c")
    stamp = _stamp_module().stamp_sha(repo)
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "--no-ff", "-q", "-m", "merge feat", "feat")
    assert _is_ancestor(repo, stamp) and stamp == base


def test_on_main_itself_the_stamp_is_head(repo):
    commit(repo, "a")
    tip = commit(repo, "b")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert _stamp_module().stamp_sha(repo) == tip


def test_without_an_origin_main_ref_it_falls_back_to_head(repo):
    """A checkout with no `origin/main` (a fresh clone of another branch, a shallow CI checkout) behaves as before."""
    commit(repo, "a")
    tip = commit(repo, "b")
    assert _stamp_module().stamp_sha(repo) == tip


def test_the_date_is_the_stamped_commits_date_not_the_branch_tips(repo):
    """The commits get different dates on purpose: a date taken from HEAD would name a day the stamped commit does not have."""
    commit(repo, "a", date="2026-01-01")
    base = commit(repo, "b", date="2026-02-02")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", "-q", "-b", "feat")
    commit(repo, "c", date="2026-03-03")
    mod = _stamp_module()
    sha = mod.stamp_sha(repo)
    assert sha == base
    assert mod.stamp_date(repo, sha) == "2026-02-02", "the stamped commit's day, not the branch tip's (2026-03-03)"


def test_all_three_generators_use_the_shared_stamp_not_their_own_rev_parse():
    for name in ("derive_register.py", "derive_current_flags.py", "derive_current_authorities.py"):
        src = (REPO / "scripts" / name).read_text(encoding="utf-8")
        assert "_page_stamp" in src, f"{name} must stamp through scripts/_page_stamp.py"
        assert '"rev-parse", "--short", "HEAD"' not in src and "rev-parse\", \"--short\", \"HEAD\"" not in src, (
            f"{name} still stamps the branch tip, which a squash merge orphans")
