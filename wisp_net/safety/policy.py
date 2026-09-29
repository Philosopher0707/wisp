"""Guardrail policy arbiter: may this change touch devices, and with whose sign-off?

Rules are data (`policies/change-policy.json`), evaluated in a fixed order so the first
refusal is the one reported: verification, blast radius, change windows, confidence.
Verdicts: `deny` (the platform will not apply it), `require_approval` (a human must sign
off), `allow` (eligible to proceed without one). Times are UTC.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from wisp_net.safety.change import ChangeSet

POLICIES = Path(__file__).resolve().parent.parent / "policies"


@dataclass(frozen=True)
class Window:
    name: str
    weekdays: tuple[int, ...] = ()
    start: str = "00:00"
    end: str = "24:00"
    from_date: str | None = None
    to_date: str | None = None

    def contains(self, ts: float) -> bool:
        moment = datetime.fromtimestamp(ts, tz=timezone.utc)
        if self.from_date and self.to_date:
            if not date.fromisoformat(self.from_date) <= moment.date() <= date.fromisoformat(self.to_date):
                return False
        if self.weekdays and moment.weekday() not in self.weekdays:
            return False
        minute = moment.hour * 60 + moment.minute
        return _minutes(self.start) <= minute < _minutes(self.end)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


@dataclass(frozen=True)
class ChangePolicy:
    max_port_changes: int = 10
    hitl_confidence_threshold: float = 0.85
    require_verification: bool = True
    windows: tuple[Window, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChangePolicy":
        windows = tuple(Window(w["name"], tuple(w.get("weekdays", ())), w.get("start", "00:00"), w.get("end", "24:00"),
                               w.get("from_date"), w.get("to_date"))
                        for w in data.get("prohibited_change_windows", []))
        return cls(int(data.get("max_concurrent_port_modifications", 10)),
                   float(data.get("mandatory_hitl_confidence_threshold", 0.85)),
                   bool(data.get("require_verification", True)), windows)

    @classmethod
    def bundled(cls) -> "ChangePolicy":
        return cls.from_dict(json.loads((POLICIES / "change-policy.json").read_text(encoding="utf-8")))

    def active_windows(self, ts: float) -> list[str]:
        return [w.name for w in self.windows if w.contains(ts)]


@dataclass(frozen=True)
class PolicyDecision:
    verdict: str  # allow | require_approval | deny
    reasons: tuple[str, ...]
    facts: dict[str, Any] = field(default_factory=dict)


def decide(change: ChangeSet, now: float, verification_passed: bool | None,
           policy: ChangePolicy) -> PolicyDecision:
    ports = sorted(f"{d}:{i}" for d, i in change.touched_ports())
    windows_now = policy.active_windows(now)
    facts: dict[str, Any] = {"touched_ports": ports, "confidence": change.confidence, "windows_now": windows_now,
             "verification_passed": verification_passed}
    denials: list[str] = []
    if policy.require_verification and verification_passed is not True:
        denials.append("formal verification has not passed" if verification_passed is False
                       else "the change has not been verified")
    if len(ports) > policy.max_port_changes:
        denials.append(f"blast radius: {len(ports)} ports changed, the limit is {policy.max_port_changes}")
    if windows_now:
        denials.append(f"inside a prohibited change window: {', '.join(windows_now)}")
    if denials:
        return PolicyDecision("deny", tuple(denials), facts)
    if change.confidence < policy.hitl_confidence_threshold:
        return PolicyDecision("require_approval", (
            f"confidence {change.confidence:.2f} is below the {policy.hitl_confidence_threshold:.2f} "
            "threshold for acting without a human",), facts)
    return PolicyDecision("allow", ("verified, within blast radius, outside change windows, confident",), facts)
