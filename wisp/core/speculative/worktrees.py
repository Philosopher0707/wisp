"""WorktreeManager adapter for speculative search (GH#29).

No parallel pool is built here: :class:`WorktreeManager` already owns
isolated worktree lifecycle (create / patch / cleanup / orphan reaping).
This module adapts one manager-issued worktree to the engine's
:class:`WorktreeBackend` protocol, plus a per-trial subprocess oracle.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Oracle-emitted artifacts that must never enter a winner patch: build
# caches, bytecode, and test-runner debris would otherwise poison the
# diff with un-appliable binary deltas (live evidence: __pycache__ .pyc
# broke git apply with "cannot apply binary patch without full index").
# Source files are never matched by these patterns.
ORACLE_ARTIFACT_EXCLUDES: tuple[str, ...] = (
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".hypothesis/",
    ".coverage*",
    "coverage.xml",
    "*.egg-info/",
)


class WorktreeManagerBackend:
    """One trial sandbox backed by an existing WorktreeManager worktree."""

    def __init__(self, manager: Any, path: Path) -> None:
        self._manager = manager
        self._path = path

    @property
    def root(self) -> Path:
        return self._path

    async def write_files(self, writes: dict[str, str]) -> None:
        for rel, content in writes.items():
            target = self._path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    async def run_tests(self, command: list[str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self._path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await proc.communicate()
        except Exception as exc:
            return 1, f"oracle transport failed: {exc}"
        text = (out or b"").decode("utf-8", errors="replace")
        return proc.returncode or 0, text

    async def get_patch(self) -> str:
        return await self._manager.get_patch(
            self._path, exclude=list(ORACLE_ARTIFACT_EXCLUDES))

    async def cleanup(self) -> None:
        await self._manager.cleanup(self._path)


async def new_manager_backend(manager: Any, agent_name: str = "speculative") -> WorktreeManagerBackend:
    """Issue one isolated worktree from the shared manager."""
    path = await manager.create(agent_name)
    return WorktreeManagerBackend(manager, Path(path))


__all__ = ["WorktreeManagerBackend", "new_manager_backend"]
