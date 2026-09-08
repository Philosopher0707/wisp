"""Speculative multi-candidate search (GH#29).

Single-path attempts die on the first bad hypothesis. This engine fans
$K$ candidate patches out across isolated worktree backends, evaluates
each against a test oracle in parallel, and applies the winner back to
the primary workspace.

Deliberate seams (everything injectable, no model calls inside):

- candidates are *injected* (full-file post-images), never generated
  here — diverse sampling temperatures belong to the core trigger
  (follow-up), not to the oracle;
- the worktree backend is a Protocol — production adapts the existing
  ``WorktreeManager`` (no parallel pool built), tests use a fake;
- the test oracle is one command run per worktree with a per-trial
  timeout; losers are always cleaned up, even on crash.

Winner selection is deterministic: passing beats failing; among passing
trials the smallest unified-diff delta wins; ties break by wall-clock.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_MAX_CANDIDATES = 3
DEFAULT_TRIAL_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class Candidate:
    """One solution hypothesis: full-file post-images to trial."""

    key: str
    writes: dict[str, str]


@dataclass(frozen=True)
class TrialResult:
    """Oracle outcome for one candidate."""

    candidate_key: str
    passed: bool
    duration_s: float
    output_tail: str
    patch: str = ""
    diff_delta: int = 0  # added + removed lines in the winner patch


@dataclass(frozen=True)
class SearchResult:
    """Full search outcome."""

    winner: TrialResult | None
    trials: tuple[TrialResult, ...] = ()
    applied: bool = False
    apply_error: str = ""


class WorktreeBackend(Protocol):
    """Isolated trial sandbox. One instance per candidate, never shared."""

    @property
    def root(self) -> Path: ...

    async def write_files(self, writes: dict[str, str]) -> None: ...

    async def run_tests(self, command: list[str]) -> tuple[int, str]:
        """Run the oracle command; return (exit_code, combined_output)."""
        ...

    async def get_patch(self) -> str: ...

    async def cleanup(self) -> None: ...


def diff_delta(patch: str) -> int:
    """Count added + removed lines in a unified diff (headers excluded)."""
    delta = 0
    for line in (patch or "").splitlines():
        if line.startswith(("+++", "---", "diff ", "index ", "@@", "Binary ")):
            continue
        if line.startswith(("+", "-")):
            delta += 1
    return delta


class SpeculativeSearchEngine:
    """Fan out candidates across isolated backends, keep the winner."""

    def __init__(
        self,
        new_backend: Callable[[], Awaitable[WorktreeBackend]],
        test_command: list[str],
        apply_patch: Callable[[str], Awaitable[bool]],
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        trial_timeout_s: float = DEFAULT_TRIAL_TIMEOUT_S,
        output_tail_chars: int = 2000,
    ) -> None:
        if max_candidates < 1:
            raise ValueError("max_candidates must be >= 1")
        if trial_timeout_s <= 0:
            raise ValueError("trial_timeout_s must be positive")
        self._new_backend = new_backend
        self._test_command = list(test_command)
        self._apply_patch = apply_patch
        self._max_candidates = max_candidates
        self._trial_timeout_s = trial_timeout_s
        self._tail_chars = output_tail_chars

    async def search(self, candidates: list[Candidate]) -> SearchResult:
        """Trial candidates in parallel; apply the deterministic winner."""
        cohort = list(candidates)[: self._max_candidates]
        if not cohort:
            return SearchResult(winner=None, trials=(), applied=False)
        trials = await asyncio.gather(
            *(self._trial(c) for c in cohort))
        passing = [t for t in trials if t.passed]
        if not passing:
            logger.info("speculative search: %d trials, none passed", len(trials))
            return SearchResult(winner=None, trials=tuple(trials), applied=False)
        # Deterministic: smallest diff first, then fastest. Sort is stable
        # so full ties resolve to candidate order.
        passing.sort(key=lambda t: (t.diff_delta, t.duration_s))
        best = passing[0]
        ok, error = await self._apply_winner(best)
        logger.info("speculative search: winner=%s applied=%s (%d trials)",
                    best.candidate_key, ok, len(trials))
        return SearchResult(winner=best, trials=tuple(trials),
                            applied=ok, apply_error=error)

    async def _trial(self, candidate: Candidate) -> TrialResult:
        start = time.monotonic()
        backend = await self._new_backend()
        try:
            try:
                async with asyncio.timeout(self._trial_timeout_s):
                    await backend.write_files(dict(candidate.writes))
                    exit_code, output = await backend.run_tests(self._test_command)
                    patch = await backend.get_patch() if exit_code == 0 else ""
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("speculative trial %s timed out", candidate.key)
                return TrialResult(candidate.key, False,
                                   time.monotonic() - start, "trial timed out")
            except Exception as exc:
                logger.warning("speculative trial %s crashed: %s", candidate.key, exc)
                return TrialResult(candidate.key, False,
                                   time.monotonic() - start, f"trial crashed: {exc}")
            duration = time.monotonic() - start
            tail = (output or "")[-self._tail_chars:]
            return TrialResult(candidate.key, exit_code == 0, duration, tail,
                               patch, diff_delta(patch))
        finally:
            try:
                await backend.cleanup()
            except Exception:
                logger.debug("trial cleanup failed for %s", candidate.key, exc_info=True)

    async def _apply_winner(self, best: TrialResult) -> tuple[bool, str]:
        if not best.patch.strip():
            return False, "winner produced an empty patch"
        try:
            ok = bool(await self._apply_patch(best.patch))
        except Exception as exc:
            return False, f"apply failed: {exc}"
        if not ok:
            return False, "patch did not apply cleanly to parent workspace"
        return True, ""


__all__ = [
    "Candidate",
    "SearchResult",
    "SpeculativeSearchEngine",
    "TrialResult",
    "WorktreeBackend",
    "diff_delta",
]
