"""Speculative multi-candidate search over isolated worktrees."""

from wisp.core.speculative.search import (
    Candidate,
    SearchResult,
    SpeculativeSearchEngine,
    TrialResult,
    WorktreeBackend,
    diff_delta,
)
from wisp.core.speculative.worktrees import (
    ORACLE_ARTIFACT_EXCLUDES,
    WorktreeManagerBackend,
    new_manager_backend,
)

__all__ = [
    "Candidate",
    "SearchResult",
    "SpeculativeSearchEngine",
    "TrialResult",
    "WorktreeBackend",
    "WorktreeManagerBackend",
    "diff_delta",
    "new_manager_backend",
    "ORACLE_ARTIFACT_EXCLUDES",
]
