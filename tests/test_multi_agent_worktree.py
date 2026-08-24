"""Unit tests for WorktreeManager.

Mocks git subprocess calls to avoid needing a real git repository.
"""

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
        # Mock _check_git_repo to return True so we reach the worktree add step
        with patch.object(mgr, "_check_git_repo", return_value=True):
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


# ═══════════════════════════════════════════════════════════════════
# Concurrent same-repo worktree isolation — proven with real git
# ═══════════════════════════════════════════════════════════════════


class TestConcurrentWorktreeIsolation:
    """N writers in N worktrees: no cross-contamination, no trampling."""

    @pytest.fixture
    def git_repo(self, tmp_path):
        """A real git repo with one tracked file."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        def git(*args, **kw):
            subprocess.run(["git", *args], cwd=repo, check=True,
                           capture_output=True, **kw)
        git("init", "-q")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        (repo / "shared.txt").write_text("base\n")
        git("add", ".")
        git("commit", "-qm", "base")
        return repo

    @pytest.mark.asyncio
    async def test_parallel_writers_never_see_each_other(self, git_repo):
        import asyncio

        from wisp.multi_agent._worktree_manager import WorktreeManager

        mgr = WorktreeManager(git_repo)
        n = 4

        async def one_writer(i: int) -> dict:
            wt = await mgr.create(f"writer-{i}")
            try:
                # Distinct files only — non-conflicting by construction.
                (wt / f"writer_{i}.txt").write_text(f"work of {i}\n")
                return {"i": i, "patch": await mgr.get_patch(wt)}
            finally:
                await mgr.cleanup(wt)

        results = await asyncio.gather(*(one_writer(i) for i in range(n)))

        # Parent workspace untouched until patches are applied.
        assert (git_repo / "shared.txt").read_text() == "base\n"
        for i in range(n):
            assert not (git_repo / f"writer_{i}.txt").exists()

        applied = await mgr.apply_patches_sequential(
            [r["patch"] for r in results]
        )
        assert all(applied.values()), applied
        for i in range(n):
            assert (git_repo / f"writer_{i}.txt").read_text() == f"work of {i}\n"

    @pytest.mark.asyncio
    async def test_conflicting_shared_edits_reported_not_corrupted(
        self, git_repo
    ):
        import asyncio

        from wisp.multi_agent._worktree_manager import WorktreeManager

        mgr = WorktreeManager(git_repo)

        async def one_writer(i: int) -> str:
            wt = await mgr.create(f"writer-{i}")
            try:
                (wt / "shared.txt").write_text(f"edited by {i}\n")
                return await mgr.get_patch(wt)
            finally:
                await mgr.cleanup(wt)

        patches = await asyncio.gather(*(one_writer(i) for i in range(3)))
        applied = await mgr.apply_patches_sequential(patches)

        # Same-file conflicts are expected: a conflicted patch reports
        # False and REVERTS to base, so later patches land cleanly against
        # restored state. Invariant: final content is exactly one writer's
        # edit (or base) — never markers, never a mixed state.
        final = (git_repo / "shared.txt").read_text().strip()
        assert final in {f"edited by {i}" for i in range(3)} | {"base"}, final
        assert "<<<" not in final and ">>>>" not in final

        import subprocess
        dirty = subprocess.run(
            ["git", "diff", "--check"], cwd=git_repo,
            capture_output=True, text=True,
        )
        assert not dirty.stdout, f"conflict debris in tree: {dirty.stdout}"
        rej = list(git_repo.rglob("*.rej"))
        assert not rej, f".rej debris: {rej}"
