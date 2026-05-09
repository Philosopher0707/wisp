"""Unified task and result types for all multi-agent systems in Wisp.

This is the single source of truth for SubagentTask, SubagentResult, and
OrchestratorEvent. All other modules (protocol.py, subagent.py, subagent_runner.py)
alias their types here.
"""

from __future__ import annotations

import uuid
import time as _time_module
from dataclasses import dataclass, field
from typing import Any, Optional

from .protocol import EventType as _EventType  # lazy to avoid circular import, resolve below


def _new_id() -> str:
    return str(uuid.uuid4())[:8]


def _now_ts() -> float:
    return _time_module.monotonic()


# ── Task ──────────────────────────────────────────────────────────────────


@dataclass
class SubagentTask:
    """A unit of work assigned to a single agent."""

    id: str = field(default_factory=_new_id)
    role: str = ""
    description: str = ""
    expected_output: str = ""
    max_iterations: int = 10
    timeout_seconds: int = 120
    tools: list[str] = field(default_factory=lambda: ["all"])
    context: dict[str, Any] = field(default_factory=dict)

    # ── subagent.py compat ──
    output_format: str = "text"
    model: Optional[str] = None
    workspace: Optional[str] = None
    system_prompt_extra: str = ""
    auto_approve: bool = True
    max_output_chars: int = 8000

    # ── subagent_runner.py compat ──
    name: str = ""
    prompt: str = ""
    system_prompt: str = ""
    worktree_isolated: bool = True
    context_files: list[str] = field(default_factory=list)


# ── Result ─────────────────────────────────────────────────────────────────


@dataclass
class SubagentResult:
    """Structured output from a completed (or failed/timed-out) agent task."""

    task_id: str = ""
    success: bool = False
    output: str = ""
    error: Optional[str] = None
    files_changed: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    iterations_used: int = 0
    retry_count: int = 0

    # ── protocol.py compat (TaskResult) ──
    # task_id maps to task_id
    # success  → success
    # output   → output
    # files_changed → files_changed
    # elapsed_seconds → elapsed_seconds
    # iterations_used → iterations_used
    # error → error

    # ── subagent.py compat ──
    messages: list[dict] = field(default_factory=list)
    timed_out: bool = False
    hit_iteration_limit: bool = False

    # ── subagent_runner.py compat ──
    spec: Any = None  # SubagentSpec, avoids circular import
    tool_calls: list[dict] = field(default_factory=list)
    duration_seconds: float = 0.0
    session_id: str = ""


# ── Orchestrator Event (for streaming) ────────────────────────────────────


class EventKind:
    """Orchestrator event type constants."""
    PLANNING = "planning"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_RETRY = "task_retry"
    DONE = "done"


@dataclass
class OrchestratorEvent:
    """Streaming progress event emitted by the orchestrator.

    Consumers (CLI, WebSocket, HTTP polling) receive these via the
    progress_callback and convert them to their native formats.
    """

    task_id: str = ""
    event_type: str = EventKind.TASK_STARTED
    payload: dict[str, Any] = field(default_factory=dict)

    def to_ws_message(self) -> dict[str, Any] | None:
        """Convert to a WebSocket message dict, or None if not needed."""
        kind = self.event_type
        p = self.payload

        if kind == EventKind.TASK_STARTED:
            return {
                "type": "subagent_start",
                "subagent_id": self.task_id,
                "name": p.get("role", p.get("name", "")),
                "description": p.get("description", ""),
            }
        elif kind == EventKind.TASK_PROGRESS:
            return {
                "type": "subagent_progress",
                "subagent_id": self.task_id,
                "progress": p.get("progress", ""),
            }
        elif kind == EventKind.TASK_COMPLETED:
            return {
                "type": "subagent_complete",
                "subagent_id": self.task_id,
                "files_changed": p.get("files_changed", []),
                "duration_ms": int(p.get("elapsed", 0) * 1000),
            }
        elif kind == EventKind.TASK_FAILED:
            return {
                "type": "subagent_fail",
                "subagent_id": self.task_id,
                "error": p.get("error", ""),
            }
        return None
