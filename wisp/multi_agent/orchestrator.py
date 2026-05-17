"""Async swarm orchestrator — backward-compatible re-export module.

This module re-exports SwarmOrchestrator, SubagentOrchestrator, and
SwarmResult from their new locations for backward compatibility.

New code should import directly from the submodules:
    from wisp.multi_agent.swarm import SwarmOrchestrator, SwarmResult
    from wisp.multi_agent.subagent_orchestrator import SubagentOrchestrator
"""

from __future__ import annotations

from .swarm import SwarmOrchestrator, SwarmResult
from .subagent_orchestrator import SubagentOrchestrator, _run_subagent_worker

__all__ = [
    "SwarmOrchestrator",
    "SwarmResult",
    "SubagentOrchestrator",
    "_run_subagent_worker",
]
