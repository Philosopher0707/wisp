"""Tool-request/result wire envelopes (M1a contract freeze)."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

TOOL_VERSION = 1
STATUSES = ("ok", "error", "denied", "cancelled")
# Canonicalization of wisp/tool_executor.py:449-481 block branches.
# "" is the "no block" sentinel (status ok/error carry no block reason).
# Out of scope: pre_bash/pre_file hooks (:527-536) fold into "pre_tool";
# approval-decline folds into "permission".
BLOCK_REASONS = ("repeat_guard", "pre_tool", "plan", "danger", "permission", "")


@dataclass(frozen=True)
class ToolRequest:
    tool_call_id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    principal_id: str = ""      # reserved, supplier TBD Phase 1
    correlation_id: str = ""    # reserved, supplier TBD Phase 1
    idempotency_key: str = ""
    version: int = TOOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {"tool_call_id": self.tool_call_id, "name": self.name,
                "args": self.args, "principal_id": self.principal_id,
                "correlation_id": self.correlation_id,
                "idempotency_key": self.idempotency_key, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolRequest":
        known = {"tool_call_id", "name", "args", "principal_id",
                 "correlation_id", "idempotency_key", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown ToolRequest fields: {sorted(unknown)}")
        return cls(tool_call_id=d["tool_call_id"], name=d["name"],
                   args=dict(d.get("args") or {}),
                   principal_id=d.get("principal_id", ""),
                   correlation_id=d.get("correlation_id", ""),
                   idempotency_key=d.get("idempotency_key", ""),
                   version=d.get("version", TOOL_VERSION))


@dataclass(frozen=True)
class ToolResult:
    tool_call_id: str
    status: str  # one of STATUSES; mirrors {status,data,metadata} wrapper
    data: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    auto_approved: bool = False
    block_reason: str = ""  # one of BLOCK_REASONS
    version: int = TOOL_VERSION

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"bad status: {self.status!r}")
        if self.block_reason not in BLOCK_REASONS:
            raise ValueError(f"bad block_reason: {self.block_reason!r}")

    def to_dict(self) -> dict[str, Any]:
        return {"tool_call_id": self.tool_call_id, "status": self.status,
                "data": self.data, "metadata": self.metadata, "error": self.error,
                "auto_approved": self.auto_approved,
                "block_reason": self.block_reason, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolResult":
        known = {"tool_call_id", "status", "data", "metadata", "error",
                 "auto_approved", "block_reason", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown ToolResult fields: {sorted(unknown)}")
        return cls(tool_call_id=d["tool_call_id"], status=d["status"],
                   data=d.get("data"), metadata=dict(d.get("metadata") or {}),
                   error=d.get("error", ""), auto_approved=d.get("auto_approved", False),
                   block_reason=d.get("block_reason", ""),
                   version=d.get("version", TOOL_VERSION))
