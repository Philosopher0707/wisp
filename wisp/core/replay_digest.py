"""Replay verifies; it does not assume.

A session is rebuilt by replaying the journal (`session_repo.reconstruct` →
`Session.replay` → `Session.apply`). Until this module existed, that rebuild was
**trusted**: if `apply` produced a transcript that differed from the one the turn
actually ran on, nothing said so. That is not hypothetical — it is **F25**:

    "The replayed transcript was not the live transcript. `Session.apply` added a
    `name` key to every tool reply that `_exchange_parts` never sets, while
    `runtime.py` states the invariant *'the log has to reproduce `messages`
    exactly'*. The journal and the blob therefore disagreed on the same session."

ADR-0029 fixed that instance and **asserted equality on a real turn** — in a
*test*. A test is not a control: it holds for the turn it drives, and says nothing
about the next one. This module makes the check a **runtime** one.

## The mechanism

At the end of each turn the runtime records a digest of the session's transcript
**as the turn left it**. Replay recomputes the same digest from the transcript
`apply` produced and compares. A mismatch raises `ReplayDivergence`.

So the digest is a claim the *writer* makes about the transcript, and replay is
where it is checked. It is not a digest of the journal — that would be a tautology,
because the journal is read back verbatim. It is a digest of the **state replay
produces**, which is the only thing `apply` can get wrong.

## What this does NOT cover, stated rather than implied

* **The system prompt and the tool descriptors are not journaled**, so they are
  not in the projection and a change to either is invisible here. That is the
  honest limit of this check, and it is the one the runtime skill names as the
  thing to record next.
* **The audit records** (`proposals`, `verdicts`, the task graph, recovery) are
  replayable but are not part of the transcript, so they are out of the
  projection. They have their own consumers and their own guards.
* **Nothing here executes a tool.** The check is over a rebuilt *transcript*, so
  replaying a mutating run cannot repeat the mutation.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

#: The payload key the digest travels under, in the journal and in the event.
#: A module constant so the name appears once — a second spelling is how a
#: record stops being readable.
REPLAY_DIGEST_KEY = "replay_digest"

#: The fields that constitute the transcript's **meaning**, and therefore the
#: projection's subject. Chosen to be the provider-visible surface: what the
#: model sees. A key outside this set is not part of the prompt, so a difference
#: in it is not a divergence — including it would make the check fire on
#: bookkeeping and train the reader to ignore it (F92's class).
_MESSAGE_FIELDS = ("role", "content", "tool_call_id", "name")

#: For an assistant message that carries tool calls, the fields that identify a
#: call. `arguments` is included because it is what the tool was asked to do, and
#: a replay that lost it would produce a call the model never made.
_TOOL_CALL_FIELDS = ("id", "name", "arguments")


class ReplayDivergence(RuntimeError):
    """A replayed transcript does not match the one the turn recorded.

    **Deliberately a plain `RuntimeError`, not the run-level error type.** The
    runtime skill is explicit about why: a divergence must *escape* the loop, not
    be caught and reported as one more way a run can end. A replay that is
    silently reconciled is worse than one that fails, because the whole reason to
    replay is to trust the result.
    """


def canonical_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Project one transcript message onto the fields that carry meaning.

    Ordering inside `tool_calls` is preserved: two calls in a different order are
    a different prompt, and sorting them would hide it.
    """
    projected: dict[str, Any] = {}
    for field in _MESSAGE_FIELDS:
        if field in message and message[field] is not None:
            projected[field] = message[field]
    calls = message.get("tool_calls")
    if isinstance(calls, (list, tuple)) and calls:
        projected["tool_calls"] = [
            {k: call.get(k) for k in _TOOL_CALL_FIELDS if isinstance(call, Mapping) and k in call}
            for call in calls
        ]
    return projected


def projection(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The canonical projection of a transcript — the digest's subject."""
    return [canonical_message(m) for m in messages if isinstance(m, Mapping)]


def projection_digest(messages: Iterable[Mapping[str, Any]]) -> str:
    """A stable digest of a transcript's projection.

    `sort_keys=True` and `ensure_ascii=True` so the digest depends on the
    transcript and not on dict insertion order or the platform's locale. Length
    is prefixed by `sha256` itself; the hex digest is truncated to 32 characters
    because this is compared for equality, never used as a security boundary.
    """
    payload = json.dumps(projection(messages), sort_keys=True, ensure_ascii=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def verify(messages: Iterable[Mapping[str, Any]], recorded: str, *,
           session_id: str = "", sequence: int | None = None) -> None:
    """Raise `ReplayDivergence` unless `messages` digests to `recorded`.

    The message names **both** digests. A guard whose failure says only "mismatch"
    sends the reader to re-derive which side moved; naming them is what makes the
    failure actionable, and it is the same discipline the corpus applies to a
    pinned line.
    """
    actual = projection_digest(messages)
    if actual != recorded:
        where = f" at sequence {sequence}" if sequence is not None else ""
        session = f" (session {session_id})" if session_id else ""
        raise ReplayDivergence(
            f"replay diverged{where}{session}: the turn recorded "
            f"{recorded!r} but replay produced {actual!r}. The journal reproduces "
            "a transcript the turn did not run on — see F25 and "
            "wisp/core/replay_digest.py. This is not a run outcome; it is a "
            "defect in the journal or in Session.apply."
        )


__all__ = [
    "REPLAY_DIGEST_KEY",
    "ReplayDivergence",
    "canonical_message",
    "projection",
    "projection_digest",
    "verify",
]
