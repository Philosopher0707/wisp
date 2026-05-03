"""Tests for wisp.git_context — git state extraction."""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from wisp.git_context import (
    GitState,
    _is_git_repo,
    _run_git,
    format_git_context,
    format_git_status_short,
    get_file_diff,
    get_git_state,
    get_workspace_diff,
    has_uncommitted_changes,
)


class TestGitContext:
    """Unit tests for git context extraction."""

    def setup_method(self):
        """Create a temporary directory and initialize a git repo."""
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = self.tmp.name
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=self.ws, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=self.ws, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=self.ws, capture_output=True, check=True,
        )

    def teardown_method(self):
        self.tmp.cleanup()

    def test_is_git_repo(self):
        assert _is_git_repo(self.ws)

    def test_is_git_repo_false(self):
        with tempfile.TemporaryDirectory() as non_git:
            assert not _is_git_repo(non_git)

    def test_run_git_success(self):
        rc, out, err = _run_git(["rev-parse", "--git-dir"], self.ws)
        assert rc == 0
        assert ".git" in out

    def test_run_git_failure(self):
        rc, out, err = _run_git(["not-a-real-command"], self.ws)
        assert rc != 0

    def test_get_git_state_clean(self):
        state = get_git_state(self.ws)
        assert state is not None
        assert state.is_git_repo
        assert state.branch in ("main", "master", "(no commits yet)")
        assert not state.is_dirty
        assert state.untracked_files == []
        assert state.modified_files == []

    def test_get_git_state_untracked(self):
        Path(self.ws, "new_file.py").write_text("print('hello')")
        state = get_git_state(self.ws)
        assert state.is_dirty
        assert "new_file.py" in state.untracked_files

    def test_get_git_state_modified(self):
        f = Path(self.ws, "tracked.py")
        f.write_text("original")
        subprocess.run(["git", "add", "."], cwd=self.ws, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=self.ws, capture_output=True, check=True,
        )
        f.write_text("modified")
        state = get_git_state(self.ws)
        assert state.is_dirty
        assert "tracked.py" in state.modified_files

    def test_get_git_state_staged(self):
        f = Path(self.ws, "staged.py")
        f.write_text("staged content")
        subprocess.run(["git", "add", "."], cwd=self.ws, capture_output=True, check=True)
        state = get_git_state(self.ws)
        assert state.is_dirty
        assert "staged.py" in state.staged_files

    def test_get_git_state_recent_commits(self):
        f = Path(self.ws, "a.py")
        f.write_text("a")
        subprocess.run(["git", "add", "."], cwd=self.ws, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "first commit"],
            cwd=self.ws, capture_output=True, check=True,
        )
        state = get_git_state(self.ws)
        assert len(state.recent_commits) >= 1
        assert "first commit" in state.recent_commits[0]

    def test_format_git_context_clean(self):
        ctx = format_git_context(self.ws)
        assert "## Git Context" in ctx
        assert "Working tree clean" in ctx

    def test_format_git_context_dirty(self):
        Path(self.ws, "dirty.py").write_text("dirty")
        ctx = format_git_context(self.ws)
        assert "untracked" in ctx or "modified" in ctx

    def test_format_git_status_short_clean(self):
        short = format_git_status_short(self.ws)
        assert "clean" in short

    def test_format_git_status_short_dirty(self):
        Path(self.ws, "x.py").write_text("x")
        short = format_git_status_short(self.ws)
        assert "?1" in short or "untracked" in short

    def test_has_uncommitted_changes_true(self):
        f = Path(self.ws, "changed.py")
        f.write_text("original")
        subprocess.run(["git", "add", "."], cwd=self.ws, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.ws, capture_output=True, check=True,
        )
        f.write_text("modified")
        assert has_uncommitted_changes("changed.py", self.ws)

    def test_has_uncommitted_changes_false(self):
        f = Path(self.ws, "clean.py")
        f.write_text("clean")
        subprocess.run(["git", "add", "."], cwd=self.ws, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.ws, capture_output=True, check=True,
        )
        assert not has_uncommitted_changes("clean.py", self.ws)

    def test_get_file_diff(self):
        f = Path(self.ws, "diff_me.py")
        f.write_text("original")
        subprocess.run(["git", "add", "."], cwd=self.ws, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.ws, capture_output=True, check=True,
        )
        f.write_text("modified")
        diff = get_file_diff("diff_me.py", self.ws)
        assert "original" in diff or "modified" in diff

    def test_get_workspace_diff(self):
        f = Path(self.ws, "diff_ws.py")
        f.write_text("original")
        subprocess.run(["git", "add", "."], cwd=self.ws, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.ws, capture_output=True, check=True,
        )
        f.write_text("modified")
        diff = get_workspace_diff(self.ws)
        assert "original" in diff or "modified" in diff

    def test_get_git_state_not_a_repo(self):
        with tempfile.TemporaryDirectory() as non_git:
            state = get_git_state(non_git)
            assert state is None

    def test_git_state_dataclass(self):
        state = GitState(branch="main", is_git_repo=True)
        assert state.branch == "main"
        assert state.is_git_repo
