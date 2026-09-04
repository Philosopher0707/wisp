"""Run record + lifecycle state machine (M3 durable runtime).

The 8-state machine extends the M1a produced vocabulary (`RunStatus`):
running→RUNNING, completed→SUCCEEDED, failed→FAILED, cancelled→CANCELLED.
Terminal states are immutable — enforced here and in SQLiteRunStore.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RunState(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED})

LEGAL_TRANSITIONS: dict[RunState, tuple[RunState, ...]] = {
    RunState.QUEUED: (RunState.PLANNING, RunState.RUNNING, RunState.CANCELLED),
    RunState.PLANNING: (RunState.RUNNING, RunState.CANCELLED),
    RunState.RUNNING: (RunState.AWAITING_APPROVAL, RunState.PAUSED,
                       RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED),
    RunState.AWAITING_APPROVAL: (RunState.RUNNING, RunState.PAUSED, RunState.CANCELLED),
    RunState.PAUSED: (RunState.RUNNING, RunState.CANCELLED),
    RunState.SUCCEEDED: (),
    RunState.FAILED: (),
    RunState.CANCELLED: (),
}


def is_legal(from_state: RunState | str, to_state: RunState | str) -> bool:
    return RunState(to_state) in LEGAL_TRANSITIONS[RunState(from_state)]


@dataclass(frozen=True)
class RunRecord:
    """One durable run. Mirrors `background_runs` columns plus lease and
    idempotency fields (added by additive migration)."""

    run_id: str
    prompt: str = ""
    model: str = "unknown"
    workspace: str = "."
    status: RunState = RunState.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    content: str = ""
    tool_calls: tuple = ()
    files_changed: tuple[str, ...] = ()
    error: str | None = None
    iterations: int = 0
    lease_owner: str = ""
    lease_expires: float = 0.0
    idempotency_key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "prompt": self.prompt, "model": self.model,
                "workspace": self.workspace, "status": self.status.value,
                "created_at": self.created_at, "started_at": self.started_at,
                "finished_at": self.finished_at, "content": self.content,
                "tool_calls": list(self.tool_calls),
                "files_changed": list(self.files_changed), "error": self.error,
                "iterations": self.iterations, "lease_owner": self.lease_owner,
                "lease_expires": self.lease_expires,
                "idempotency_key": self.idempotency_key}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunRecord":
        return cls(run_id=d["run_id"], prompt=d.get("prompt", ""),
                   model=d.get("model", "unknown"),
                   workspace=d.get("workspace", "."),
                   status=RunState(d.get("status", "queued")),
                   created_at=d.get("created_at", 0.0),
                   started_at=d.get("started_at"),
                   finished_at=d.get("finished_at"),
                   content=d.get("content", ""),
                   tool_calls=tuple(d.get("tool_calls") or ()),
                   files_changed=tuple(d.get("files_changed") or ()),
                   error=d.get("error"), iterations=d.get("iterations", 0),
                   lease_owner=d.get("lease_owner", ""),
                   lease_expires=d.get("lease_expires", 0.0),
                   idempotency_key=d.get("idempotency_key", ""))
