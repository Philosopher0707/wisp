"""Hardened subagent orchestration seam (pool + protocol + coordinator).

Depends only on stdlib + pydantic. Execution backends are injected, so
this package is fully testable without LLM providers.
"""

from __future__ import annotations

from wisp.core.subagent.coordinator import Coordinator, CoordinatorConfig, Reducer, tools_for_role
from wisp.core.subagent.pool import BoundedWorkerPool
from wisp.core.subagent.protocol import (
    ConflictPair,
    ContextChunk,
    ExecutionPolicy,
    Finding,
    PatchProposal,
    ReducedResult,
    SubagentResult,
    TaskFrame,
    TaskStatus,
    TelemetrySink,
    TokenUsage,
    WorkerEvent,
    patches_conflict,
)

__all__ = [
    "BoundedWorkerPool",
    "ConflictPair",
    "ContextChunk",
    "Coordinator",
    "CoordinatorConfig",
    "ExecutionPolicy",
    "Finding",
    "PatchProposal",
    "Reducer",
    "ReducedResult",
    "SubagentResult",
    "TaskFrame",
    "TaskStatus",
    "TelemetrySink",
    "TokenUsage",
    "WorkerEvent",
    "patches_conflict",
    "tools_for_role",
]
