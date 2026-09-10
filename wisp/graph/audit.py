"""Graph → immutable-audit adapter (closes G-1).

Thin boundary: security decisions made by the graph runtime are recorded in
the EXISTING ImmutableAuditTrail (same hash chain as tool decisions). The
adapter records; it never authorizes — audit output cannot grant authority.

Consistency model (matches SecurityPolicy._audit): decision → audit attempt
→ state transition. Audit failure never blocks execution, but the returned
hash ("..." or "") is stored with the decision — a missing hash means "not
durably audited", never a false claim.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Optional

from wisp.graph.security import scrub, scrub_text
from wisp.infra.audit import ImmutableAuditTrail

logger = logging.getLogger(__name__)

# Machine-checkable coverage matrix anchor: every event the engine emits.
AUDIT_EVENTS = frozenset({
    "graph.validation_rejected",
    "graph.policy_rejected",
    "graph.fingerprint_mismatch",
    "graph.state_tamper",
    "graph.approval_requested",
    "graph.approval_granted",
    "graph.approval_denied",
    "graph.route_selected",
    "graph.route_unknown",
    "graph.gate_decision",
    "graph.verification_allow",
    "graph.verification_reject",
    "graph.verification_retry",
    "graph.verification_escalate",
    "graph.retry_allowed",
    "graph.retry_refused",
    "graph.cancel",
    "graph.stale_rejected",
    "graph.budget_exceeded",
    "graph.run_terminated",
    "graph.policy_narrowed",
    "graph.proposal_decided",
    "graph.optimization_rejected",
})

_SUMMARY_BYTES = 2000


class _Conn:
    """Minimal _get_conn shim: own autocommit connection (thread-safe via lock)."""

    def __init__(self, path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._conn = sqlite3.connect(path, timeout=10, isolation_level=None,
                                     check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def _get_conn(self) -> sqlite3.Connection:
        return self._conn


class GraphSecurityAuditor:
    """Records graph security decisions into ImmutableAuditTrail."""

    def __init__(self, workspace: str = ".", trail: ImmutableAuditTrail | None = None,
                 db_path: str = "") -> None:
        self.workspace = os.path.abspath(workspace)
        self._lock = threading.RLock()
        if trail is not None:
            self._trail = trail
        else:
            from wisp.graph.store import _db_path
            self._trail = ImmutableAuditTrail(_Conn(db_path or _db_path(workspace)))

    def emit(self, event: str, *, graph_id: str = "", graph_hash: str = "",
             run_id: str = "", node_id: str = "", attempt: int = 0,
             allowed: bool, reason: str = "", evidence: Any = None) -> str:
        """Append one security event. Returns entry hash, or "" on failure."""
        if event not in AUDIT_EVENTS:
            logger.warning("graph audit: unknown event %r refused", event)
            return ""
        if evidence is None:
            ev_blob = None
        else:
            # Scrub the STRUCTURED value first, then serialize: str()/repr()
            # escaping would defeat multiline secret patterns (PEM blocks).
            try:
                ev_blob = json.dumps(scrub(evidence), sort_keys=True, default=str)
            except (TypeError, ValueError):
                ev_blob = scrub_text(str(evidence))
        summary = scrub({
            "graph_id": str(graph_id)[:128], "graph_hash": str(graph_hash)[:64],
            "run_id": str(run_id)[:128], "node_id": str(node_id)[:128],
            "attempt": int(attempt or 0),
            "evidence": ev_blob[:500] if ev_blob else None,
        })
        try:
            blob = json.dumps(summary, sort_keys=True, default=str)
        except (TypeError, ValueError):
            blob = "{}"
        if len(blob) > _SUMMARY_BYTES:
            blob = blob[:_SUMMARY_BYTES]
        try:
            with self._lock:
                return self._trail.record_decision(
                    action=event, tool_name=scrub_text(str(node_id))[:128],
                    workspace=self.workspace, allowed=allowed,
                    reason=scrub_text(str(reason))[:500], args_summary=blob) or ""
        except Exception:
            logger.exception("graph audit write failed for %s", event)
            return ""

    def verify(self) -> Optional[int]:
        try:
            with self._lock:
                return self._trail.verify()
        except Exception:
            logger.exception("graph audit verify failed")
            return -1
