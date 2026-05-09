"""Event system for Wisp SDK — structured events emitted by the agent core.

All I/O (printing, logging, WebSocket pushes) is handled by transports that
subscribe to these events. The core itself is pure logic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class AgentEvent:
    """A single event emitted by the agent during a turn.

    Attributes:
        type: Event category — one of the TYPE_* constants below.
        data: Payload dict (structure depends on type).
        timestamp: Monotonic timestamp for ordering and latency tracking.
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)

    # ── Convenience accessors ──────────────────────────────────────

    @property
    def text(self) -> str:
        """Shortcut for content/thinking events."""
        return self.data.get("text", "")

    @property
    def tool_name(self) -> str:
        """Shortcut for tool_call / tool_result events."""
        return self.data.get("name", "")

    @property
    def is_final(self) -> bool:
        """True if this event signals the end of a turn."""
        return self.type in (TYPE_DONE, TYPE_ERROR)

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict.

        Useful for custom transports that need to send events over
        WebSocket, HTTP, or other protocols.
        """
        return {
            "type": self.type,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AgentEvent:
        """Deserialize from a dict (round-trips with to_dict)."""
        return cls(
            type=data["type"],
            data=data.get("data", {}),
            timestamp=data.get("timestamp", 0.0),
        )


# ── Event type constants ─────────────────────────────────────────────

TYPE_THINKING = "thinking"
TYPE_TOOL_CALL = "tool_call"
TYPE_TOOL_RESULT = "tool_result"
TYPE_CONTENT = "content"
TYPE_ERROR = "error"
TYPE_DONE = "done"
TYPE_SYSTEM = "system"          # meta: model changed, session compacted, etc.
TYPE_APPROVAL_REQUEST = "approval_request"  # transport should prompt user
TYPE_CHECKPOINT_CREATED = "checkpoint_created"
TYPE_STEERING_PAUSED = "steering_paused"
TYPE_STEERING_INJECT = "steering_inject"
TYPE_STEERING_RESUMED = "steering_resumed"

# Human-readable descriptions
_EVENT_DESCRIPTIONS: dict[str, str] = {
    TYPE_THINKING: "Model reasoning trace",
    TYPE_TOOL_CALL: "Tool invocation",
    TYPE_TOOL_RESULT: "Tool execution result",
    TYPE_CONTENT: "Assistant text response",
    TYPE_ERROR: "Error occurred",
    TYPE_DONE: "Turn complete",
    TYPE_SYSTEM: "System notification",
    TYPE_APPROVAL_REQUEST: "User approval required",
}


def describe_event_type(event_type: str) -> str:
    """Return a human description for an event type."""
    return _EVENT_DESCRIPTIONS.get(event_type, "Unknown event")


# ── Event builders (convenience factories) ─────────────────────────

def thinking(text: str) -> AgentEvent:
    return AgentEvent(TYPE_THINKING, {"text": text})


def tool_call(name: str, args: dict[str, Any]) -> AgentEvent:
    return AgentEvent(TYPE_TOOL_CALL, {"name": name, "arguments": args})


def tool_result(name: str, result: str, duration_ms: Optional[float] = None) -> AgentEvent:
    payload: dict[str, Any] = {"name": name, "result": result}
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    return AgentEvent(TYPE_TOOL_RESULT, payload)


def content(text: str) -> AgentEvent:
    return AgentEvent(TYPE_CONTENT, {"text": text})


def error(message: str, recoverable: bool = True) -> AgentEvent:
    return AgentEvent(TYPE_ERROR, {"message": message, "recoverable": recoverable})


def done(session_id: str, turns: int = 0, summary: str = "") -> AgentEvent:
    return AgentEvent(TYPE_DONE, {"session_id": session_id, "turns": turns, "summary": summary})


def system(message: str, level: str = "info") -> AgentEvent:
    return AgentEvent(TYPE_SYSTEM, {"message": message, "level": level})


def checkpoint_created(checkpoint_id: str, description: str,
                       tool_name: str = "", file_count: int = 0) -> AgentEvent:
    return AgentEvent(TYPE_CHECKPOINT_CREATED, {
        "checkpoint_id": checkpoint_id,
        "description": description,
        "tool_name": tool_name,
        "file_count": file_count,
    })


def steering_paused(reason: str = "User paused") -> AgentEvent:
    return AgentEvent(TYPE_STEERING_PAUSED, {"reason": reason})


def steering_resumed() -> AgentEvent:
    return AgentEvent(TYPE_STEERING_RESUMED, {})


def steering_feedback(text: str) -> AgentEvent:
    return AgentEvent(TYPE_STEERING_INJECT, {"text": text})


def approval_request(tool_name: str, args: dict[str, Any], reason: str = "") -> AgentEvent:
    return AgentEvent(TYPE_APPROVAL_REQUEST, {"name": tool_name, "arguments": args, "reason": reason})


# ── Simple in-memory event bus (for sync transports) ────────────────

class EventBus:
    """Synchronous pub/sub bus for AgentEvents.

    Used by CLI and other transports that consume events in real time.
    """

    def __init__(self):
        self._subscribers: list[Callable[[AgentEvent], None]] = []

    def subscribe(self, handler: Callable[[AgentEvent], None]) -> None:
        """Register a callback that will receive every emitted event."""
        self._subscribers.append(handler)

    def unsubscribe(self, handler: Callable[[AgentEvent], None]) -> None:
        """Remove a previously registered callback."""
        try:
            self._subscribers.remove(handler)
        except ValueError:
            pass

    def emit(self, event: AgentEvent) -> None:
        """Dispatch an event to all subscribers."""
        for handler in self._subscribers:
            try:
                handler(event)
            except Exception:
                # Never let a transport crash the agent
                pass

    def clear(self) -> None:
        """Remove all subscribers."""
        self._subscribers.clear()
