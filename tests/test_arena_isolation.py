"""Tests for arena mode worktree isolation hardening.

Regression: arena.py used to fall back to tempfile.mkdtemp(dir=workspace)
when git worktree creation failed.  This provided zero isolation — both A and
B sides could see and modify the same files.  After the fix, arena mode
fails closed with a clear error instead.
"""

import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from wisp.arena import ArenaRunner, ArenaCompareRequest


class TestArenaWorktreeIsolation:
    """Arena mode must fail closed when git worktrees are unavailable."""

    @pytest.mark.asyncio
    async def test_arena_fails_closed_on_worktree_error(self, tmp_path):
        """If git worktree creation fails, arena must NOT fall back to temp dirs."""
        arena = ArenaRunner()
        request = ArenaCompareRequest(
            workspace=str(tmp_path),
            prompt="test",
            task="test",
            model_a="model-a",
            model_b="model-b",
        )

        # Simulate git worktree failure (not a git repo)
        with patch(
            "wisp.arena.WorktreeManager.create",
            side_effect=RuntimeError("git worktree add failed"),
        ):
            with pytest.raises(RuntimeError) as exc_info:
                await arena.run_comparison(request)

        msg = str(exc_info.value)
        assert "Arena mode requires a git repository" in msg
        assert "fall back" not in msg.lower()

    @pytest.mark.asyncio
    async def test_arena_cleans_up_worktrees_on_success(self, tmp_path):
        """Successful arena runs must clean up both worktrees."""
        arena = ArenaRunner()
        request = ArenaCompareRequest(
            workspace=str(tmp_path),
            prompt="test",
            task="test",
            model_a="model-a",
            model_b="model-b",
        )

        mock_wt = Path("/fake/worktree")
        cleanup_calls = []

        async def _fake_create(name):
            return mock_wt

        async def _fake_cleanup(wt):
            cleanup_calls.append(wt)

        with patch("wisp.arena.WorktreeManager.create", side_effect=_fake_create):
            with patch("wisp.arena.WorktreeManager.cleanup", side_effect=_fake_cleanup):
                with patch.object(arena, "_run_side", return_value=("", "", [], 0)):
                    with patch.object(arena, "_save_leaderboard"):
                        await arena.run_comparison(request)

        assert len(cleanup_calls) == 2
        assert all(wt == mock_wt for wt in cleanup_calls)
