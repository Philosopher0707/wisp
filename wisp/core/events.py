"""Event system for Wisp SDK — structured events emitted by the agent core.

All I/O (printing, logging, WebSocket pushes) is handled by transports that
subscribe to these events. The core itself is pure logic.

Schema version 1 — all events carry trace context and strict types.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional


EVENT_SCHEMA_VERSION = 1


class EventType(StrEnum):
    """Type-safe event type constants.

    Backward-compatible with existing TYPE_* string constants.
    Can be used anywhere a string is expected.
    """
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CONTENT = "content"
    ERROR = "error"
    DONE = "done"
    SYSTEM = "system"
    APPROVAL_REQUEST = "approval_request"
    STEERING_PAUSED = "steering_paused"
    STEERING_INJECT = "steering_inject"
    STEERING_RESUMED = "steering_resumed"
    PROVIDER_STATUS = "provider_status"
    SUBAGENT = "subagent"


# ── Backward-compatible aliases ──────────────────────────────────────
TYPE_THINKING = EventType.THINKING
TYPE_TOOL_CALL = EventType.TOOL_CALL
TYPE_TOOL_RESULT = EventType.TOOL_RESULT
TYPE_CONTENT = EventType.CONTENT
TYPE_ERROR = EventType.ERROR
TYPE_DONE = EventType.DONE
TYPE_SYSTEM = EventType.SYSTEM
TYPE_APPROVAL_REQUEST = EventType.APPROVAL_REQUEST
TYPE_STEERING_PAUSED = EventType.STEERING_PAUSED
TYPE_STEERING_INJECT = EventType.STEERING_INJECT
TYPE_STEERING_RESUMED = EventType.STEERING_RESUMED


@dataclass(frozen=True)
class AgentEvent:
    """A single event emitted by the agent during a turn.

    Attributes:
        type: Event category — one of the EventType enum values.
        data: Payload dict (structure depends on type).
        timestamp: Monotonic timestamp for ordering and latency tracking.
        trace_id: UUID7 trace ID — shared by all events in a turn.
        span_id: UUID7 span ID — unique per event within a trace.
        schema_version: Event schema version for forward compatibility.
    """

    type: EventType | str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)
    trace_id: str = ""
    span_id: str = ""
    schema_version: int = EVENT_SCHEMA_VERSION

    # ── Convenience accessors ──────────────────────────────────────

    @property
    def text(self) -> str:
        """Shortcut for content/thinking events."""
        return str(self.data.get("text", ""))

    @property
    def tool_name(self) -> str:
        """Shortcut for tool_call / tool_result events."""
        return str(self.data.get("name", ""))

    @property
    def is_final(self) -> bool:
        """True if this event signals the end of a turn."""
        return self.type in (TYPE_DONE, TYPE_ERROR)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-friendly dict.

        Canonical format: {"type": str, "data": {...}, "timestamp": float,
                           "trace_id": str, "span_id": str, "schema_version": int}
        """
        d: dict[str, Any] = {
            "type": str(self.type),
            "data": self.data,
            "timestamp": self.timestamp,
            "schema_version": self.schema_version,
        }
        if self.trace_id:
            d["trace_id"] = self.trace_id
        if self.span_id:
            d["span_id"] = self.span_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentEvent:
        """Deserialize from dict (round-trips with to_dict).

        Handles both canonical format ({type, data}) and flat format
        ({type, text, ...}) for backward compatibility.
        """
        ev_type = data.get("type", "")
        ev_data = data.get("data")
        trace_id = data.get("trace_id", "")
        span_id = data.get("span_id", "")
        schema_ver = data.get("schema_version", EVENT_SCHEMA_VERSION)

        if ev_data is not None:
            return cls(
                type=ev_type,
                data=dict(ev_data),
                timestamp=data.get("timestamp", 0.0),
                trace_id=trace_id,
                span_id=span_id,
                schema_version=schema_ver,
            )

        flat_data = {k: v for k, v in data.items()
                     if k not in ("type", "timestamp", "trace_id", "span_id", "schema_version")}
        return cls(
            type=ev_type,
            data=flat_data,
            timestamp=data.get("timestamp", 0.0),
            trace_id=trace_id,
            span_id=span_id,
            schema_version=schema_ver,
        )


# ── Event normalizer ──────────────────────────────────────────────

