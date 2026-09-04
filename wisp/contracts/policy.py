"""Policy-decision wire envelope (M1a). Adds no authority: serializes the two
existing decision types (core/contracts.ApprovalDecision,
infra/policy_engine.PolicyDecision) into one wire form."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

CANCELLED_BY_USER = "cancelled_by_user"


@dataclass(frozen=True)
class PolicyDecisionEnvelope:
    allowed: bool
    reason: str = ""
    modified_args: Optional[dict[str, Any]] = None
    rule_name: str = ""
    risk: str = "read"
    controlling_layer: str = ""  # reserved: built-in/org/admin/workspace/session
    principal_id: str = ""       # reserved, supplier TBD Phase 1
    correlation_id: str = ""     # reserved, supplier TBD Phase 1
    version: int = 1

    @classmethod
    def from_gate_decision(cls, d: Any) -> "PolicyDecisionEnvelope":
        return cls(allowed=d.allowed, reason=d.reason,
                   modified_args=d.modified_args, risk=str(getattr(d.risk, "value", d.risk)))

    @classmethod
    def from_engine_decision(cls, d: Any) -> "PolicyDecisionEnvelope":
        # Explicit exclusion (spec §3): engine decisions carry no risk, so the
        # wire form defaults to "read". Callers with risk knowledge must set
        # it explicitly; never infer authority from a default.
        return cls(allowed=d.allowed, reason=d.reason,
                   modified_args=d.modified_args, rule_name=d.rule_name)

    @classmethod
    def cancelled(cls, correlation_id: str = "") -> "PolicyDecisionEnvelope":
        return cls(allowed=False, reason=CANCELLED_BY_USER,
                   correlation_id=correlation_id)

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason,
                "modified_args": self.modified_args, "rule_name": self.rule_name,
                "risk": self.risk, "controlling_layer": self.controlling_layer,
                "principal_id": self.principal_id,
                "correlation_id": self.correlation_id, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "PolicyDecisionEnvelope":
        known = {"allowed", "reason", "modified_args", "rule_name", "risk",
                 "controlling_layer", "principal_id", "correlation_id", "version"}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"unknown policy fields: {sorted(unknown)}")
        return cls(allowed=d["allowed"], reason=d.get("reason", ""),
                   modified_args=d.get("modified_args"),
                   rule_name=d.get("rule_name", ""), risk=d.get("risk", "read"),
                   controlling_layer=d.get("controlling_layer", ""),
                   principal_id=d.get("principal_id", ""),
                   correlation_id=d.get("correlation_id", ""),
                   version=d.get("version", 1))
