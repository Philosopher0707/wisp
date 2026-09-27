"""Redaction has exactly one legal point, and the two candidates are exclusive.

## The claim, and why it is not a preference

    Redact the PROMPT  → the model sees `[REDACTED]`, and the trace records what the
                         model saw. The trace is faithful. Replay verifies.
    Redact the TRACE   → the model saw the real value and the trace holds something
                         else. The trace is a LIE about the run, and replay raises
                         `ReplayDivergence` (`wisp/core/replay_digest.py`).

They cannot both be done, because they are the same edit at two different moments,
and **only one of them leaves the record true**. Redacting the trace is not a
redaction of the trace — it is a redaction of the *prompt* performed after the
prompt has already been used, and the record then describes a run that did not
happen.

So the choice is not *whether* to redact but **where the record is taken relative to
it**, and the answer is forced: **redact before the record**. That is `PROMPT`
below, and it is the only point this module will construct.

## What this costs, stated rather than discovered later

Redacting before the record means **the model does not see the real value either**.
For a secret that is only *displayed* — an approval prompt, a tool-call preview —
that is free. For a secret the model must *use* — a token to call an API with — it
is not: redaction and function are then in direct conflict, and that conflict is a
**product decision about the tool**, not something this module can resolve by
choosing a cleverer point. The honest options are to give the tool a credential
reference the model can name without holding (so the real value never enters the
prompt), or to accept the value in the trace. There is no third.

## Where this sits

`redact_sensitive_tool_args` (`wisp/infra/security.py`) is the **authority for which
fields are sensitive** and is applied today at three display sites — the CLI, the
TUI and the approval prompt. Those are all *prompt-side*, which is why wisp is
consistent today by accident rather than by rule. This module is the rule.
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

    #: Before the value enters the record. The model sees the redacted value and so
    #: does the trace: one value, one truth, replay verifies.
    PROMPT = "prompt"
    #: After the model has seen it. The trace then disagrees with the run.
    TRACE = "trace"


class RedactionPointError(RuntimeError):
    """A redaction was requested at a point that would falsify the record."""

    code = "redaction_point_invalid"


Redactor = Callable[[Any], Any]


def redact(value: Any, *, point: RedactionPoint,
           redactor: Redactor | None = None) -> Any:
    """Apply `redactor` at `point`, refusing the point that breaks replay.

    With no redactor this is the identity, so a caller can wire the point once and
    leave the policy to configuration — the same shape as the rest of this project:
    the core is generic, the capability is configuration.
    """
    if point is RedactionPoint.TRACE:
        raise RedactionPointError(
            "redacting the TRACE is not a redaction of the trace: it is a redaction "
            "of the PROMPT applied after the prompt was already used, so the record "
            "describes a run that did not happen and replay raises "
            "ReplayDivergence (wisp/core/replay_digest.py). Redact before the "
            "record instead — and if the model needs the real value, the tool needs "
            "a credential reference rather than the value, which is a decision "
            "about the tool, not about this function."
        )
    return redactor(value) if redactor is not None else value


def recorded_value(value: Any, *, redactor: Redactor | None = None) -> Any:
    """The value as it must appear in the record: redacted once, before recording.

    A named helper rather than an inline call because this is the **one** place the
    record's value is produced, and a second place is how a trace acquires a value
    the model never saw.
    """
    return redact(value, point=RedactionPoint.PROMPT, redactor=redactor)


__all__ = ["RedactionPoint", "RedactionPointError", "Redactor", "redact",
           "recorded_value"]
