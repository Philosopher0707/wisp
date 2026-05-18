"""Structured JSONL audit log for destructive (write) tool auto-approvals in headless mode.

Q22: When WISP_HEADLESS_AUTO_APPROVE=1 bypasses explicit approval, every
invocation of a tool in ``_WRITE_TOOLS`` is recorded so CI/compliance
can retrospectively audit what was executed without operator consent.

Uses POSIX fcntl advisory locks (shared read / exclusive write) so
concurrent processes can append safely.
"""

from __future__ import annotations

import fcntl
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:
    """Thread-safe JSONL audit trail for destructive tool executions.

    One entry per tool call that modifies workspace state (the set of
    tools in ``_WRITE_TOOLS``).  Records the decision path — auto-approved
    when headless, blocked when forbidden, or explicit via approval handler.
    """

    def __init__(self, audit_path: Path | str):
        self._path = Path(audit_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Ensure the file exists for locking operations that need an fd.
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
        "arg_summary": _scrub_args(func_name, func_args),
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
        # Large content fields get hard-truncated to 120 chars
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
