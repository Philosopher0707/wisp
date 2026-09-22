"""The proposal boundary (migration P2).

Reasoning produces a **proposal**; validation disposes of it. This module
supplies the two record types that make that boundary observable, built on the
contracts that already existed and had no producer:

| Record | Contract | Produced |
|---|---|---|
| `ToolRequest` | `contracts/tool.py` | for every tool call, **before** dispatch |
| `ToolResult` | `contracts/tool.py` | for every proposal, **including rejections** |

**What this is not.** It is not a second decision procedure. The gates in
`ToolExecutor` (19 of them) keep their exact semantics and order — see
`tests/test_gate_order_corpus.py`, written RED-first to pin that. This layer
adds a *record* of what the gates decided, not a decision of its own.

**Why a record at all.** Before P2 a rejection was observable only as a
side-effect of the tool result the model happened to receive, and an allowed
call left no trace that validation had run. Neither could be queried,
replayed, or audited as a first-class event.

**Status derivation is delegated.** `classify_result()` in `core/events.py` is
the ONE authority for "what kind of outcome is this?" — this module maps its
`OutcomeClass` onto the frozen `ToolResult.STATUSES` vocabulary rather than
re-deriving any predicate. A second classifier is what let benchmark error
accounting miss every structured denial in the first place.
"""
from __future__ import annotations

from typing import Any

from wisp.contracts.tool import ToolRequest, ToolResult
from wisp.core.events import OutcomeClass, classify_result

#: `OutcomeClass` → the frozen `ToolResult.STATUSES` vocabulary.
#: Deliberately total: every class maps, so no outcome can be silently
#: un-representable. `TIMEOUT` maps to `cancelled` rather than `error`
#: because an approval that lapsed is not a tool failure — the tool never
#: ran, and calling it an error would invite a retry that repeats a prompt.
#:
#: The mapping is LOSSY in one direction: `STATUSES` has four values and
#: `OutcomeClass` has eight, so a consumer needing the precise class must read
#: `outcome_class` from the record's metadata rather than infer it from
#: `status`. That field is always populated.
OUTCOME_STATUS: dict[OutcomeClass, str] = {
    OutcomeClass.SUCCESS: "ok",
    OutcomeClass.ERROR: "error",
    OutcomeClass.DENIAL: "denied",
    OutcomeClass.POLICY_DENIAL: "denied",
    OutcomeClass.TIMEOUT: "cancelled",
    OutcomeClass.CANCELLATION: "cancelled",
    OutcomeClass.INVALID: "denied",
    OutcomeClass.UNKNOWN: "error",
}

#: `OutcomeClass` → the frozen `ToolResult.BLOCK_REASONS` vocabulary.
#: Coarse by construction: the block-reason vocabulary predates the outcome
#: taxonomy and has five values, so every refusal folds into "permission"
#: except the two flow-control blocks a tool result never carries.
#: `outcome_class` in metadata is the precise signal.
BLOCK_REASON: dict[OutcomeClass, str] = {
    OutcomeClass.DENIAL: "permission",
    OutcomeClass.POLICY_DENIAL: "permission",
    OutcomeClass.TIMEOUT: "permission",
    OutcomeClass.CANCELLATION: "permission",
    OutcomeClass.INVALID: "permission",
}


def build_proposal(call_id: str, name: str, args: Any,
                   action_key: str = "",
                   principal_id: str = "",
                   correlation_id: str = "") -> ToolRequest:
    """Build the proposal record for one tool call.

    `action_key` is the canonical idempotency digest from migration P1, so a
    proposal and its later outcome are joinable by a key that is stable across
    a crash and a resume — which is what makes "was this already dispatched?"
    answerable from the durable record alone.
    """
    return ToolRequest(
        tool_call_id=str(call_id or ""),
        name=str(name or ""),
        args=dict(args) if isinstance(args, dict) else {"raw": str(args)},
        principal_id=str(principal_id or ""),
        correlation_id=str(correlation_id or ""),
        idempotency_key=str(action_key or ""),
    )


def build_outcome(call_id: str, name: str, result: Any,
                  synthesized: bool = False,
                  error: str = "") -> ToolResult:
    """Build the outcome record for one proposal — allow or reject.

    Classification goes through `classify_result()`, the canonical authority,
    so an outcome cannot be classified one way here and another way in the
    benchmark or the transport.

    `synthesized` marks the placeholder written for a call that never
    returned; it is surfaced in metadata so a reader can tell "the tool
    failed" from "we never learned what happened".
    """
    outcome_class = classify_result(result)
    status = OUTCOME_STATUS.get(outcome_class, "error")
    metadata: dict[str, Any] = {"outcome_class": outcome_class.value}
    if synthesized:
        metadata["synthesized"] = True
    return ToolResult(
        tool_call_id=str(call_id or ""),
        status=status,
        data=result,
        metadata=metadata,
        error=str(error or ""),
        block_reason=BLOCK_REASON.get(outcome_class, ""),
    )


def is_refusal(outcome: ToolResult) -> bool:
    """True when the outcome records a refusal rather than an execution.

    Reads the precise class from metadata rather than the lossy status, so a
    timeout is not mistaken for a user denial.
    """
    cls = str((outcome.metadata or {}).get("outcome_class", ""))
    return cls in {
        OutcomeClass.DENIAL.value,
        OutcomeClass.POLICY_DENIAL.value,
        OutcomeClass.TIMEOUT.value,
        OutcomeClass.CANCELLATION.value,
        OutcomeClass.INVALID.value,
    }
