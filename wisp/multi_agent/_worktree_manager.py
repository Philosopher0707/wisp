"""WorktreeManager — git worktree lifecycle for isolated subagents."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


class WorktreeManager:
    """Create and cleanup git worktrees for subagent isolation."""

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self._worktrees_root = workspace / ".wisp" / "worktrees"

    async def create(self, agent_name: str) -> Path:
        """Create an isolated git worktree."""
        self._worktrees_root.mkdir(parents=True, exist_ok=True)

        short_id = uuid.uuid4().hex[:8]
        ts = int(time.time())
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", agent_name)[:32].strip("-")
        if not safe_name:
            safe_name = "subagent"
        dir_name = f"{safe_name}-{short_id}"
        branch_name = f"wisp-subagent/{safe_name}-{short_id}-{ts}"

        worktree_path = (self._worktrees_root / dir_name).resolve()

        logger.info("Creating worktree: path=%s branch=%s", worktree_path, branch_name)

        proc = await asyncio.create_subprocess_exec(
            "git", "worktree", "add", str(worktree_path), "-b", branch_name,
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"git worktree add failed (exit {proc.returncode}): {err_text}")

        # Sync parent workspace uncommitted tracked changes into the worktree
        diff_proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD",
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout_diff, _ = await diff_proc.communicate()
        if stdout_diff.strip():
            apply_proc = await asyncio.create_subprocess_exec(
                "git", "apply",
                cwd=str(worktree_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await apply_proc.communicate(input=stdout_diff)

        # Sync untracked files from parent workspace
        untracked_proc = await asyncio.create_subprocess_exec(
            "git", "ls-files", "--others", "--exclude-standard",
            cwd=str(self.workspace),
            stdout=asyncio.subprocess.PIPE,
        )
        stdout_untracked, _ = await untracked_proc.communicate()
        if stdout_untracked.strip():
            for rel_file in stdout_untracked.decode("utf-8").splitlines():
                if not rel_file.strip():
                    continue
                src = self.workspace / rel_file
                dst = worktree_path / rel_file
                if src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)

        logger.debug("Worktree created and synced: %s (branch=%s)", worktree_path, branch_name)
        return worktree_path

    async def detect_files_changed(self, worktree_path: Path) -> list[str]:
        """Return list of files changed in the worktree via git diff --name-only.

        More reliable than regex-extracting paths from LLM text output.
        Returns empty list if worktree doesn't exist or has no changes.
        """
        if not worktree_path.exists():
            return []

        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--name-only", "HEAD",
            cwd=str(worktree_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        files = [
            line.strip() for line in stdout.decode("utf-8", errors="replace").splitlines()
            if line.strip()
        ]
        return files

    async def apply_patch(self, patch: str) -> bool:
        """Apply a git patch to the parent workspace.

        Returns True if the patch applied cleanly, False on conflict.
        """
        if not patch.strip():
            return True

        proc = await asyncio.create_subprocess_exec(
            "git", "apply",
            cwd=str(self.workspace),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=patch.encode("utf-8"))

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace").strip()
            logger.warning("git apply failed (exit %d): %s", proc.returncode, err_text)
            return False

        logger.info("Patch applied successfully to %s", self.workspace)
        return True

    async def apply_patches_sequential(self, patches: list[str]) -> dict[str, bool]:
        """Apply multiple patches sequentially. Skips on conflict.

        Returns a dict mapping patch index → success.
        """
        results: dict[str, bool] = {}
        for i, patch in enumerate(patches):
            if not patch.strip():
                results[str(i)] = True
                continue
            ok = await self.apply_patch(patch)
            results[str(i)] = ok
            if not ok:
                logger.warning("Patch %d/%d conflicted — skipping remaining", i + 1, len(patches))
        return results

    async def get_patch(self, worktree_path: Path) -> str:
        """Capture all uncommitted changes (tracked & untracked) in the worktree as a patch string."""
        if not worktree_path.exists():
            return ""
        
        # Add all untracked files to index so they appear in diff
        add_proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=str(worktree_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await add_proc.communicate()

        # Generate a patch of all changes compared to HEAD
        diff_proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD",
            cwd=str(worktree_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await diff_proc.communicate()
        
        # Reset the index to not leave the worktree in an awkward state if not destroyed
        reset_proc = await asyncio.create_subprocess_exec(
            "git", "reset", "HEAD",
            cwd=str(worktree_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await reset_proc.communicate()

        return stdout.decode("utf-8", errors="replace")

    async def cleanup(self, worktree_path: Path) -> None:
        """Remove a worktree and delete the associated branch."""
        logger.info("Cleaning up worktree: %s", worktree_path)

        # Derive branch name from git metadata
        git_dir = self.workspace / ".git" / "worktrees"
        branch_name: str | None = None
        try:
            for entry in git_dir.iterdir():
                if entry.is_dir() and worktree_path.name in str(entry.name):
                    head_file = entry / "HEAD"
                    if head_file.exists():
                        head_text = head_file.read_text().strip()
                        if head_text.startswith("ref: "):
                            branch_name = head_text.replace("ref: refs/heads/", "")
                            break
        except Exception as exc:
            logger.debug("Could not determine branch for %s: %s", worktree_path, exc)

        max_attempts = 5
        backoff = 0.05  # Start with 50ms
        for attempt in range(max_attempts):
            proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "remove", str(worktree_path), "--force",
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                break

            err_text = stderr.decode("utf-8", errors="replace").strip()
            logger.warning(
                "git worktree remove failed (attempt %d/%d, exit %d): %s",
                attempt + 1, max_attempts, proc.returncode, err_text
            )

            if attempt < max_attempts - 1:
                await asyncio.sleep(backoff)
                backoff *= 2
        else:
            # Fallback manual removal
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)
                logger.debug("Manually removed worktree directory: %s", worktree_path)

        if branch_name and branch_name.startswith("wisp-subagent/"):
            try:
                branch_proc = await asyncio.create_subprocess_exec(
                    "git", "branch", "-D", branch_name,
                    cwd=str(self.workspace),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await branch_proc.communicate()
            except Exception as exc:
                logger.debug("Branch delete failed (non-critical): %s", exc)

        try:
            prune_proc = await asyncio.create_subprocess_exec(
                "git", "worktree", "prune",
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await prune_proc.communicate()
        except Exception as exc:
            logger.debug("Worktree prune failed (non-critical): %s", exc)

        logger.debug("Worktree cleanup complete: %s", worktree_path)