# ── The ONE provider-object canonicalization implementation (ADR-0039) ──
#
# ADR-0039 R2: exactly one canonicalizer for provider objects shall exist.
# The whitelist below is that one implementation. `WispAgentCore._normalize_event`
# and `normalize_event` both delegate here, so no second whitelist can exist to
# drift from this one — F42 was precisely that drift (a 14-field copy that
# silently dropped `calls` and `done_reason`).
#
# The whitelist is deliberately narrow: it drops provider-internal metadata
# (`batch_index`, `phase`, `validation_hash`, `total_tokens`, `error_type`,
# `partial_*`, `final_*`, `accumulated_*`). ADR-0039 measured every dropped
# field and found zero consumers outside the providers, so the projection is
# minimal but adequate — and the schema is deliberately NOT expanded.
CANONICAL_EVENT_FIELDS: frozenset[str] = frozenset({
    "text",
    "name",
    "arguments",
    "result",
    "message",
    "duration_ms",
    "turns",
    "session_id",
    "summary",
    "reason",
    "level",
    "recoverable",
    "tool_call_id",
    "id",
    "calls",
    "done_reason",
})


def canonical_event(event: Any) -> dict[str, Any]:
    """Project any provider event to the canonical flat representation.

    **Total** (ADR-0039 R3): returns a dict for ANY input and never raises.
    A dict is copied (the copy is load-bearing — the main loop mutates the
    result, and mutating a provider's own dict would corrupt shared state);
    a provider object contributes `type`/`phase` plus the whitelisted
    fields; anything else becomes ``type="unknown"``.

    This is data transformation only (ADR-0039 R11): it may remove keys,
    never add them, and it executes nothing.
    """
    if isinstance(event, dict):
        return dict(event)

    result: dict[str, Any] = {}
    if hasattr(event, "type"):
        result["type"] = event.type
    elif hasattr(event, "phase"):
        result["type"] = event.phase
    else:
        result["type"] = "unknown"

    for field_name in CANONICAL_EVENT_FIELDS:
        if hasattr(event, field_name):
            result[field_name] = getattr(event, field_name)

    return result


def normalize_event(event: Any) -> AgentEvent:
    """Normalize any event representation to a canonical AgentEvent.

    Accepts:
      - AgentEvent instances (returned as-is)
      - Canonical dicts: {"type": "content", "data": {"text": "..."}}
      - Flat dicts: {"type": "content", "text": "..."}
      - Provider objects with type/phase + attributes

    Returns:
        Canonical AgentEvent with all payload in the data dict.

    Provider objects are projected by :func:`canonical_event` — the single
    implementation. This function owns **no whitelist of its own** (ADR-0039
    R2), so it cannot drift from the core's canonicalization.
    """
    if isinstance(event, AgentEvent):
        return event

    if isinstance(event, dict):
        return AgentEvent.from_dict(event)

    # Provider object (TokenBatch, Checkpoint, ...) — delegate, do not
    # re-implement. Keeping a second whitelist here is what F42 was.
    return AgentEvent.from_dict(canonical_event(event))


# Human-readable descriptions
_EVENT_DESCRIPTIONS: dict[str, str] = {
    EventType.THINKING: "Model reasoning trace",
    EventType.TOOL_CALL: "Tool invocation",
    EventType.TOOL_RESULT: "Tool execution result",
    EventType.CONTENT: "Assistant text response",
    EventType.ERROR: "Error occurred",
    EventType.DONE: "Turn complete",
    EventType.SYSTEM: "System notification",
    EventType.APPROVAL_REQUEST: "User approval required",
    EventType.PROVIDER_STATUS: "Provider availability change",
    EventType.SUBAGENT: "Subagent lifecycle update",
}


def describe_event_type(event_type: EventType | str) -> str:
    """Return a human description for an event type."""
    return _EVENT_DESCRIPTIONS.get(str(event_type), "Unknown event")


# ── Trace context helpers ──────────────────────────────────────────

def _trace_ctx() -> tuple[str, str]:
    """Return (trace_id, span_id) from contextvars, or (\"\", \"\")."""
    try:
        from wisp.infra.tracing import current_trace_id, current_span_id
        return current_trace_id() or "", current_span_id() or ""
    except ImportError:
        return "", ""


