"""Event system for Wisp SDK — structured events emitted by the agent core.

All I/O (printing, logging, WebSocket pushes) is handled by transports that
subscribe to these events. The core itself is pure logic.

Schema version 1 — all events carry trace context and strict types.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional


EVENT_SCHEMA_VERSION = 1


class EventType(StrEnum):
    """Type-safe event type constants.

    Backward-compatible with existing TYPE_* string constants.
    Can be used anywhere a string is expected.
    """
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTENT = "content"
    ERROR = "error"
    DONE = "done"
    SYSTEM = "system"
    APPROVAL_REQUEST = "approval_request"
    STEERING_PAUSED = "steering_paused"
    STEERING_INJECT = "steering_inject"
    STEERING_RESUMED = "steering_resumed"


# ── Backward-compatible aliases ──────────────────────────────────────
TYPE_THINKING = EventType.THINKING
TYPE_TOOL_CALL = EventType.TOOL_CALL
TYPE_TOOL_RESULT = EventType.TOOL_RESULT
TYPE_CONTENT = EventType.CONTENT
TYPE_ERROR = EventType.ERROR
TYPE_DONE = EventType.DONE
TYPE_SYSTEM = EventType.SYSTEM
TYPE_APPROVAL_REQUEST = EventType.APPROVAL_REQUEST
TYPE_STEERING_PAUSED = EventType.STEERING_PAUSED
TYPE_STEERING_INJECT = EventType.STEERING_INJECT
TYPE_STEERING_RESUMED = EventType.STEERING_RESUMED


@dataclass(frozen=True)
class AgentEvent:
    """A single event emitted by the agent during a turn.

    Attributes:
        type: Event category — one of the EventType enum values.
        data: Payload dict (structure depends on type).
        timestamp: Monotonic timestamp for ordering and latency tracking.
        trace_id: UUID7 trace ID — shared by all events in a turn.
        span_id: UUID7 span ID — unique per event within a trace.
        schema_version: Event schema version for forward compatibility.
    """

    type: EventType | str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)
    trace_id: str = ""
    span_id: str = ""
    schema_version: int = EVENT_SCHEMA_VERSION

    # ── Convenience accessors ──────────────────────────────────────

    @property
    def text(self) -> str:
        """Shortcut for content/thinking events."""
        return str(self.data.get("text", ""))

    @property
    def tool_name(self) -> str:
        """Shortcut for tool_call / tool_result events."""
        return str(self.data.get("name", ""))

    @property
    def is_final(self) -> bool:
        """True if this event signals the end of a turn."""
        return self.type in (TYPE_DONE, TYPE_ERROR)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict.

        Canonical format: {"type": str, "data": {...}, "timestamp": float,
                           "trace_id": str, "span_id": str, "schema_version": int}
        """
        d: dict[str, Any] = {
            "type": str(self.type),
            "data": self.data,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }
        if self.trace_id:
            d["trace_id"] = self.trace_id
        if self.span_id:
            d["span_id"] = self.span_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentEvent:
        """Deserialize from dict (round-trips with to_dict).

        Handles both canonical format ({type, data}) and flat format
        ({type, text, ...}) for backward compatibility.
        """
        ev_type = data.get("type", "")
        ev_data = data.get("data")
        trace_id = data.get("trace_id", "")
        span_id = data.get("span_id", "")
        schema_ver = data.get("schema_version", EVENT_SCHEMA_VERSION)

        if ev_data is not None:
            return cls(
                type=ev_type,
                data=dict(ev_data),
                timestamp=data.get("timestamp", 0.0),
                trace_id=trace_id,
                span_id=span_id,
                schema_version=schema_ver,
            )

        flat_data = {k: v for k, v in data.items()
                     if k not in ("type", "timestamp", "trace_id", "span_id", "schema_version")}
        return cls(
            type=ev_type,
            data=flat_data,
            timestamp=data.get("timestamp", 0.0),
            trace_id=trace_id,
            span_id=span_id,
            schema_version=schema_ver,
        )


# ── Event normalizer ──────────────────────────────────────────────

def normalize_event(event: Any) -> AgentEvent:
    """Normalize any event representation to a canonical AgentEvent.

    Accepts:
      - AgentEvent instances (returned as-is)
      - Canonical dicts: {"type": "content", "data": {"text": "..."}}
      - Flat dicts: {"type": "content", "text": "..."}
      - Provider objects with type/phase + attributes

    Returns:
        Canonical AgentEvent with all payload in the data dict.
    """
    if isinstance(event, AgentEvent):
        return event

    if isinstance(event, dict):
        return AgentEvent.from_dict(event)

    # Provider object (TokenBatch, Checkpoint, etc.)
    result: dict[str, Any] = {}
    if hasattr(event, "type"):
        result["type"] = event.type
    elif hasattr(event, "phase"):
        result["type"] = event.phase
    else:
        result["type"] = "unknown"

    # Whitelist known safe fields
    safe_fields = {
        "text", "name", "arguments", "result", "message",
        "duration_ms", "turns", "session_id", "summary", "reason",
        "level", "recoverable", "tool_call_id", "id",
    }
    for field_name in safe_fields:
        if hasattr(event, field_name):
            result[field_name] = getattr(event, field_name)

    return AgentEvent.from_dict(result)


