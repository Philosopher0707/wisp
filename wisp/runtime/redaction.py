"""Redaction happens on the TRACE, never on the PROMPT — and the trace pays for it.

## The decision

    Redact the TRACE, never the PROMPT.

They are mutually exclusive: the same edit at two different moments, and whichever
one you take, you lose the other property. **This module takes the trace side.**
An earlier version of it took the prompt side; the inversion is recorded rather than
quietly rewritten, because the two are close enough that a reader deserves to know
which one is live and why.

| | prompt redaction | **trace redaction (this)** |
|---|---|---|
| the model sees | `[REDACTED]` — it cannot use the value | the real value — it can work |
| the stored trace | faithful, replayable | redacted, **not replayable** |
| what you lose | the tool's function | replay |

**Why the trace side.** A coding agent is handed secrets it must *use* — a token to
call a tool with, a connection string to run a command against. Redacting the prompt
does not protect those; it disables them, and the agent then fails in a way that
looks like a broken tool. The trace is read by humans and by retention policy, and
it is the thing that must not hold the secret at rest.

## The cost, and it is not hypothetical

**A redacted trace is unrunnable.** `wisp/core/replay_digest.py` verifies that a
replayed transcript digests to what the run recorded; a redacted trace cannot, by
construction. So the trace must **declare** it and replay must refuse it **by name**
(`REPLAY_UNAVAILABLE_REASON`) rather than raising a divergence that reads like a
corruption bug. A trace that silently cannot be replayed is worse than one that says
so.

## A second cost this decision carries, named because the table does not

*Never redact the prompt* means the secret goes **into the prompt**, and therefore
**to the provider** — a third party, over the network, into a request log that is
not yours. Trace redaction protects the copy at rest and does nothing about the copy
in transit. If the value must not leave the machine, the answer is not a redaction
point: it is to give the tool a **credential reference** the model can name without
holding, so the real value never enters the prompt.

## Where the authority for *which* fields are sensitive lives

`redact_sensitive_tool_args` (`wisp/infra/security.py`). This module decides **when**
it is applied; it does not decide **what** it covers.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable


class RedactionPoint(StrEnum):
    """When a redaction is applied relative to the record.

    **`TRACE` exists only to be refused.** It is spelled out so a caller that
    reaches for it gets a named error rather than a silent divergence three turns
    later, and so the reason is readable in the enum rather than only in a commit
    message.
    """

    #: Before the model sees it. **Refused** — see `redact`.
    PROMPT = "prompt"
    #: After the model has seen it, when the trace is written. The live choice.
    TRACE = "trace"


class RedactionPointError(RuntimeError):
    """A redaction was requested at the point this project has decided against."""

    code = "redaction_point_invalid"


#: The reason a redacted trace cannot be replayed. A **named constant**, so replay
#: refuses it by name rather than by a `ReplayDivergence` — which would read as a
#: corruption bug instead of a known, priced cost.
REPLAY_UNAVAILABLE_REASON = (
    "this trace was redacted at rest, so it cannot be replayed: the recorded "
    "transcript deliberately differs from the one the run used. Replay requires "
    "fidelity and redaction requires the opposite — the project chose redaction and "
    "accepted this cost (see wisp/runtime/redaction.py)."
)


Redactor = Callable[[Any], Any]


def redact(value: Any, *, point: RedactionPoint,
           redactor: Redactor | None = None) -> Any:
    """Apply `redactor` at `point`, refusing the point that breaks replay.

    With no redactor this is the identity, so a caller can wire the point once and
    leave the policy to configuration — the same shape as the rest of this project:
    the core is generic, the capability is configuration.
    """
    if point is RedactionPoint.PROMPT:
        raise RedactionPointError(
            "the PROMPT is never redacted: the model is handed secrets it must USE, "
            "and redacting them does not protect the value — it disables the tool, "
            "and the failure looks like a broken tool rather than a policy. Redact "
            "the TRACE instead and accept that it is not replayable; or, if the "
            "value must not reach the provider either, give the tool a credential "
            "reference it can name without holding."
        )
    return redactor(value) if redactor is not None else value


def prompt_value(value: Any) -> Any:
    """What the model sees: **the value, unredacted**.

    A named function rather than an omission, so the decision is visible at the
    place a reader would look for a redaction that is not there.
    """
    return value


def recorded_value(value: Any, *, redactor: Redactor | None = None) -> Any:
    """What the trace stores: redacted once, at the one place a record is written.

    A named helper rather than an inline call because a second place is how a trace
    acquires an *unredacted* value — the failure this decision exists to prevent,
    arriving by the back door.
    """
    return redact(value, point=RedactionPoint.TRACE, redactor=redactor)


def is_replayable(*, redacted: bool) -> bool:
    """Whether a trace may be replayed. **False the moment it was redacted.**

    The whole cost of this decision, as one function: it cannot be true both ways,
    and a caller that wants replay must not redact.
    """
    return not redacted


__all__ = ["RedactionPoint", "RedactionPointError", "REPLAY_UNAVAILABLE_REASON",
           "Redactor", "redact", "prompt_value", "recorded_value", "is_replayable"]
