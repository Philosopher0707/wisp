"""Goal-state derivation (ADR-0035). Pure, deterministic, replayable.

ADR-0035 splits one question into two authorities:

| Track | Question | Owner |
|---|---|---|
| completion | *"is the work complete?"* | this module (+ the verification floor guard) |
| recovery | *"what should happen next?"* | `core/recovery.py::RecoveryLadder` |

This module implements **only the completion side**, as a pure function of
already-established facts. It is the arbiter, not a second opinion:

- the **acceptance verdict** comes from P3 (`core/acceptance.py`),
- the **stagnation predicate** comes from M13
  (`StagnationDetector.may_report_goal_met`),
- the **failure class** comes from M12 (`core/recovery.py::classify_failure_signal`).

It therefore inspects no files, reads no git state, calls no model, touches no
store, and mutates nothing. Every input is a fact some other authority already
established, which is what makes the result replayable: the same durable inputs
yield the same state, live or reconstructed.

**The precedence is an explicit state contract, not a ranking.** ADR-0035 fixes
it as an ordered table; there is deliberately no severity score, no timestamp
comparison, no enum ordering, and no model judgment. Do not add one.

**ADR-0047 (F60) revised rows 3–6** so that the objective's own evidence decides
where it is decisive, and the execution outcome decides only where it is not.
`turn_succeeded` stopped being an arbitration input: it is a fact about the
*attempt*, and a failed attempt is not a failed objective. Exactly one input
combination changed — *fatal terminal error + `PASS`*, `GOAL_FAILED` →
`GOAL_MET`. See `PRECEDENCE` for the principle and `derive_goal_state` for the
narrow statement of what moved.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any


class TerminalOutcome(StrEnum):
    """What the turn's terminal evidence said (13-H5).

    Derived from `saw_done` / `saw_fatal_error` only — the same two facts the
    turn-level rule uses, so the two levels can never disagree about what
    happened.
    """

    SUCCEEDED = "succeeded"      # a `done` was seen, and no fatal error was
    FAILED = "failed"            # a fatal error: `recoverable` was falsy
    INCOMPLETE = "incomplete"    # neither — bare exhaustion or partial output


class GoalState(StrEnum):
    """ADR-0035's six goal states. No more, no fewer.

    `BUDGET_EXHAUSTED` and `TIMED_OUT` are deliberately **not** states: they
    reach the runtime as fatal error codes and are carried as the `reason` of
    `GOAL_FAILED`. Promoting them would require the `BudgetGovernor` on the live
    path, which ADR-0035 does not decide.
    """

    GOAL_MET = "goal_met"
    GOAL_UNVERIFIED = "goal_unverified"
    GOAL_STAGNATED = "goal_stagnated"
    GOAL_FAILED = "goal_failed"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    CANCELLED = "cancelled"


#: Every goal state is a conclusion about a turn, so every one is terminal and
#: is frozen once recorded (ADR-0020; ADR-0035 precedence row 0).
TERMINAL_GOAL_STATES: frozenset[GoalState] = frozenset(GoalState)


#: ADR-0035's arbitration order, documented as data so a reader can compare the
#: contract against the code without reverse-engineering the branches.
#:
#: **Revised by ADR-0047 (F60).** Row 3's fatal-error clause became row 4 and
#: gained one qualifier; `turn_succeeded` stopped arbitrating. The principle,
#: stated once:
#:
#: > **A fatal terminal error fails the objective unless the objective is
#: > independently verified as satisfied.**
#:
#: A failed *turn* is not a failed *objective*. ADR-0035's row 3 read
#: *"P3 FAIL **or fatal terminal error**"*, which made a turn timeout outrank an
#: independent acceptance `PASS` — so a repository that satisfied every
#: objective criterion was reported `GOAL_FAILED`. That is a false **negative**
#: (the mirror of F37), and it cost a whole extra attempt in five live runs: the
#: objective was met, and the loop re-attempted solely to close a turn that the
#: objective did not need.
#:
#: **Why the qualifier is a conjunction and not a reordering.** Three rules have
#: to hold at once, and they are not totally orderable:
#:
#: * a fatal error must outrank stagnation, so a *heuristic* cannot soften a
#:   *fact* (`test_a_fatal_error_with_a_closed_predicate_stays_goal_failed`);
#: * stagnation must outrank `PASS`, so the completion gate can withhold
#:   goal-met (`test_t2_pass_and_stagnating_is_goal_stagnated`);
#: * `PASS` must outrank a fatal error (this ADR).
#:
#: Read as priorities that is `fatal > stagnation > PASS > fatal` — a cycle, so
#: no ordering of rows can express it. The cycle is broken where it is
#: *semantically* broken: the fatal clause is the only one whose meaning depends
#: on the verdict, because it is the only one that is a statement about the
#: *attempt* rather than about the *objective*.
PRECEDENCE: tuple[tuple[int, str, str], ...] = (
    (0, "already-recorded terminal state", "frozen — never rewritten (ADR-0020)"),
    (1, "operator cancellation", "CANCELLED"),
    (2, "ladder exhausted / human escalation", "ESCALATED_TO_HUMAN"),
    (3, "P3 FAIL", "GOAL_FAILED"),
    (4, "fatal terminal error, and no P3 PASS", "GOAL_FAILED"),
    (5, "may_report_goal_met() is False", "GOAL_STAGNATED"),
    (6, "P3 PASS", "GOAL_MET"),
    (7, "otherwise — no decisive verdict", "GOAL_UNVERIFIED"),
)


def _value_of(thing: Any) -> str:
    """The string value of an enum-or-string, without importing its module.

    Both `Verdict` and `TerminalOutcome` are `StrEnum`s, and callers may pass
    either the member or its raw string. Reading `.value` when present keeps
    this module free of an import cycle with `acceptance.py`.
    """
    if thing is None:
        return ""
    return str(getattr(thing, "value", thing) or "")


def terminal_outcome_from_evidence(*, saw_done: bool,
                                   saw_fatal_error: bool) -> TerminalOutcome:
    """Derive the terminal outcome from the two 13-H5 facts.

    Kept here, beside the arbiter, so the turn-level rule and the goal-level
    derivation cannot drift about what "the turn failed" means.
    """
    if saw_fatal_error:
        return TerminalOutcome.FAILED
    if saw_done:
        return TerminalOutcome.SUCCEEDED
    return TerminalOutcome.INCOMPLETE


def derive_goal_state(*,
                      terminal_outcome: TerminalOutcome | str,
                      acceptance_verdict: Any = None,
                      stagnating: bool = False,
                      turn_succeeded: bool = False,
                      cancelled: bool = False,
                      escalated: bool = False,
                      already_recorded: GoalState | str | None = None,
                      reason: str = "") -> GoalState:
    """ADR-0035's ordered arbitration, revised by ADR-0047. First match wins. Total.

    Total by construction: the final `return` is reachable, so no input
    combination can raise or fall through.

    **The revision (F60).** Two things changed, and they are one decision:

    - The fatal-terminal-error clause is now qualified by *"and no P3 PASS"*.
      An independent, host-produced measurement that every objective criterion
      holds is the definition of the objective being met; a timeout says the
      *attempt* was cut off, not that the repository failed.
    - `turn_succeeded` is therefore **no longer an arbitration input**. It
      remains a fact — recorded on the attempt, and the turn-level authority
      ADR-0044 established — but it is not a *precondition* for objective
      completion. It was never sufficient on its own (row 6 still needs a
      `PASS`), and the F60 finding is that it is not necessary either.

    What did **not** change: a `FAIL` verdict is still `GOAL_FAILED`; a
    fatal terminal error with no `PASS` is still `GOAL_FAILED`, and still
    outranks stagnation; stagnation still outranks `GOAL_MET`; an absent or
    `INCONCLUSIVE` verdict is still `GOAL_UNVERIFIED`; and a recorded state is
    still frozen. Exactly one input combination moved: *fatal error + `PASS`*,
    which was `GOAL_FAILED` and is now `GOAL_MET`.

    `already_recorded` implements row 0: a terminal state that is already
    durable is returned unchanged, so a later observation, a duplicate event, or
    a replay cannot rewrite history.

    `reason` is not part of the arbitration — it is carried through for the
    audit record (e.g. `budget` / `timeout` under `GOAL_FAILED`).
    """
    del reason  # arbitration input only; the caller records it
    # Carried for the audit record, not consulted. See the docstring: a failed
    # turn is not a failed objective, and `PASS` alone cannot be a success.
    del turn_succeeded
    if already_recorded is not None:
        return GoalState(_value_of(already_recorded))

    if cancelled:                                               # row 1
        return GoalState.CANCELLED
    if escalated:                                               # row 2
        return GoalState.ESCALATED_TO_HUMAN

    outcome = _value_of(terminal_outcome)
    acceptance = _value_of(acceptance_verdict)

    if acceptance == "fail":                                    # row 3
        return GoalState.GOAL_FAILED
    if outcome == TerminalOutcome.FAILED.value and acceptance != "pass":  # row 4
        return GoalState.GOAL_FAILED
    if stagnating:                                              # row 5
        return GoalState.GOAL_STAGNATED
    if acceptance == "pass":                                    # row 6
        return GoalState.GOAL_MET

    # Row 7 — no decisive verdict. Most often a successful turn with no
    # acceptance verdict at all. The honest answer is "not verified", never
    # "met".
    return GoalState.GOAL_UNVERIFIED


def is_frozen(state: GoalState | str | None) -> bool:
    """True when `state` is a terminal goal state that must not be rewritten."""
    if state is None:
        return False
    try:
        return GoalState(_value_of(state)) in TERMINAL_GOAL_STATES
    except ValueError:
        return False


def already_recorded_from(records: Any) -> GoalState | None:
    """The frozen goal state from a session's replayed records, or `None`.

    Reads the journal-derived record list (ADR-0029's projection), taking the
    **first** goal-state record: the first conclusion is the one that is frozen,
    so a duplicate or later event cannot displace it.

    Defensive by design: this is called on the live path, where an unreadable
    record must not break the turn. A caller that needs the *loud* behaviour
    (the replay contract) uses `goal_state_from_record` instead.
    """
    for record in (records or ()):
        state = (record or {}).get("goal_state")
        if state:
            try:
                return GoalState(_value_of(state))
            except ValueError:
                return None
    return None


def goal_state_from_record(record: Any) -> GoalState:
    """Read one recorded goal state, **failing loud** on absence or corruption.

    ADR-0035's replay contract: a reconstruction that cannot read the authority's
    own answer must say so, not fall back to a plausible one. "Missing" and
    "unreadable" are different facts, and collapsing either into a default is
    how a replay silently disagrees with the live run it is supposed to
    reproduce.
    """
    state = (record or {}).get("goal_state")
    if not state:
        raise ValueError(
            "no goal_state in the record — refusing to guess one")
    try:
        return GoalState(_value_of(state))
    except ValueError as exc:
        raise ValueError(f"unknown goal state {state!r}") from exc
