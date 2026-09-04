"""Explicit execution phases for the coding-agent state loop.

The loop is a fixed cycle with two legal exits — approval denial parks in
AWAIT_APPROVAL, anything else flows forward:

    INIT -> RETRIEVE_CONTEXT -> SPECULATIVE_PLAN -> AWAIT_APPROVAL
      -> EXECUTE_SANDBOX -> VERIFY_DIAGNOSTICS -> REDUCE -> TERMINATE
                                                    |           ^
                                                    +----<------+
                                              (next iteration)

Failure traps (owned by ``loop.py``) cut across phases:
  - diff-hash oscillation (1-cycle repeat or 2-cycle) -> RECOVER
  - recursion ceiling exhausted -> TERMINATE with a failure artifact
"""

from __future__ import annotations

from enum import Enum


class Phase(str, Enum):
    """One node of the execution graph. Wire-stable values."""

    INIT = "INIT"
    RETRIEVE_CONTEXT = "RETRIEVE_CONTEXT"
    SPECULATIVE_PLAN = "SPECULATIVE_PLAN"
    AWAIT_APPROVAL = "AWAIT_APPROVAL"
    EXECUTE_SANDBOX = "EXECUTE_SANDBOX"
    VERIFY_DIAGNOSTICS = "VERIFY_DIAGNOSTICS"
    REDUCE = "REDUCE"
    RECOVER = "RECOVER"
    TERMINATE = "TERMINATE"


_NEXT: dict[Phase, Phase] = {
    Phase.INIT: Phase.RETRIEVE_CONTEXT,
    Phase.RETRIEVE_CONTEXT: Phase.SPECULATIVE_PLAN,
    Phase.SPECULATIVE_PLAN: Phase.AWAIT_APPROVAL,
    Phase.AWAIT_APPROVAL: Phase.EXECUTE_SANDBOX,
    Phase.EXECUTE_SANDBOX: Phase.VERIFY_DIAGNOSTICS,
    Phase.VERIFY_DIAGNOSTICS: Phase.REDUCE,
    Phase.REDUCE: Phase.TERMINATE,
    Phase.RECOVER: Phase.RETRIEVE_CONTEXT,
    Phase.TERMINATE: Phase.TERMINATE,
}


def next_phase(current: Phase) -> Phase:
    """Deterministic forward edge of the cycle."""
    return _NEXT[current]


def is_terminal(phase: Phase) -> bool:
    return phase is Phase.TERMINATE