def _make_event(event_type: EventType | str, data: dict[str, Any]) -> AgentEvent:
    """Create an AgentEvent with trace context and schema version auto-populated."""
    tid, sid = _trace_ctx()
    return AgentEvent(
        type=event_type,
        data=data,
        trace_id=tid,
        span_id=sid,
        schema_version=EVENT_SCHEMA_VERSION,
    )


# ── Event builders (convenience factories) ─────────────────────────

def thinking(text: str) -> AgentEvent:
    return _make_event(TYPE_THINKING, {"text": text})


def tool_call(name: str, args: dict[str, Any]) -> AgentEvent:
    return _make_event(TYPE_TOOL_CALL, {"name": name, "arguments": args})


def tool_result(name: str, result: str | dict[str, Any], duration_ms: Optional[float] = None, *, auto_approved: bool = False, tool_call_id: Optional[str] = None) -> AgentEvent:
    payload: dict[str, Any] = {"name": name, "result": result}
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if auto_approved:
        payload["auto_approved"] = True
    if tool_call_id is not None:
        payload["tool_call_id"] = tool_call_id
    return _make_event(TYPE_TOOL_RESULT, payload)


# ── Structured denial envelope (13F.1 R2) ──────────────────────────
# Machine-readable denial statuses. Execution failures keep their
# existing shapes; only denials/refusals use this envelope so the model
# can distinguish POLICY_DENIED / USER_DENIED / APPROVAL_TIMEOUT /
# CANCELLED from ordinary failure.
DENIAL_POLICY_DENIED = "POLICY_DENIED"
DENIAL_USER_DENIED = "USER_DENIED"
DENIAL_APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"
DENIAL_CANCELLED = "CANCELLED"
DENIAL_SCHEMA_INVALID = "SCHEMA_INVALID"
#: The run met its declared cost ceiling, so no further tool call is made.
#: Distinct from POLICY_DENIED for the same reason a no-approver refusal is:
#: "the run is out of budget" is a fact about the RUN, not a judgement about
#: this tool or this call — and a caller that cannot tell them apart cannot
#: decide whether to reconfigure or to ask a human.
DENIAL_BUDGET_EXCEEDED = "BUDGET_EXCEEDED"

_DENIAL_STATUSES = frozenset({
    DENIAL_POLICY_DENIED, DENIAL_USER_DENIED,
    DENIAL_APPROVAL_TIMEOUT, DENIAL_CANCELLED,
    DENIAL_SCHEMA_INVALID, DENIAL_BUDGET_EXCEEDED,
})


# ── Canonical outcome classification ────────────────────────────────
# ONE authority for "what kind of outcome is this?". Consumers classify
# through here rather than re-deriving predicates — a second classifier is
# what let benchmark error accounting miss every structured denial.
#
# The taxonomy exists because a binary error/not-error answer is not enough:
# a POLICY_DENIED result is a failure that must NOT be retried, while a
# transient ERROR may be. Callers needing that distinction read the class,
# not the message text.


class OutcomeClass(StrEnum):
    """What a tool result represents. Exactly one applies."""

    SUCCESS = "success"
    ERROR = "error"                   # execution failure
    DENIAL = "denial"                 # the user declined
    POLICY_DENIAL = "policy_denial"   # policy/authority refused
    TIMEOUT = "timeout"               # approval or execution lapsed
    CANCELLATION = "cancellation"     # cancelled by user or caller
    INVALID = "invalid"               # schema/argument rejection
    UNKNOWN = "unknown"               # an unrecognised non-"ok" status


#: The tool-result envelope's status vocabulary, mapped to its class.
#: Every status `denial_result()` can emit appears here; "ok" is the only
#: success. Any other non-"ok" status is UNKNOWN — still a failure.
OUTCOME_BY_STATUS: dict[str, OutcomeClass] = {
    "ok": OutcomeClass.SUCCESS,
    "error": OutcomeClass.ERROR,
    DENIAL_POLICY_DENIED: OutcomeClass.POLICY_DENIAL,
    DENIAL_USER_DENIED: OutcomeClass.DENIAL,
    DENIAL_APPROVAL_TIMEOUT: OutcomeClass.TIMEOUT,
    DENIAL_CANCELLED: OutcomeClass.CANCELLATION,
    DENIAL_SCHEMA_INVALID: OutcomeClass.INVALID,
}

