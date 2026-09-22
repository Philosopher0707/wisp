"""Canonical action keys for durable tool idempotency (migration P1).

The audit found **no durable idempotency on the live turn path**: the
`idempotency` table was empty, `Scheduler.memoize`/`already_done` had no
production caller, and the only guard was an in-memory per-turn repeat guard
that dies with the process. A crash after a tool's side effect therefore left
no record that the effect had happened.

This module supplies the missing primitive: a **stable digest of one tool
invocation**. Two identical invocations produce the same key; any difference
in arguments produces a different one. Stamping it onto the `TOOL_CALL` event
turns the append-only journal into a durable intent record — the event is
written *before* dispatch — and stamping the same key onto the matching
`TOOL_RESULT` closes it.

That gives recovery what it needs without a second table: an action whose key
appears in a `TOOL_CALL` but never in a `TOOL_RESULT` was **dispatched and
never resolved**. The honest response is to surface it, not to silently repeat
it — the effect may or may not have landed (see `PHASE_P1_REPORT.md` §4).

Scope: this is a pure function of (tool, arguments). It deliberately does not
fold in a session or turn id — callers that need cross-session isolation
should prefix their own scope, because a key that silently means different
things in different contexts is worse than no key at all.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

KEY_LENGTH = 32


def _canonical(obj: Any) -> str:
    """Deterministic JSON for hashing.

    `sort_keys=True` makes key order irrelevant, so `{"a":1,"b":2}` and
    `{"b":2,"a":1}` hash alike — they are the same invocation. `default=repr`
    keeps unhashable values (sets, custom objects) deterministic instead of
    raising, because an unhashable argument must not break a tool call.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=repr, ensure_ascii=False)


def action_key(tool: str, args: Any) -> str:
    """Stable digest identifying one tool invocation.

    Args that arrive as a JSON *string* (the provider wire shape) and the
    equivalent dict hash identically — the same call must not acquire two
    identities depending on how it was transported.
    """
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (ValueError, TypeError):
            pass  # not JSON — hash the raw string
    return hashlib.sha256(
        _canonical({"tool": str(tool), "args": args}).encode("utf-8")
    ).hexdigest()[:KEY_LENGTH]
