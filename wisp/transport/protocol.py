"""Unified message format for Wisp transports.

Replaces: ad-hoc event dictionaries scattered across transports.
One schema for all events, validated and typed.

Design:
  - Dataclass-based events with type safety
  - Factory methods for each event type
  - Serialization to/from dict for JSON transport
  - Transport-specific formatting (WS, SSE, CLI)
  - Validation on deserialization
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Event:
    """Unified event format for all transports."""

    type: str
    text: str = ""
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    message: str = ""
    recoverable: bool = False
    session_id: str = ""

    # ── Factory methods ───────────────────────────────────────────────

    @classmethod
    def content(cls, text: str) -> Event:
        return cls(type="content", text=text)

    @classmethod
    def tool_call(cls, name: str, arguments: dict[str, Any]) -> Event:
        return cls(type="tool_call", name=name, arguments=arguments)

    @classmethod
    def tool_result(cls, name: str, result: str) -> Event:
        return cls(type="tool_result", name=name, result=result)

    @classmethod
    def error(cls, message: str, recoverable: bool = True) -> Event:
        return cls(type="error", message=message, recoverable=recoverable)

    @classmethod
    def done(cls) -> Event:
        return cls(type="done")

    @classmethod
    def ready(cls, session_id: str) -> Event:
        return cls(type="ready", session_id=session_id)

    # ── Serialization ───────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON transport."""
        data: dict[str, Any] = {"type": self.type}
        if self.text:
            data["text"] = self.text
        if self.name:
            data["name"] = self.name
        if self.arguments:
            data["arguments"] = self.arguments
        if self.result:
            data["result"] = self.result
        if self.message:
            data["message"] = self.message
        if self.recoverable:
            data["recoverable"] = self.recoverable
        if self.session_id:
            data["session_id"] = self.session_id
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        """Deserialize from dictionary with validation."""
        event_type = data.get("type")
        if not event_type:
            raise ValueError("Event missing 'type' field")

        if event_type not in ("content", "tool_call", "tool_result", "error", "done", "ready"):
            raise ValueError(f"Unknown event type: {event_type}")

        if event_type == "content" and "text" not in data:
            raise ValueError("Content event missing 'text' field")
        if event_type == "tool_call" and "name" not in data:
            raise ValueError("Tool call event missing 'name' field")

        return cls(
            type=event_type,
            text=data.get("text", ""),
            name=data.get("name", ""),
            arguments=data.get("arguments", {}),
            result=data.get("result", ""),
            message=data.get("message", ""),
            recoverable=data.get("recoverable", False),
            session_id=data.get("session_id", ""),
        )

    # ── Transport-specific formatting ────────────────────────────────

    def to_ws(self) -> dict[str, Any]:
        """Format for WebSocket transport."""
        return self.to_dict()

    def to_sse(self) -> str:
        """Format for SSE transport."""
        return f"data: {json.dumps(self.to_dict())}\n\n"

    def to_cli(self) -> str:
        """Format for CLI transport."""
        if self.type == "content":
            return self.text
        elif self.type == "error":
            return f"Error: {self.message}"
        elif self.type == "tool_call":
            return f"[Tool: {self.name}]"
        elif self.type == "tool_result":
            return f"[Result: {self.result}]"
        elif self.type == "done":
            return "\n"
        elif self.type == "ready":
            return f"Ready: {self.session_id}"
        return ""
