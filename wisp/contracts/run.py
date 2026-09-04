"""Produced run vocabulary freeze (M1a). The 8-state lifecycle is M1b design;
this module pins only what producers emit today."""
from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum

# Exact wire values from wisp/multi_agent/task.py:243-251 (no rename).
EVENT_KINDS: tuple[str, ...] = ("planning", "task_started", "task_progress",
    "task_completed", "task_failed", "task_retry", "done")


class RunStatus(StrEnum):
    """Background-agent statuses, exactly as produced
    (wisp/multi_agent/background.py)."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Transition:
    """One append-only run-state transition record."""
    run_id: str
    seq: int
    from_state: str
    to_state: str
    reason: str = ""
    timestamp: float = 0.0
    version: int = 1

    def to_dict(self) -> dict:
        return {"run_id": self.run_id, "seq": self.seq,
                "from_state": self.from_state, "to_state": self.to_state,
                "reason": self.reason, "timestamp": self.timestamp,
                "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "Transition":
        known = {"run_id", "seq", "from_state", "to_state", "reason",
                 "timestamp", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown transition fields: {sorted(unknown)}")
        return cls(run_id=d["run_id"], seq=d["seq"],
                   from_state=d["from_state"], to_state=d["to_state"],
                   reason=d.get("reason", ""), timestamp=d.get("timestamp", 0.0),
                   version=d.get("version", 1))
