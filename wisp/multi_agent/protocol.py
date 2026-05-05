"""Typed communication protocol for multi-agent swarm.

Agents communicate by emitting events to a shared MessageBus.
All events are immutable dataclasses with strict typing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventType(Enum):
    """Kinds of events agents can emit or consume."""

    TASK_ASSIGNED = auto()      # Orchestrator → Agent: "do this"
    TASK_RESULT = auto()        # Agent → Orchestrator: "here's what I did"
    TASK_FAILED = auto()        # Agent → Orchestrator: "I failed"
    AGENT_STARTED = auto()      # Agent → Bus: "I'm alive"
    AGENT_HEARTBEAT = auto()    # Agent → Bus: "still working"
    AGENT_STOPPED = auto()      # Agent → Bus: "I'm done / crashed"
    FILE_CLAIMED = auto()       # Agent → Bus: "I'm editing foo.py"
    FILE_RELEASED = auto()      # Agent → Bus: "Done with foo.py"
    BROADCAST = auto()          # Any → All: general message
    QUERY = auto()              # Any → Any: "what's the status of X?"
    QUERY_RESPONSE = auto()     # Any → Any: response to query


@dataclass(frozen=True)
class AgentEvent:
    """A single event on the message bus."""

    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(default_factory=_now_iso)
    event_type: EventType = EventType.BROADCAST
    source_agent: str = ""           # Agent ID that emitted this
    target_agent: Optional[str] = None  # None = broadcast
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.name,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AgentEvent:
        return cls(
            event_id=d["event_id"],
            timestamp=d["timestamp"],
            event_type=EventType[d["event_type"]],
            source_agent=d["source_agent"],
            target_agent=d.get("target_agent"),
            payload=d.get("payload", {}),
        )


@dataclass(frozen=True)
class TaskAssignment:
    """Orchestrator assigns a task to an agent."""

    task_id: str
    description: str
    expected_output: str = ""
    max_iterations: int = 10
    timeout_seconds: int = 120
    tools: list[str] = field(default_factory=lambda: ["all"])
    context: dict[str, Any] = field(default_factory=dict)

    def to_event(self, source: str, target: str) -> AgentEvent:
        return AgentEvent(
            event_type=EventType.TASK_ASSIGNED,
            source_agent=source,
            target_agent=target,
            payload={
                "task_id": self.task_id,
                "description": self.description,
                "expected_output": self.expected_output,
                "max_iterations": self.max_iterations,
                "timeout_seconds": self.timeout_seconds,
                "tools": self.tools,
                "context": self.context,
            },
        )

    @classmethod
    def from_event(cls, event: AgentEvent) -> TaskAssignment:
        p = event.payload
        return cls(
            task_id=p["task_id"],
            description=p["description"],
            expected_output=p.get("expected_output", ""),
            max_iterations=p.get("max_iterations", 10),
            timeout_seconds=p.get("timeout_seconds", 120),
            tools=p.get("tools", ["all"]),
            context=p.get("context", {}),
        )


@dataclass(frozen=True)
class TaskResult:
    """An agent reports completion (or failure) of a task."""

    task_id: str
    success: bool
    output: str
    files_changed: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    iterations_used: int = 0
    error: Optional[str] = None

    def to_event(self, source: str, target: Optional[str] = None) -> AgentEvent:
        return AgentEvent(
            event_type=EventType.TASK_RESULT if self.success else EventType.TASK_FAILED,
            source_agent=source,
            target_agent=target,
            payload={
                "task_id": self.task_id,
                "success": self.success,
                "output": self.output,
                "files_changed": self.files_changed,
                "elapsed_seconds": self.elapsed_seconds,
                "iterations_used": self.iterations_used,
                "error": self.error,
            },
        )

    @classmethod
    def from_event(cls, event: AgentEvent) -> TaskResult:
        p = event.payload
        return cls(
            task_id=p["task_id"],
            success=p.get("success", False),
            output=p.get("output", ""),
            files_changed=p.get("files_changed", []),
            elapsed_seconds=p.get("elapsed_seconds", 0.0),
            iterations_used=p.get("iterations_used", 0),
            error=p.get("error"),
        )
