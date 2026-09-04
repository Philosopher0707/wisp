"""Cyclic execution graph: explicit phases with oscillation + ceiling traps."""

from __future__ import annotations

from wisp.core.graph.loop import (
    DEFAULT_MAX_ITERATIONS,
    ExecutionGraph,
    FailureArtifact,
    GraphOutcome,
    OscillationTrap,
    PhaseResult,
    Snapshot,
    diff_hash,
)
from wisp.core.graph.phases import Phase, is_terminal, next_phase

__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "ExecutionGraph",
    "FailureArtifact",
    "GraphOutcome",
    "OscillationTrap",
    "Phase",
    "PhaseResult",
    "Snapshot",
    "diff_hash",
    "is_terminal",
    "next_phase",
]
