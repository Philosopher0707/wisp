"""Structured JSONL audit log for destructive (write) tool auto-approvals in headless mode.

Q22: When WISP_HEADLESS_AUTO_APPROVE=1 bypasses explicit approval, every
invocation of a tool in ``_WRITE_TOOLS`` is recorded so CI/compliance
can retrospectively audit what was executed without operator consent.

Uses POSIX fcntl advisory locks (exclusive write) so concurrent
processes can append safely.
"""

from __future__ import annotations

import fcntl
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Deferred import to avoid circular references
_ImmutableAuditTrail = None

def _get_immutable_audit_trail():
    global _ImmutableAuditTrail
    if _ImmutableAuditTrail is None:
        from wisp.infra.audit import ImmutableAuditTrail
        _ImmutableAuditTrail = ImmutableAuditTrail
    return _ImmutableAuditTrail


class AuditLog:
    """Thread-safe JSONL audit trail for destructive tool executions.

    One entry per tool call that modifies workspace state (the set of
tools in ``_WRITE_TOOLS``).  Records the decision path — auto-approved
    when headless, blocked when forbidden, or explicit via approval handler.

    When *store* is provided, writes are delegated to the consolidated
    ``ImmutableAuditTrail`` (SQLite-backed) instead of a separate JSONL file.
    """

    def __init__(self, audit_path: Path | str | None = None, *, store: Any = None):
        self._path = Path(audit_path) if audit_path else None
        self._store = store
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch(exist_ok=True)

    # ------------------------------------------------------------------
    # Public recording methods
    # ------------------------------------------------------------------

    def log_auto_approved(
        self,
        func_name: str,
        func_args: dict,
        workspace: str,
        result: str,
        duration_ms: float,
        mode: str,
        forced: bool = False,
    ) -> None:
        self._append_entry(
            _build_entry(
                func_name,
                func_args,
                workspace,
                result,
                duration_ms,
                decision="auto_approved",
                forced=forced,
                mode=mode,
            )
        )

    def log_explicit_approved(
        self,
        func_name: str,
        func_args: dict,
        workspace: str,
        result: str,
        duration_ms: float,
        mode: str,
    ) -> None:
        self._append_entry(
            _build_entry(
                func_name,
                func_args,
                workspace,
                result,
                duration_ms,
                decision="approved",
                forced=False,
                mode=mode,
            )
        )

    def log_blocked(
        self,
        func_name: str,
        func_args: dict,
        workspace: str,
        reason: str,
        mode: str,
    ) -> None:
        entry = _build_entry(
            func_name,
            func_args,
            workspace,
            result="",
            duration_ms=0.0,
            decision="blocked",
            forced=False,
            mode=mode,
        )
        entry["block_reason"] = reason
        self._append_entry(entry)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_entry(self, entry: dict) -> None:
        if self._store is not None:
            try:
                trail = _get_immutable_audit_trail()(self._store)
                # Map AuditLog entry to ImmutableAuditTrail.record_decision
                allowed = entry["decision"] in ("auto_approved", "approved")
                reason = entry.get("block_reason", "")
                if not reason and entry["decision"] == "auto_approved":
                    reason = f"auto_approved (forced={entry.get('forced', False)}, mode={entry.get('mode', '')})"
                elif not reason and entry["decision"] == "approved":
                    reason = f"explicit_approved (mode={entry.get('mode', '')})"
                args_summary = json.dumps({
                    "args_keys": entry.get("args_keys", []),
                    "arg_summary": entry.get("arg_summary", {}),
                    "duration_ms": entry.get("duration_ms", 0),
                    "result_status": entry.get("result_status", ""),
                }, ensure_ascii=False)
                trail.record_decision(
                    action=entry.get("tool", ""),
                    tool_name=entry.get("tool", ""),
                    workspace=entry.get("workspace", ""),
                    allowed=allowed,
                    reason=reason,
                    args_summary=args_summary,
                )
            except Exception:
                logger.warning("Audit SQLite append failed for %s", entry.get("tool", "?"), exc_info=True)
            return

        if self._path is None:
            return

        try:
            with open(self._path, "a", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    f.flush()
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            logger.warning("Audit append failed for %s", entry.get("tool", "?"), exc_info=True)


def _redacted_summary(func_name: str, func_args: dict) -> dict[str, str]:
    """Scrub long values, then redact secret patterns (M2 I3).

    Redaction at record construction — not export — so a misconfigured
    sink cannot leak key material into the audit trail.
    """
    from wisp.auth.secrets import redact
    return {k: redact(v) for k, v in _scrub_args(func_name, func_args).items()}


def _build_entry(
    func_name: str,
    func_args: dict,
    workspace: str,
    result: str,
    duration_ms: float,
    *,
    decision: str,
    forced: bool,
    mode: str,
) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": func_name,
        "args_keys": sorted(func_args.keys()),
        "arg_summary": _redacted_summary(func_name, func_args),
        "workspace": str(Path(workspace).resolve()),
        "result_status": _result_status(result),
        "duration_ms": round(duration_ms, 2),
        "decision": decision,
        "forced": forced,
        "mode": mode,
    }


def _scrub_args(func_name: str, args: dict) -> dict[str, str]:
    """Scrub long values so the audit log never leaks full file contents."""
    scrubbed: dict[str, str] = {}
    for key, value in args.items():
        val = str(value)
        if key in ("content", "command", "text", "new_text", "old_text"):
            scrubbed[key] = val if len(val) <= 120 else val[:117] + "..."
        else:
            scrubbed[key] = val if len(val) <= 200 else val[:197] + "..."
    return scrubbed


def _result_status(result: str) -> str:
    if isinstance(result, str):
        if result.startswith("Error:"):
            return "error"
        if '"status": "error"' in result:
            return "error"
        if '"status": "ok"' in result:
            return "ok"
    return "ok"
