"""Operator controls: the approval queue and the kill switch.

An approval is bound to one change fingerprint and expires. It can only be granted
through the operator cockpit: no MCP tool grants one, so the agent that proposed a change
cannot also approve it. The kill switch returns the platform to passive monitoring —
while it is engaged nothing is applied, and engaging it rolls back a change still inside
its confirm window.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from typing import Any

APPROVAL_TTL_S = 3600.0


@dataclass
class ApprovalRequest:
    request_id: str
    fingerprint: str
    intent: str
    requested_at: float
    reasons: list[str]
    summary: dict[str, Any]
    status: str = "pending"  # pending | granted | denied | expired | used
    decided_by: str | None = None
    decided_at: float | None = None
    note: str = ""
    expires_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApprovalError(ValueError):
    pass


class ApprovalQueue:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, ApprovalRequest] = {}
        self._seq = 0

    def request(self, fingerprint: str, intent: str, now: float, reasons: list[str],
                summary: dict[str, Any]) -> ApprovalRequest:
        """The open request for this change, creating it if there is none."""
        with self._lock:
            for r in self._requests.values():
                if r.fingerprint == fingerprint and r.status in ("pending", "granted"):
                    self._expire(r, now)
                    if r.status in ("pending", "granted"):
                        return r
            self._seq += 1
            req = ApprovalRequest(f"R{self._seq:04d}", fingerprint, intent, now, list(reasons), summary)
            self._requests[req.request_id] = req
            return req

    def decide(self, request_id: str, grant: bool, operator: str, now: float, note: str = "") -> ApprovalRequest:
        operator = operator.strip()
        if not operator:
            raise ApprovalError("an approval decision needs the operator's name")
        with self._lock:
            req = self._requests.get(request_id)
            if req is None:
                raise ApprovalError(f"no approval request {request_id!r}")
            self._expire(req, now)
            if req.status != "pending":
                raise ApprovalError(f"{request_id} is {req.status}, not pending")
            req.status = "granted" if grant else "denied"
            req.decided_by, req.decided_at, req.note = operator, now, note
            req.expires_at = now + APPROVAL_TTL_S if grant else None
            return req

    def consume(self, fingerprint: str, now: float) -> ApprovalRequest | None:
        """A valid grant for this change, marked used so it approves exactly one apply."""
        with self._lock:
            for req in self._requests.values():
                if req.fingerprint != fingerprint:
                    continue
                self._expire(req, now)
                if req.status == "granted":
                    req.status = "used"
                    return req
            return None

    def list(self, status: str | None = None, now: float | None = None) -> list[ApprovalRequest]:
        with self._lock:
            if now is not None:
                for r in self._requests.values():
                    self._expire(r, now)
            return [r for r in self._requests.values() if status is None or r.status == status]

    @staticmethod
    def _expire(req: ApprovalRequest, now: float) -> None:
        if req.status == "granted" and req.expires_at is not None and now > req.expires_at:
            req.status = "expired"


@dataclass
class KillSwitch:
    engaged: bool = False
    by: str | None = None
    reason: str = ""
    since: float | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def set(self, engaged: bool, operator: str, reason: str, now: float) -> None:
        if not operator.strip():
            raise ApprovalError("the kill switch needs the operator's name")
        self.engaged, self.by, self.reason, self.since = engaged, operator, reason, now
        self.history.append({"engaged": engaged, "by": operator, "reason": reason, "at": now})