#: Classes that are terminal for automatic retry. A denial is a *verdict*,
#: not a blip, so re-issuing it is never right (§26).
TERMINAL_OUTCOME_CLASSES = frozenset({
    OutcomeClass.DENIAL,
    OutcomeClass.POLICY_DENIAL,
    OutcomeClass.TIMEOUT,
    OutcomeClass.CANCELLATION,
    OutcomeClass.INVALID,
})

#: Legacy text prefixes that predate the structured envelope, each mapped to
#: a class so a text-only result classifies as precisely as a structured one.
_ERROR_TEXT_MARKERS: tuple[tuple[str, OutcomeClass], ...] = (
    ("[Denied", OutcomeClass.DENIAL),
    ("[Blocked", OutcomeClass.POLICY_DENIAL),
    ("[Cancelled", OutcomeClass.CANCELLATION),
    ("Error", OutcomeClass.ERROR),
    ("[Error", OutcomeClass.ERROR),
    ("[WEB_FETCH_FAILED]", OutcomeClass.ERROR),
    ("[WEB_FETCH_BLOCKED]", OutcomeClass.POLICY_DENIAL),
    ("ToolError:", OutcomeClass.ERROR),
    ("Unexpected error:", OutcomeClass.ERROR),
)

#: Prose phrasings of a denial. These predate the taxonomy and are kept as a
#: fallback — but they are checked *after* the canonical status tokens,
#: because none of them match "POLICY_DENIED".
_PROSE_DENIAL_MARKERS: tuple[str, ...] = (
    "[denied", "denied by", "approval denied", "not authorized",
)


#: Prefixes that mark an **engine-level refusal** (migration M12). The engine
#: refuses a tool call before dispatch — role restriction, schema, gate,
#: extension — and emits the refusal as an `error` event whose text begins with
#: one of these. They are denials by construction.
#:
#: **A prefix, not a substring.** `_PROSE_DENIAL_MARKERS` are substrings and
#: deliberately stay that way; these are matched with `startswith`, so
#: "the write was not blocked: it succeeded" is not read as a refusal.
#:
#: Why this is here and not in the caller: `is_denial_text` is the ONE authority
#: for "is this text a denial". F15 was the prose markers matching nothing real;
#: this is the same defect from the other side — the canonical statuses matched,
#: and the *engine's own* marker was missing, so every engine refusal was
#: invisible to denial detection and got retried.
_ENGINE_DENIAL_PREFIXES: tuple[str, ...] = (
    "blocked:",
    "extension intercept failed:",
)


def classify_status(status: str) -> OutcomeClass:
    """Classify a result-envelope status value."""
    if status in OUTCOME_BY_STATUS:
        return OUTCOME_BY_STATUS[status]
    return OutcomeClass.SUCCESS if status == "ok" else OutcomeClass.UNKNOWN


def classify_text(text: str) -> OutcomeClass:
    """Classify a free-text result/error string.

    Parses a JSON envelope when the text is one (slow tools surface as JSON
    strings), then falls back to the legacy prefix markers.
    """
    stripped = (text or "").strip()
    if stripped.startswith("{"):
        try:
            import json as _json
            parsed = _json.loads(stripped)
            if isinstance(parsed, dict):
                return classify_status(str(parsed.get("status", "ok")))
        except (ValueError, TypeError):
            pass
    for prefix, cls in _ERROR_TEXT_MARKERS:
        if stripped.startswith(prefix):
            return cls
    return OutcomeClass.SUCCESS


def classify_result(result: Any) -> OutcomeClass:
    """The single authority for what a tool result represents."""
    if isinstance(result, dict):
        return classify_status(str(result.get("status", "ok")))
    if isinstance(result, str):
        return classify_text(result)
    return OutcomeClass.SUCCESS


def is_error_outcome(result: Any) -> bool:
    """True when `result` is not a success. The binary view of the taxonomy."""
    return classify_result(result) is not OutcomeClass.SUCCESS


def is_terminal_outcome(result: Any) -> bool:
    """True when the outcome is a verdict that must never be auto-retried."""
    return classify_result(result) in TERMINAL_OUTCOME_CLASSES