# Human-readable descriptions
_EVENT_DESCRIPTIONS: dict[str, str] = {
    EventType.THINKING: "Model reasoning trace",
    EventType.TOOL_CALL: "Tool invocation",
    EventType.TOOL_RESULT: "Tool execution result",
    EventType.CONTENT: "Assistant text response",
    EventType.ERROR: "Error occurred",
    EventType.DONE: "Turn complete",
    EventType.SYSTEM: "System notification",
    EventType.APPROVAL_REQUEST: "User approval required",
}


def describe_event_type(event_type: EventType | str) -> str:
    """Return a human description for an event type."""
    return _EVENT_DESCRIPTIONS.get(str(event_type), "Unknown event")


# ── Trace context helpers ──────────────────────────────────────────

def _trace_ctx() -> tuple[str, str]:
    """Return (trace_id, span_id) from contextvars, or (\"\", \"\")."""
    try:
        from wisp.infra.tracing import current_trace_id, current_span_id
        return current_trace_id() or "", current_span_id() or ""
    except ImportError:
        return "", ""


def _make_event(event_type: EventType | str, data: dict[str, Any]) -> AgentEvent:
    """Create an AgentEvent with trace context and schema version auto-populated."""
    tid, sid = _trace_ctx()
    return AgentEvent(
        type=event_type,
        data=data,
        trace_id=tid,
        span_id=sid,
        schema_version=EVENT_SCHEMA_VERSION,
    )


# ── Event builders (convenience factories) ─────────────────────────

def thinking(text: str) -> AgentEvent:
    return _make_event(TYPE_THINKING, {"text": text})


def tool_call(name: str, args: dict[str, Any]) -> AgentEvent:
    return _make_event(TYPE_TOOL_CALL, {"name": name, "arguments": args})


def tool_result(name: str, result: str, duration_ms: Optional[float] = None, *, auto_approved: bool = False, tool_call_id: Optional[str] = None) -> AgentEvent:
    payload: dict[str, Any] = {"name": name, "result": result}
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if auto_approved:
        payload["auto_approved"] = True
    if tool_call_id is not None:
        payload["tool_call_id"] = tool_call_id
    return _make_event(TYPE_TOOL_RESULT, payload)


def content(text: str) -> AgentEvent:
    return _make_event(TYPE_CONTENT, {"text": text})


def error(message: str, recoverable: bool = True) -> AgentEvent:
    return _make_event(TYPE_ERROR, {"message": message, "recoverable": recoverable})


def done(session_id: str, turns: int = 0, summary: str = "", reason: str = "natural") -> AgentEvent:
    return _make_event(TYPE_DONE, {"session_id": session_id, "turns": turns, "summary": summary, "reason": reason})


def system(message: str, level: str = "info") -> AgentEvent:
    return _make_event(TYPE_SYSTEM, {"message": message, "level": level})


def steering_paused(reason: str = "User paused") -> AgentEvent:
    return _make_event(TYPE_STEERING_PAUSED, {"reason": reason})


def steering_resumed() -> AgentEvent:
    return _make_event(TYPE_STEERING_RESUMED, {})


def steering_feedback(text: str) -> AgentEvent:
    return _make_event(TYPE_STEERING_INJECT, {"text": text})


def approval_request(tool_name: str, args: dict[str, Any], reason: str = "") -> AgentEvent:
    return _make_event(TYPE_APPROVAL_REQUEST, {"name": tool_name, "arguments": args, "reason": reason})


__all__ = [
    "AgentEvent",
    "EventType",
    "EVENT_SCHEMA_VERSION",
    "TYPE_THINKING",
    "TYPE_TOOL_CALL",
    "TYPE_TOOL_RESULT",
    "TYPE_CONTENT",
    "TYPE_ERROR",
    "TYPE_DONE",
    "TYPE_SYSTEM",
    "TYPE_APPROVAL_REQUEST",
    "TYPE_STEERING_PAUSED",
    "TYPE_STEERING_INJECT",
    "TYPE_STEERING_RESUMED",
    "describe_event_type",
    "thinking",
    "tool_call",
    "tool_result",
    "content",
    "error",
    "done",
    "system",
    "steering_paused",
    "steering_resumed",
    "steering_feedback",
    "approval_request",
    "normalize_event",
]
