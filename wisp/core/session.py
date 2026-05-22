"""Session aggregate + event types for event-sourced session management.

Session state is derived by replaying an append-only event log. This gives us:
  - Crash recovery: replay from last snapshot
  - Full audit: every state change is an event
  - Time travel: reconstruct session at any point
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SessionEventType(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMPACTED = "compacted"
    ERROR = "error"
    DONE = "done"


@dataclass(frozen=True)
class SessionEvent:
    """A single immutable event in a session's lifecycle."""

    event_type: SessionEventType
    sequence_num: int
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def user_message(cls, seq: int, content: str) -> SessionEvent:
        return cls(SessionEventType.USER_MESSAGE, seq, {"content": content})

    @classmethod
    def assistant_message(cls, seq: int, content: str, tool_calls: list[dict] | None = None) -> SessionEvent:
        return cls(SessionEventType.ASSISTANT_MESSAGE, seq, {"content": content, "tool_calls": tool_calls or []})

    @classmethod
    def tool_call_event(cls, seq: int, name: str, args: dict) -> SessionEvent:
        return cls(SessionEventType.TOOL_CALL, seq, {"name": name, "arguments": args})

    @classmethod
    def tool_result_event(cls, seq: int, name: str, result: str, duration_ms: float = 0.0) -> SessionEvent:
        return cls(SessionEventType.TOOL_RESULT, seq, {"name": name, "result": result, "duration_ms": duration_ms})

    @classmethod
    def compacted(cls, seq: int, before_count: int, after_count: int, summary: str = "") -> SessionEvent:
        return cls(SessionEventType.COMPACTED, seq, {"before_count": before_count, "after_count": after_count, "summary": summary})

    @classmethod
    def error(cls, seq: int, message: str, recoverable: bool = True) -> SessionEvent:
        return cls(SessionEventType.ERROR, seq, {"message": message, "recoverable": recoverable})

    @classmethod
    def done(cls, seq: int, turns: int, reason: str = "natural") -> SessionEvent:
        return cls(SessionEventType.DONE, seq, {"turns": turns, "reason": reason})


@dataclass
class Session:
    """Session aggregate rebuilt from event replay."""

    session_id: str
    model: str = ""
    workspace: str = ""
    messages: list[dict] = field(default_factory=list)
    compaction_history: list[dict] = field(default_factory=list)
    sequence_num: int = 0
    turn_count: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def apply(self, event: SessionEvent) -> None:
        """Apply a single event to mutate session state."""
        self.sequence_num = max(self.sequence_num, event.sequence_num)
        self.updated_at = event.timestamp

        match event.event_type:
            case SessionEventType.USER_MESSAGE:
                self.messages.append({"role": "user", "content": event.payload["content"]})
                self.turn_count += 1

            case SessionEventType.ASSISTANT_MESSAGE:
                msg: dict[str, Any] = {"role": "assistant", "content": event.payload["content"]}
                if event.payload.get("tool_calls"):
                    msg["tool_calls"] = event.payload["tool_calls"]
                self.messages.append(msg)

            case SessionEventType.TOOL_RESULT:
                self.messages.append({
                    "role": "tool",
                    "content": event.payload["result"],
                    "name": event.payload["name"],
                })

            case SessionEventType.COMPACTED:
                self.compaction_history.append({
                    "before_count": event.payload["before_count"],
                    "after_count": event.payload["after_count"],
                    "summary": event.payload.get("summary", ""),
                    "timestamp": event.timestamp,
                })

            case SessionEventType.ERROR:
                self.messages.append({
                    "role": "system",
                    "content": f"[Error] {event.payload['message']}",
                })

            case SessionEventType.DONE:
                pass  # terminal event, no state change

    def replay(self, events: list[SessionEvent]) -> None:
        """Replay a sequence of events from scratch."""
        self.messages.clear()
        self.compaction_history.clear()
        self.sequence_num = 0
        self.turn_count = 0
        for ev in sorted(events, key=lambda e: e.sequence_num):
            self.apply(ev)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.session_id,
            "model": self.model,
            "workspace": self.workspace,
            "messages": self.messages,
            "compaction_history": self.compaction_history,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
