"""Trace spans (M5 evidence layer). Kind vocabulary covers the turn
lifecycle; every span carries the uuid7 trace lineage AgentEvent already
propagates. Redaction is applied at store append, not here.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SPAN_VERSION = 1

SPAN_KINDS: tuple[str, ...] = (
    "run", "turn", "model_request", "tool_call", "policy_decision",
    "approval", "retry", "subagent", "checkpoint", "artifact",
)


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    DENIED = "denied"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class Span:
    trace_id: str
    span_id: str
    kind: str
    name: str = ""
    parent_span_id: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    attrs: dict[str, Any] = field(default_factory=dict)
    status: SpanStatus = SpanStatus.OK
    version: int = SPAN_VERSION

    def __post_init__(self) -> None:
        if self.kind not in SPAN_KINDS:
            raise ValueError(f"bad span kind: {self.kind!r}")

    @property
    def duration_ms(self) -> float:
        return max(0.0, (self.finished_at - self.started_at) * 1000.0)

    def to_dict(self) -> dict[str, Any]:
        return {"trace_id": self.trace_id, "span_id": self.span_id,
                "parent_span_id": self.parent_span_id, "kind": self.kind,
                "name": self.name, "started_at": self.started_at,
                "finished_at": self.finished_at, "attrs": self.attrs,
                "status": self.status.value, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Span":
        known = {"trace_id", "span_id", "parent_span_id", "kind", "name",
                 "started_at", "finished_at", "attrs", "status", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown span fields: {sorted(unknown)}")
        return cls(trace_id=d["trace_id"], span_id=d["span_id"],
                   parent_span_id=d.get("parent_span_id", ""), kind=d["kind"],
                   name=d.get("name", ""), started_at=d.get("started_at", 0.0),
                   finished_at=d.get("finished_at", 0.0),
                   attrs=dict(d.get("attrs") or {}),
                   status=SpanStatus(d.get("status", "ok")),
                   version=d.get("version", SPAN_VERSION))
