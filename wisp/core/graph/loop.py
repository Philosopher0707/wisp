"""Cyclic execution graph with diff-hash oscillation trap and ceiling.

The loop drives injected phase handlers (pure dependency injection — no
imports from providers, tools, or transports), so the state machine is
fully testable with fakes:

  - Each iteration runs every phase INIT..REDUCE in order; a handler
    returns a ``PhaseResult`` carrying an optional unified diff produced
    in EXECUTE_SANDBOX.
  - After EXECUTE_SANDBOX the SHA-256 of the diff is compared against the
    two previous diffs: an exact repeat (1-cycle) or a return to the
    diff-before-last (2-cycle) aborts execution, reverts tracked files to
    the pre-run snapshot, and transitions to RECOVER (diagnostic state).
  - ``max_iterations`` (default 25) bounds total phase steps; exhaustion
    terminates with a structured :class:`FailureArtifact`.

Snapshots cover exactly the files the run mutates (caller-supplied read
callback), kept in memory — no temp-dir lifecycle to leak.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from wisp.core.graph.phases import Phase, is_terminal, next_phase

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 25


@dataclass(frozen=True)
class PhaseResult:
    """One phase handler's verdict."""

    ok: bool = True
    diff: str = ""
    detail: str = ""
    workspace_files: dict[str, str] | None = None


@dataclass(frozen=True)
class FailureArtifact:
    """Structured terminal record for post-mortem and UI rendering."""

    reason: str
    phase: Phase
    iterations_used: int
    detail: str = ""
    reverted_files: tuple[str, ...] = ()


@dataclass
class GraphOutcome:
    """Terminal outcome of one ExecutionGraph run."""

    phase: Phase = Phase.TERMINATE
    success: bool = True
    iterations_used: int = 0
    artifact: FailureArtifact | None = None
    history: list[Phase] = field(default_factory=list)


Handler = Callable[[Phase, int], Awaitable[PhaseResult]]
ReadFile = Callable[[str], str | None]
WriteFile = Callable[[str, str], None]


def diff_hash(diff: str) -> str:
    """Stable identity of a produced diff for oscillation detection."""
    return hashlib.sha256(diff.encode("utf-8", errors="ignore")).hexdigest()


class Snapshot:
    """In-memory pre-run copy of caller-tracked files with revert."""

    def __init__(self, read_file: ReadFile, write_file: WriteFile) -> None:
        self._read = read_file
        self._write = write_file
        self._saved: dict[str, str | None] = {}

    def capture(self, paths: list[str]) -> None:
        for path in paths:
            if path not in self._saved:
                try:
                    self._saved[path] = self._read(path)
                except Exception:
                    self._saved[path] = None

    @property
    def tracked(self) -> tuple[str, ...]:
        return tuple(self._saved)

    def revert(self) -> tuple[str, ...]:
        """Restore every tracked file; returns the reverted paths."""
        reverted: list[str] = []
        for path, content in self._saved.items():
            if content is None:
                continue
            try:
                self._write(path, content)
                reverted.append(path)
            except Exception:
                logger.warning("snapshot revert failed for %s", path, exc_info=True)
        return tuple(reverted)


class OscillationTrap:
    """Detects 1-cycle repeats and 2-cycle oscillations of diff hashes."""

    def __init__(self) -> None:
        self._hashes: list[str] = []

    def observe(self, digest: str) -> str | None:
        """Record a diff hash; return 'repeat' | 'cycle' | None."""
        self._hashes.append(digest)
        if len(self._hashes) >= 2 and self._hashes[-1] == self._hashes[-2]:
            return "repeat"
        if len(self._hashes) >= 3 and self._hashes[-1] == self._hashes[-3]:
            return "cycle"
        return None


class ExecutionGraph:
    """Drives the phase cycle over injected handlers with failure traps."""

    def __init__(
        self,
        handlers: dict[Phase, Handler],
        read_file: ReadFile,
        write_file: WriteFile,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> None:
        if max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self._handlers = handlers
        self._snapshot = Snapshot(read_file, write_file)
        self._trap = OscillationTrap()
        self._max_iterations = max_iterations

    async def run(self, mutated_paths: list[str] | None = None) -> GraphOutcome:
        """Execute the cycle to TERMINATE (always — never raises)."""
        outcome = GraphOutcome()
        self._snapshot.capture(list(mutated_paths or []))
        phase = Phase.INIT
        steps = 0
        try:
            while not is_terminal(phase):
                if steps >= self._max_iterations:
                    outcome.success = False
                    outcome.artifact = FailureArtifact(
                        reason="iteration_budget_exhausted",
                        phase=phase,
                        iterations_used=steps,
                        detail=f"ceiling of {self._max_iterations} phase steps reached",
                    )
                    phase = Phase.TERMINATE
                    break
                handler = self._handlers.get(phase)
                if handler is None:
                    phase = next_phase(phase)
                    steps += 1
                    outcome.history.append(phase)
                    continue
                try:
                    result = await handler(phase, steps)
                except Exception as exc:
                    logger.exception("phase %s handler failed", phase.value)
                    outcome.success = False
                    outcome.artifact = FailureArtifact(
                        reason="phase_handler_error", phase=phase,
                        iterations_used=steps, detail=str(exc),
                    )
                    phase = Phase.TERMINATE
                    break
                if result.diff:
                    verdict = self._trap.observe(diff_hash(result.diff))
                    if verdict is not None:
                        reverted = self._snapshot.revert()
                        outcome.success = False
                        outcome.artifact = FailureArtifact(
                            reason=f"oscillation_{verdict}",
                            phase=Phase.RECOVER,
                            iterations_used=steps + 1,
                            detail=(f"diff hash repeated ({verdict}); reverted "
                                    f"{len(reverted)} file(s) to pre-run snapshot"),
                            reverted_files=reverted,
                        )
                        phase = Phase.RECOVER
                        outcome.history.append(phase)
                        steps += 1
                        break
                if not result.ok and phase is Phase.AWAIT_APPROVAL:
                    # Denial parks the run: terminal but successful-noop.
                    phase = Phase.TERMINATE
                    outcome.history.append(phase)
                    steps += 1
                    break
                if not result.ok:
                    outcome.success = False
                    outcome.artifact = FailureArtifact(
                        reason="phase_failed", phase=phase,
                        iterations_used=steps + 1, detail=result.detail,
                    )
                    phase = Phase.TERMINATE
                    outcome.history.append(phase)
                    steps += 1
                    break
                phase = next_phase(phase)
                outcome.history.append(phase)
                steps += 1
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        outcome.phase = phase
        outcome.iterations_used = steps
        return outcome
