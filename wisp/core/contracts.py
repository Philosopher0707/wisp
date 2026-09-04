"""Phase 1 contracts — pure interfaces for the Wisp remediation program.

This module is intentionally dependency-free (stdlib only, no ``wisp.*``
imports) so it can sit at the bottom of the import graph without creating
cycles. Every downstream refactor (Phases 2–3) adapts *toward* these types;
nothing in this file imports business logic, transport backends, or UI code.

Debt IDs reference the Architecture Debt Matrix (audit 2026-09-03):
  D1 global prompt caches, D2 BaseException swallowing, D3 ad-hoc pruning,
  D4 session ownership, D5 transport coupling, D6 triple-gate security,
  D7 namespace fragmentation, D8 flat error taxonomy, D9-D12 globals/clocks.

Backward compatibility: purely additive. No existing module is modified by
adding this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ── Error taxonomy (D2, D8) ────────────────────────────────────────────
# Replaces: stringly ``error_event(code, hint)`` + substring matching in
# ``wisp/core/transport.py:is_transient_error`` + bare ``except
# BaseException`` in ``provider_stream.py`` / ``stateless.py``.


class ErrorKind(str, Enum):
    """Machine-readable failure classes. Wire-stable: do not rename values."""

    TRANSIENT_TRANSPORT = "transient_transport"  # timeout/reset/5xx/429 → retry
    RATE_LIMITED = "rate_limited"  # 429 with retry-after semantics
    FATAL_PROTOCOL = "fatal_protocol"  # 4xx (non-429), bad payload, auth
    TOOL_DENIED = "tool_denied"  # approval / permission-mode block
    TOOL_FAILED = "tool_failed"  # tool ran, returned error
    CONTEXT_BUDGET = "context_budget"  # pruning/compaction ceiling hit
    TURN_TIMEOUT = "turn_timeout"  # wall-clock budget exhausted
    ITERATION_BUDGET = "iteration_budget"  # max_iterations exhausted
    CANCELLED = "cancelled"  # asyncio.CancelledError / KeyboardInterrupt
    INTERNAL = "internal"  # unexpected bug — never retry silently


# HTTP statuses that are retryable by definition. Permanent 4xx (except 429)
# must NOT be retried — the current ``is_transient_status`` conflates them
# via message-substring fallback (D8).
TRANSIENT_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


def classify_status(status: Optional[int]) -> ErrorKind | None:
    """Classify an HTTP status without string matching.

    Returns None for ``None`` (unknown — caller decides, default fatal).
    """
    if status is None:
        return None
    if status == 429:
        return ErrorKind.RATE_LIMITED
    if status in TRANSIENT_STATUS_CODES:
        return ErrorKind.TRANSIENT_TRANSPORT
    if isinstance(status, int) and 400 <= status < 500:
        return ErrorKind.FATAL_PROTOCOL
    if isinstance(status, int) and 500 <= status < 600:
        return ErrorKind.TRANSIENT_TRANSPORT
    return ErrorKind.INTERNAL


class WispError(Exception):
    """Base for all typed Wisp failures. Carries kind + recoverability."""

    kind: ErrorKind = ErrorKind.INTERNAL

    def __init__(self, message: str, *, kind: ErrorKind | None = None,
                 recoverable: bool = False,
                 context: Optional[list[str]] = None) -> None:
        super().__init__(message)
        if kind is not None:
            self.kind = kind
        self.recoverable = recoverable
        self.context: list[str] = list(context or [])

    def to_event_dict(self) -> dict[str, Any]:
        """Render to the flat event-dict shape transports already consume."""
        return {
            "type": "error",
            "message": str(self),
            "kind": self.kind.value,
            "recoverable": self.recoverable,
            "context": list(self.context),
        }


class TransientTransportError(WispError):
    kind: ErrorKind = ErrorKind.TRANSIENT_TRANSPORT

    def __init__(self, message: str, **kw: Any) -> None:
        kw.setdefault("recoverable", True)
        super().__init__(message, kind=ErrorKind.TRANSIENT_TRANSPORT, **kw)


class FatalProviderError(WispError):
    kind: ErrorKind = ErrorKind.FATAL_PROTOCOL

    def __init__(self, message: str, **kw: Any) -> None:
        kw.setdefault("recoverable", False)
        super().__init__(message, kind=ErrorKind.FATAL_PROTOCOL, **kw)


class ToolDeniedError(WispError):
    kind: ErrorKind = ErrorKind.TOOL_DENIED

    def __init__(self, message: str, **kw: Any) -> None:
        kw.setdefault("recoverable", True)
        super().__init__(message, kind=ErrorKind.TOOL_DENIED, **kw)


class CancelledTurnError(WispError):
    """Cancellation must propagate, never be retried as transient (D2)."""

    kind: ErrorKind = ErrorKind.CANCELLED

    def __init__(self, message: str = "turn cancelled", **kw: Any) -> None:
        kw.setdefault("recoverable", False)
        super().__init__(message, kind=ErrorKind.CANCELLED, **kw)


def is_cancellation(exc: BaseException) -> bool:
    """True for signals that must never be classified as transient.

    Covers ``asyncio.CancelledError`` (BaseException since 3.8),
    ``KeyboardInterrupt``, ``GeneratorExit``, and ``SystemExit``. Phase 2
    rewrites every ``except BaseException`` guard to check this first and
    re-raise.
    """
    import asyncio

    return isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, GeneratorExit, SystemExit))


# ── Transport seam (D4, D5) ────────────────────────────────────────────
# Replaces: magic numbers scattered across ``core/transport.py``,
# ``ollama_client.py``, ``providers/openai.py`` + per-call
# ``get_hardened_session()`` with no ownership.


@dataclass(frozen=True)
class TransportConfig:
    """Granular timeouts + pool bounds. Mirrors the hardened spec."""

    connect_s: float = 15.0
    write_s: float = 60.0
    read_s: float = 120.0
    pool_s: float = 30.0
    pool_connections: int = 20
    pool_maxsize: int = 20

    def as_requests_timeout(self) -> tuple[float, float]:
        """requests only supports (connect, read); fold write into read."""
        return (self.connect_s, max(self.write_s, self.read_s))


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry for TRANSIENT_TRANSPORT / RATE_LIMITED only."""

    max_attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    jitter_s: float = 0.5

    def delay_for_attempt(self, attempt: int) -> float:
        """Exponential backoff without randomness (jitter added by caller)."""
        return min(self.base_delay_s * (2.0 ** max(0, attempt - 1)), self.max_delay_s)


