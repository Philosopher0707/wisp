"""Cyclic execution graph: explicit phases with oscillation + ceiling traps.

Experimental — NOT wired into ``WispAgentCore.turn`` (the live path is the
provider-driven ``_turn_inner`` loop in ``wisp/core/stateless.py``). Keep for
reference; delete if no caller appears (history preserves it).
"""

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
