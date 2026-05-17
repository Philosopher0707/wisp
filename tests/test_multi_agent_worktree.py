"""Unit tests for WorktreeManager.

Mocks git subprocess calls to avoid needing a real git repository.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from wisp.multi_agent._worktree_manager import WorktreeManager


class TestWorktreeManager:

    def test_init(self, tmp_path):
        mgr = WorktreeManager(tmp_path)
        assert mgr.workspace == tmp_path
        assert mgr._worktrees_root == tmp_path / ".wisp" / "worktrees"

    @pytest.mark.asyncio
    async def test_create_success(self, tmp_path):
        mgr = WorktreeManager(tmp_path)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            path = await mgr.create("test-agent")

        assert path.name.startswith("test-agent-")
        assert path.parent == mgr._worktrees_root

    @pytest.mark.asyncio
    async def test_create_failure(self, tmp_path):
        mgr = WorktreeManager(tmp_path)
        mock_proc = AsyncMock()
        mock_proc.returncode = 128
        mock_proc.communicate.return_value = (b"", b"fatal: not a git repo")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="git worktree add failed"):
                await mgr.create("test-agent")

    @pytest.mark.asyncio
    async def test_cleanup_success(self, tmp_path):
        mgr = WorktreeManager(tmp_path)
        worktree_path = tmp_path / ".wisp" / "worktrees" / "test-123"
        worktree_path.mkdir(parents=True)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await mgr.cleanup(worktree_path)

    @pytest.mark.asyncio
    async def test_cleanup_fallback_rmtree(self, tmp_path):
        """If git worktree remove fails, falls back to shutil.rmtree."""
        mgr = WorktreeManager(tmp_path)
        worktree_path = tmp_path / ".wisp" / "worktrees" / "test-123"
        worktree_path.mkdir(parents=True)
        (worktree_path / "file.txt").write_text("test")

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"error")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await mgr.cleanup(worktree_path)

        assert not worktree_path.exists()

    @pytest.mark.asyncio
    async def test_cleanup_nonexistent(self, tmp_path):
        """Cleanup of non-existent path should not raise."""
        mgr = WorktreeManager(tmp_path)
        worktree_path = tmp_path / ".wisp" / "worktrees" / "does-not-exist"

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await mgr.cleanup(worktree_path)