@dataclass(frozen=True)
class StreamGuardConfig:
    """Stall deadlines for ``guarded_provider_stream`` (D2)."""

    first_token_deadline_s: float = 90.0
    chunk_deadline_s: float = 30.0
    max_attempts: int = 3


# ── Pruning contract (D3) ──────────────────────────────────────────────
# Replaces: five call-sites each constructing a default ``PrunerConfig``
# plus the legacy ``agent/context_pruner.py`` sliding-window policy.


@dataclass(frozen=True)
class PrunePolicy:
    """Single source of truth for context-window pruning ceilings."""

    keep_last_n_full: int = 3
    max_bytes_per_historical_result: int = 8192
    max_bytes_per_recent_result: int = 50000
    max_total_bytes: int = 200000
    read_file_historical_max_bytes: int = 2048
    list_files_historical_max_bytes: int = 2048
    preserve_status_line: bool = True
    add_pruned_marker: bool = True

    def validate(self) -> list[str]:
        """Return human-readable violations (empty = valid)."""
        errors: list[str] = []
        if self.keep_last_n_full < 1:
            errors.append("keep_last_n_full must be >= 1")
        if self.max_bytes_per_historical_result < 1:
            errors.append("max_bytes_per_historical_result must be >= 1")
        if self.max_total_bytes < self.max_bytes_per_recent_result:
            errors.append("max_total_bytes must cover one recent result")
        return errors


@dataclass(frozen=True)
class PruneStats:
    """Observable outcome of one prune pass (for telemetry, not logs)."""

    input_messages: int = 0
    pruned_messages: int = 0
    bytes_before: int = 0
    bytes_after: int = 0


#: Shared default pruning ceilings — single source of truth (D3).
#: Call sites pass this explicitly instead of relying on bare defaults so
#: the policy is visible at the seam and tunable in one place.
DEFAULT_PRUNE_POLICY: "PrunePolicy" = PrunePolicy()


# ── Approval contract (D6) ─────────────────────────────────────────────
# Replaces: the triple gate (SecurityPolicy matrix vs
# ToolExecutor._check_permission_mode vs check_dangerous_command regex).
# Phase 2 collapses all three onto ToolRisk + ApprovalDecision.


class ToolRisk(str, Enum):
    READ = "read"  # never needs approval, even in read_only-adjacent modes
    WRITE = "write"  # file/plan/memory mutation
    EXEC = "exec"  # shell, git push, spawn/fanout
    NETWORK = "network"  # web_fetch/search, MCP side-effects unknown
    PRIVILEGED = "privileged"  # shutdown, trust, auth material


