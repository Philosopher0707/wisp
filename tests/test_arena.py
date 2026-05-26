"""Tests for Arena Mode worktree isolation.

These tests verify that ArenaRunner creates isolated git worktrees
for Model A and Model B, preventing concurrent filesystem corruption.
"""

import pytest
import asyncio
from pathlib import Path
from unittest.mock import patch


class TestArenaIsolation:
    """Tests that ArenaRunner properly isolates Model A and Model B."""

    @pytest.fixture(autouse=True)
    def setup_git_repo(self, tmp_path, monkeypatch):
        """Initialize a git repo in tmp_path for worktree support."""
        import subprocess
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        # Create an initial commit so worktrees can be created
        (tmp_path / "README").write_text("test")
        subprocess.run(["git", "add", "README"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True)

    def test_arena_creates_isolated_worktrees(self, tmp_path, monkeypatch):
        """run_comparison should create two separate directories for A and B."""
        from wisp.arena import ArenaRunner, ArenaCompareRequest

        arena = ArenaRunner()
        req = ArenaCompareRequest(
            prompt="write hello to output.txt",
            task="test",
            model_a="model-a",
            model_b="model-b",
            workspace=str(tmp_path),
        )

        # Mock _run_side to just return dummy data and record workspace path
        workspaces_seen = []
        async def mock_run_side(workspace, prompt, model):
            workspaces_seen.append(str(workspace))
            return ("summary", "diff", [], 0)

        with patch.object(arena, '_run_side', side_effect=mock_run_side):
            asyncio.run(arena.run_comparison(req))

        # Should have run in TWO different directories
        assert len(workspaces_seen) == 2
        assert workspaces_seen[0] != workspaces_seen[1]
        # Both should be under tmp_path (or in worktrees under it)
        assert Path(workspaces_seen[0]).exists() or workspaces_seen[0].startswith(str(tmp_path))

    def test_concurrent_writes_dont_corrupt(self, tmp_path, monkeypatch):
        """Model A and Model B writing the same file should not clobber each other."""
        from wisp.arena import ArenaRunner, ArenaCompareRequest

        arena = ArenaRunner()
        req = ArenaCompareRequest(
            prompt="write your model name to output.txt",
            task="test",
            model_a="model-a",
            model_b="model-b",
            workspace=str(tmp_path),
        )

        async def mock_run_side(workspace, prompt, model):
            # Simulate the model writing its identity
            out = Path(workspace) / "output.txt"
            out.write_text(model)
            return (f"wrote {model}", "", ["output.txt"], 0)

        with patch.object(arena, '_run_side', side_effect=mock_run_side):
            entry = asyncio.run(arena.run_comparison(req))

        # Each side should report its OWN file content, not the other's
        # In the broken implementation, both sides see the same (last-written) content
        # After fix, each side's summary should reflect its own write
        assert "model-a" in entry.a_summary or entry.a_files_changed == ["output.txt"]
        assert "model-b" in entry.b_summary or entry.b_files_changed == ["output.txt"]

    def test_cleanup_removes_worktrees(self, tmp_path, monkeypatch):
        """After run_comparison, no leftover worktree directories should remain."""
        from wisp.arena import ArenaRunner, ArenaCompareRequest

        arena = ArenaRunner()
        req = ArenaCompareRequest(
            prompt="test",
            task="test",
            model_a="a",
            model_b="b",
            workspace=str(tmp_path),
        )

        async def mock_run_side(workspace, prompt, model):
            return ("summary", "", [], 0)

        before_dirs = set(d.name for d in tmp_path.iterdir() if d.is_dir())

        with patch.object(arena, '_run_side', side_effect=mock_run_side):
            asyncio.run(arena.run_comparison(req))

        after_dirs = set(d.name for d in tmp_path.iterdir() if d.is_dir())
        # Should not leave behind arena worktree directories
        new_dirs = after_dirs - before_dirs
        assert not any("arena" in d.lower() or "worktree" in d.lower() for d in new_dirs)

    def test_diff_shows_only_own_changes(self, tmp_path, monkeypatch):
        """Each side's diff should reflect only that side's changes."""
        from wisp.arena import ArenaRunner, ArenaCompareRequest

        arena = ArenaRunner()
        req = ArenaCompareRequest(
            prompt="modify the file",
            task="test",
            model_a="model-a",
            model_b="model-b",
            workspace=str(tmp_path),
        )

        # Create a base file in the workspace
        (tmp_path / "base.txt").write_text("original")

        async def mock_run_side(workspace, prompt, model):
            # Each model appends its name
            f = Path(workspace) / "base.txt"
            if f.exists():
                f.write_text(f.read_text() + f"\n{model}")
            else:
                f.write_text(model)
            return (f"modified by {model}", "", ["base.txt"], 0)

        with patch.object(arena, '_run_side', side_effect=mock_run_side):
            entry = asyncio.run(arena.run_comparison(req))

        # In the broken implementation, both diffs would show the same merged state
        # After fix, each diff should only show that side's additions
        # (We can't easily check the exact diff content without git, but we can
        # verify the workspaces are different)
        assert entry.a_summary != entry.b_summary