def is_denial_text(text: str | None) -> bool:
    """True when free text names a denial — structured status or prose.

    The canonical status tokens are checked FIRST. The prose markers predate
    the taxonomy and none of them match a structured status such as
    "POLICY_DENIED", so a structured denial arriving as text was previously
    invisible to denial detection.
    """
    if not text:
        return False
    upper = text.upper()
    if any(status in upper for status in _DENIAL_STATUSES):
        return True
    lowered = text.lower()
    if any(lowered.startswith(p) for p in _ENGINE_DENIAL_PREFIXES):
        return True
    return any(m in lowered for m in _PROSE_DENIAL_MARKERS)


def is_denial_outcome(result: Any) -> bool:
    """True when an envelope result is a denial/refusal of any kind."""
    return classify_result(result) in TERMINAL_OUTCOME_CLASSES


def denial_result(name: str, status: str, reason: str, *,
                  duration_ms: float = 0,
                  tool_call_id: Optional[str] = None) -> AgentEvent:
    """Build a structured denial tool result.

    The result dict carries status/authorized/executed/retryable/reason
    (all denials: authorized=False, executed=False, retryable=False)
    plus a human-readable `data` line; history serialization keeps it
    intact (dict → JSON) and the shared error predicate treats any
    non-"ok" status as failure. tool_call_id is forwarded verbatim —
    never invented here.
    """
    if status not in _DENIAL_STATUSES:
        raise ValueError(f"unknown denial status: {status!r}")
    return tool_result(
        name,
        {"status": status, "authorized": False, "executed": False,
         "retryable": False, "reason": reason, "data": reason,
         "hint": "Denial is final for these arguments: do not retry the "
                 "identical call, and do not claim you ran the tool."},
        duration_ms=duration_ms,
        tool_call_id=tool_call_id,
    )


#: The capability-failure envelope's status. Deliberately **not** a member of
#: `_DENIAL_STATUSES` (ADR-0052): a host that cannot validate the arguments has
#: not *denied* anything. `denial_result()`'s payload asserts three things about
#: the ARGUMENTS — authorized=False, executed=False, retryable=False — and its
#: hint says *"Denial is final for these arguments"*. None of that is true here:
#: no authorization decision was made and the arguments were never examined.
CAPABILITY_FAILURE_STATUS = "error"

#: The key the failure's kind travels under, inside the envelope's `data`, so a
#: caller at the published boundary distinguishes a system failure from a data
#: failure **without parsing prose**.
CAPABILITY_KIND_KEY = "kind"


def capability_failure_result(name: str, reason: str, kind: str, *,
                              duration_ms: float = 0,
                              tool_call_id: Optional[str] = None) -> AgentEvent:
    """A tool result for a failure of the **host**, not of the arguments (ADR-0052).

    `_validate_tool_args` produces a `ValidationFailure` whose `kind` is
    `CAPABILITY_MISSING` when the validator could not run at all. Publishing that
    as a denial (`SCHEMA_INVALID`) told the model its arguments were rejected when
    they had never been examined — the attribution was correct where the failure
    was *produced* and wrong where it was *published*.

    The status is `error` (`OutcomeClass.ERROR` — a system failure, and not in
    `TERMINAL_OUTCOME_CLASSES`, so it is retryable in principle once the
    environment is fixed) and the `kind` travels in `data`. `retryable` is `False`
    because the *identical* call fails identically until the environment changes —
    the same statement the failure's own text makes.

    `authorized` is `None`, not `False`: no authorization decision was reached, and
    recording "not authorized" would be a claim nothing made.
    """
    return tool_result(
        name,
        {"status": CAPABILITY_FAILURE_STATUS,
         "capability": "tool_argument_validation",
         CAPABILITY_KIND_KEY: kind,
         "authorized": None, "executed": False, "retryable": False,
         "reason": reason, "data": reason,
         "hint": "The host could not validate these arguments, so they were never "
                 "checked. This is not a verdict on them. Do not retry the "
                 "identical call — it will fail the same way until the "
                 "environment is fixed."},
        duration_ms=duration_ms,
        tool_call_id=tool_call_id,
    )


def content(text: str) -> AgentEvent:
    return _make_event(TYPE_CONTENT, {"text": text})