@dataclass(frozen=True)
class ApprovalDecision:
    """Result of a single approval check. Immutable, auditable."""

    allowed: bool
    reason: str = ""
    modified_args: Optional[dict[str, Any]] = None
    risk: ToolRisk = ToolRisk.READ

    def to_tuple(self) -> tuple[bool, Optional[str]]:
        """Back-compat with ``ApprovalGate.check() -> (bool, reason|None)``."""
        return (self.allowed, None if self.allowed else (self.reason or "denied"))


# Canonical risk table. Phase 2 generated from TOOL_SCHEMAS names so the
# matrix cannot drift from the registry (current drift source: three
# hand-maintained frozensets in ``infra/security.py:38-56``).
TOOL_RISK_TABLE: dict[str, ToolRisk] = {
    "read_file": ToolRisk.READ,
    "list_files": ToolRisk.READ,
    "search_codebase": ToolRisk.READ,
    "search_symbols": ToolRisk.READ,
    "git_status": ToolRisk.READ,
    "git_diff": ToolRisk.READ,
    "lsp_diagnostics": ToolRisk.READ,
    "lsp_definition": ToolRisk.READ,
    "lsp_references": ToolRisk.READ,
    "lsp_hover": ToolRisk.READ,
    "lsp_symbols": ToolRisk.READ,
    "web_fetch": ToolRisk.NETWORK,
    "web_search": ToolRisk.NETWORK,
    "recall": ToolRisk.READ,
    "remember": ToolRisk.WRITE,
    "write_file": ToolRisk.WRITE,
    "edit_file": ToolRisk.WRITE,
    "edit_file_multi": ToolRisk.WRITE,
    "plan_task": ToolRisk.WRITE,
    "mark_step_done": ToolRisk.WRITE,
    "update_plan": ToolRisk.WRITE,
    "run_bash": ToolRisk.EXEC,
    "run_tests": ToolRisk.EXEC,
    "git_branch": ToolRisk.EXEC,
    "git_commit": ToolRisk.EXEC,
    "git_push": ToolRisk.EXEC,
    "gh_pr_create": ToolRisk.EXEC,
    "spawn": ToolRisk.EXEC,
    "fanout": ToolRisk.EXEC,
    "diagnose": ToolRisk.READ,
    # Orchestration + subagent lifecycle (Phase 2.4, D6): delegate execution
    # to other agents — EXEC risk, always approval-gated like spawn/fanout.
    "orchestrate_vote": ToolRisk.EXEC,
    "orchestrate_map_reduce": ToolRisk.EXEC,
    "orchestrate_chain": ToolRisk.EXEC,
    "orchestrate_dag": ToolRisk.EXEC,
    "spawn_background": ToolRisk.EXEC,
    "subagent_wait": ToolRisk.EXEC,
    "subagent_list": ToolRisk.EXEC,
    "subagent_result": ToolRisk.EXEC,
    "subagent_send": ToolRisk.EXEC,
    "subagent_cancel": ToolRisk.EXEC,
    # Workflow capture writes skill files — WRITE risk.
    "capture_skill": ToolRisk.WRITE,
}


def risk_for_tool(name: str) -> ToolRisk:
    """Classify a tool name; unknown tools default to EXEC (fail-closed)."""
    return TOOL_RISK_TABLE.get(name, ToolRisk.EXEC)


# ── Turn / session budget (D1, D10) ────────────────────────────────────


@dataclass(frozen=True)
class TurnBudget:
    """Wall-clock + iteration ceiling for one turn."""

    max_iterations: int = 50
    turn_timeout_s: float = 1800.0

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not 1 <= self.max_iterations <= 200:
            errors.append("max_iterations must be within 1..200")
        if self.turn_timeout_s <= 0:
            errors.append("turn_timeout_s must be positive")
        return errors


@dataclass
class SessionState:
    """Minimal deterministic session shape for pruning/budget decisions.

    Phase 2 migrates ``AgentRuntime`` session dicts onto this schema so
    pruning is governed, not ad-hoc (D3). Mutable by design — the runtime
    owns it; the core borrows it per turn.
    """

    session_id: str
    model: str = ""
    workspace: str = "."
    messages: list[dict[str, Any]] = field(default_factory=list)
    allowed_tools: Optional[list[str]] = None

    def user_turns(self) -> int:
        return sum(1 for m in self.messages if m.get("role") == "user")
