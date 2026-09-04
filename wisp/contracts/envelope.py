"""Canonical nested event envelope (M1a contract freeze)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from wisp.core.events import AgentEvent

CONTRACT_VERSION = 1


@dataclass(frozen=True)
class CanonicalEvent:
    """Nested-only canonical event. Field `schema_version` matches
    AgentEvent.to_dict() wire name exactly (no rename on the wire)."""
    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0
    trace_id: str = ""
    span_id: str = ""
    schema_version: int = CONTRACT_VERSION

    @classmethod
    def from_agent_event(cls, ev: AgentEvent) -> "CanonicalEvent":
        return cls(type=str(ev.type), data=dict(ev.data),
                   timestamp=ev.timestamp, trace_id=ev.trace_id,
                   span_id=ev.span_id, schema_version=ev.schema_version)

    def to_agent_event(self) -> AgentEvent:
        return AgentEvent(type=self.type, data=dict(self.data),
                          timestamp=self.timestamp, trace_id=self.trace_id,
                          span_id=self.span_id, schema_version=self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "data": self.data,
                             "timestamp": self.timestamp,
                             "schema_version": self.schema_version}
        if self.trace_id:
            d["trace_id"] = self.trace_id
        if self.span_id:
            d["span_id"] = self.span_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CanonicalEvent":
        known = {"type", "data", "timestamp", "trace_id", "span_id", "schema_version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown envelope fields: {sorted(unknown)}")
        return cls(type=d.get("type", ""), data=dict(d.get("data") or {}),
                   timestamp=d.get("timestamp", 0.0),
                   trace_id=d.get("trace_id", ""), span_id=d.get("span_id", ""),
                   schema_version=d.get("schema_version", CONTRACT_VERSION))