# Error-code ranges (docs/repl-aesthetics.md §3): E11xx provider/stream,
# E21xx tool execution, E31xx approval/permission, E41xx session/state,
# E51xx child/orchestration. W-codes mark recoverable (inline warn) cases.
CODE_TURN_TIMEOUT = "E1101"
CODE_PROVIDER_STREAM = "E1102"
CODE_TOOL_TIMEOUT = "E2103"
CODE_ITERATION_BUDGET = "E5101"


def error(
    message: str,
    recoverable: bool = True,
    code: str | None = None,
    hint: str | None = None,
    context: list[str] | None = None,
) -> AgentEvent:
    """Fatal-turn error; structured fields render rustc-style when set."""
    data: dict[str, Any] = {"message": message, "recoverable": recoverable}
    if code:
        data["code"] = code
    if hint:
        data["hint"] = hint
    if context:
        data["context"] = [str(c)[:120] for c in context]
    return _make_event(TYPE_ERROR, data)


def done(session_id: str, turns: int = 0, summary: str = "", reason: str = "natural") -> AgentEvent:
    return _make_event(TYPE_DONE, {"session_id": session_id, "turns": turns, "summary": summary, "reason": reason})


def system(message: str, level: str = "info") -> AgentEvent:
    return _make_event(TYPE_SYSTEM, {"message": message, "level": level})


def steering_paused(reason: str = "User paused") -> AgentEvent:
    return _make_event(TYPE_STEERING_PAUSED, {"reason": reason})


def steering_resumed() -> AgentEvent:
    return _make_event(TYPE_STEERING_RESUMED, {})


def steering_feedback(text: str) -> AgentEvent:
    return _make_event(TYPE_STEERING_INJECT, {"text": text})


def nudge_message(text: str) -> dict[str, Any]:
    """User-role message shape for provider-injected system context (mirrors system() yields)."""
    return {"role": "user", "content": text}


def steering_message(note: str) -> dict[str, Any]:
    """User-role message shape for steering injections (mirrors steering_feedback yields)."""
    return {"role": "user", "content": f"[steering] {note.strip()}"}


def approval_request(tool_name: str, args: dict[str, Any], reason: str = "") -> AgentEvent:
    return _make_event(TYPE_APPROVAL_REQUEST, {"name": tool_name, "arguments": args, "reason": reason})


def provider_status(status: str, detail: str = "", retry_after: Optional[float] = None) -> AgentEvent:
    """Provider availability signal (circuit breaker lifecycle).

    status: "circuit_open" or "circuit_closed". retry_after carries the
    seconds until recovery is attempted when the circuit opens, so
    transports can render honest expectations instead of a hang.
    """
    data: dict[str, Any] = {"status": status, "detail": detail}
    if retry_after is not None:
        data["retry_after"] = retry_after
    return _make_event(EventType.PROVIDER_STATUS, data)


def subagent(
    kind: str,
    name: str = "",
    role: str = "",
    detail: str = "",
    **extra: Any,
) -> AgentEvent:
    """Subagent lifecycle signal from the orchestrator's progress channel.

    kind mirrors multi_agent.task.EventKind values (task_started,
    task_progress, task_completed, task_failed, task_retry, done) so the
    orchestrator converts without remapping. detail is a short human
    fragment; structured fields ride along as extra keys.
    """
    data: dict[str, Any] = {"kind": kind, "name": name, "role": role, "detail": detail}
    data.update(extra)
    return _make_event(EventType.SUBAGENT, data)


__all__ = [
    "AgentEvent",
    "EventType",
    "EVENT_SCHEMA_VERSION",
    "TYPE_THINKING",
    "TYPE_TOOL_CALL",
    "TYPE_TOOL_RESULT",
    "TYPE_CONTENT",
    "TYPE_ERROR",
    "TYPE_DONE",
    "TYPE_SYSTEM",
    "TYPE_APPROVAL_REQUEST",
    "TYPE_STEERING_PAUSED",
    "TYPE_STEERING_INJECT",
    "TYPE_STEERING_RESUMED",
    "describe_event_type",
    "thinking",
    "tool_call",
    "tool_result",
    "denial_result",
    "content",
    "error",
    "done",
    "system",
    "steering_paused",
    "steering_resumed",
    "steering_feedback",
    "nudge_message",
    "steering_message",
    "approval_request",
    "provider_status",
    "subagent",
    "normalize_event",
    "canonical_event",
    "CANONICAL_EVENT_FIELDS",
]
