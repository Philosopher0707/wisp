"""The recovery ladder (migration P6).

Replaces ad-hoc recovery with an explicit, budgeted, evidence-bearing ladder.

Four things P6 asked for, and where each stands:

| Item | Before | After |
|---|---|---|
| A closed failure taxonomy | `NodeFailure.failure_code` was a **free string** (`graph/types.py`, default `"ERROR"`); six of the ten classes existed nowhere | `FailureClass` (10, closed) with `classify_failure()` delegating to the canonical outcome authority |
| The 7-rung ladder | recovery was a set of independent mechanisms with no ordering | `LEGAL_RUNGS` per class, `FORBIDDEN_RUNGS` for the structural rules, `RecoveryLadder` as the state machine |
| A durable rollback path | `runs/compensation.py` says *"No tool wiring"* and had **zero** production callers; `tools/checkpoints.py` is in-memory and session-scoped | the Rollback rung consults `reversibility()` / `rollback_preview()`, and records survive via the session journal |
| Recovery budgets from one place | the audit found five unordered termination modes and no object that answered "how much is left" | `BudgetGovernor` |

**The rule that matters most.** Denials must never auto-retry. `graph/subagent_orchestrator.py`
comments it, and Phase 10 **removed** `_DENIAL_MARKERS` precisely because it failed to enforce it —
all five canonical denial statuses matched nothing (`CONTEXT.md:456`). The taxonomy makes the rule
enforceable **by class** rather than by prose matching, which is the difference between a rule and a
comment.

**Terminal honesty (R3).** An exhausted ladder yields `ESCALATED_TO_HUMAN` — a terminal state, not a
hang and not a false success. `RecoveryLadder.exhausted` is a property the caller can act on.

**Progress as a second input (progress-aware recovery mission).** A failure class answers *what
failed*; it cannot answer *whether the attempt moved the objective*, and for one class the two
questions have opposite answers. `ENVIRONMENT` covers both "the model could not get started" and
"the turn was cut off mid-implementation", and its legal set — `{DIAGNOSTIC, HUMAN}` — is right for
the first and useless for the second. `decide(..., progress=…)` therefore lets a *meaningful
progress* observation widen the class's legal set with the continuation rungs
(`PROGRESS_CONTINUATION_RUNGS`). It is additive and defaults to `None`, so every existing caller and
every existing invariant is unchanged: `SECURITY` still cannot retry, `REPEATED`/`STAGNATION` still
cannot repeat, and R5 still forbids a rung twice.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any, ClassVar, Iterable

from wisp.core.events import (
    CODE_ITERATION_BUDGET,
    CODE_PROVIDER_STREAM,
    CODE_TOOL_TIMEOUT,
    CODE_TURN_TIMEOUT,
    DENIAL_APPROVAL_TIMEOUT,
    DENIAL_CANCELLED,
    DENIAL_POLICY_DENIED,
    DENIAL_SCHEMA_INVALID,
    DENIAL_USER_DENIED,
    OutcomeClass,
    classify_result,
    is_denial_text,
)
from wisp.core.progress import ProgressVerdict


class FailureClass(StrEnum):
    """The closed failure taxonomy. Exactly ten classes.

    Closed on purpose: `NodeFailure.failure_code` was a free string, so a
    producer could invent a class and nothing would notice — which is how
    "recovery" becomes a pile of special cases. `classify_failure()` maps every
    detection onto one of these, and a test pins the count.
    """

    TRANSIENT = "transient"                    # provider/network blip
    TOOL = "tool"                              # the tool returned an error
    IMPLEMENTATION = "implementation"          # verification says the work is wrong
    VERIFICATION = "verification"              # the verifier could not decide
    DEPENDENCY = "dependency"                  # a predecessor failed or is blocked
    ENVIRONMENT = "environment"                # sandbox / toolchain / host
    INVALID_ASSUMPTION = "invalid_assumption"  # the plan contradicts observed reality
    REPEATED = "repeated"                      # the same failure, N times
    STAGNATION = "stagnation"                  # working, not progressing
    SECURITY = "security"                      # policy / authorization denial


class RecoveryRung(IntEnum):
    """The ladder, in cost order. Lower is cheaper and narrower in blast radius.

    `IntEnum` so "move down the ladder" is a comparison rather than a list
    index — `R1 — escalate, do not loop` depends on the ordering being real.
    """

    RETRY = 1
    REPAIR = 2
    ROLLBACK = 3
    LOCAL_REPLAN = 4
    GLOBAL_REPLAN = 5
    DIAGNOSTIC = 6
    HUMAN = 7


#: Which rungs a failure class may use (WISP_RECOVERY_ARCHITECTURE.md §4).
#: `HUMAN` is legal for every class: escalation is always available, and a
#: taxonomy that could forbid it would be able to trap a failure.
LEGAL_RUNGS: dict[FailureClass, frozenset[RecoveryRung]] = {
    FailureClass.TRANSIENT: frozenset({RecoveryRung.RETRY, RecoveryRung.HUMAN}),
    FailureClass.TOOL: frozenset({
        RecoveryRung.RETRY, RecoveryRung.REPAIR, RecoveryRung.ROLLBACK,
        RecoveryRung.LOCAL_REPLAN, RecoveryRung.HUMAN}),
    FailureClass.IMPLEMENTATION: frozenset({
        RecoveryRung.REPAIR, RecoveryRung.ROLLBACK,
        RecoveryRung.LOCAL_REPLAN, RecoveryRung.GLOBAL_REPLAN,
        RecoveryRung.HUMAN}),
    FailureClass.VERIFICATION: frozenset({
        RecoveryRung.REPAIR, RecoveryRung.LOCAL_REPLAN,
        RecoveryRung.DIAGNOSTIC, RecoveryRung.HUMAN}),
    FailureClass.DEPENDENCY: frozenset({
        RecoveryRung.GLOBAL_REPLAN, RecoveryRung.HUMAN}),
    FailureClass.ENVIRONMENT: frozenset({
        RecoveryRung.DIAGNOSTIC, RecoveryRung.HUMAN}),
    FailureClass.INVALID_ASSUMPTION: frozenset({
        RecoveryRung.LOCAL_REPLAN, RecoveryRung.GLOBAL_REPLAN,
        RecoveryRung.HUMAN}),
    FailureClass.REPEATED: frozenset({
        RecoveryRung.DIAGNOSTIC, RecoveryRung.HUMAN}),
    FailureClass.STAGNATION: frozenset({
        RecoveryRung.GLOBAL_REPLAN, RecoveryRung.DIAGNOSTIC,
        RecoveryRung.HUMAN}),
    FailureClass.SECURITY: frozenset({RecoveryRung.HUMAN}),
}

#: Structurally FORBIDDEN rungs — not merely discouraged (§4's `❌`).
#: Retrying a `SECURITY` failure is how an agent hammers a denial; retrying a
#: `REPEATED` failure is the infinite loop the class exists to name. Both are
#: forbidden as *classes*, so the rule cannot be defeated by wording.
#:
#: TOTAL by design. The seven classes with no structural prohibition carry an
#: **empty** frozenset rather than being absent, so "is this class missing, or
#: does it forbid nothing?" is never a question a reader has to answer — and a
#: future phase adding a prohibition has an obvious place to put it.
FORBIDDEN_RUNGS: dict[FailureClass, frozenset[RecoveryRung]] = {
    FailureClass.TRANSIENT: frozenset(),
    FailureClass.TOOL: frozenset(),
    FailureClass.IMPLEMENTATION: frozenset(),
    FailureClass.VERIFICATION: frozenset(),
    FailureClass.DEPENDENCY: frozenset(),
    FailureClass.ENVIRONMENT: frozenset(),
    FailureClass.INVALID_ASSUMPTION: frozenset(),
    FailureClass.REPEATED: frozenset({RecoveryRung.RETRY, RecoveryRung.REPAIR}),
    FailureClass.STAGNATION: frozenset({RecoveryRung.RETRY, RecoveryRung.REPAIR}),
    FailureClass.SECURITY: frozenset({
        RecoveryRung.RETRY, RecoveryRung.REPAIR, RecoveryRung.ROLLBACK,
        RecoveryRung.LOCAL_REPLAN, RecoveryRung.GLOBAL_REPLAN,
        RecoveryRung.DIAGNOSTIC}),
}

#: The canonical denial statuses. Imported from `core/events.py` rather than
#: re-listed: a local copy is exactly the defect Phase 10 removed
#: (`_DENIAL_MARKERS` matched none of these).
DENIAL_STATUSES = frozenset({
    DENIAL_POLICY_DENIED, DENIAL_USER_DENIED, DENIAL_APPROVAL_TIMEOUT,
    DENIAL_CANCELLED, DENIAL_SCHEMA_INVALID,
})


#: Rungs that a **meaningful-progress** observation may ADD to a class's legal
#: set — the continuation rungs.
#:
#: This exists because the failure class alone cannot express the distinction
#: the live experiment exposed. `CODE_TURN_TIMEOUT` is an `ENVIRONMENT` failure
#: whether the turn was cut off mid-implementation or whether the model simply
#: could not get started, and `ENVIRONMENT`'s legal set is `{DIAGNOSTIC, HUMAN}`
#: *by design* — "the model is too slow or unreachable — not retrying". That
#: design is right for the second case and wrong for the first, and no amount of
#: re-classification can tell them apart, because they are the same failure.
#: The difference is not *what failed*; it is *whether the attempt moved the
#: objective*, which is a separate fact with a separate owner
#: (`core/progress.py`).
#:
#: So progress **widens** rather than re-classifies. `ENVIRONMENT` gains
#: `REPAIR` — "repair that specific failure rather than re-doing the whole
#: task", which is exactly what continuing a partially-completed objective is —
#: and gains nothing else, because a second progressing timeout means the
#: objective genuinely exceeds the attempt budget and escalation is the honest
#: answer, not a third strategy.
#:
#: TOTAL by design, like the two tables above, and **deliberately empty for six
#: classes**. `SECURITY` must never widen (a denial is a denial, and the
#: no-retry rule is not negotiable); `REPEATED` and `STAGNATION` must not
#: either, because both are *defined* as the absence of progress and widening
#: them would let this table contradict the classes that name it. The classes
#: that already admit `REPAIR` need no entry: they are legal without it.
PROGRESS_CONTINUATION_RUNGS: dict[FailureClass, frozenset[RecoveryRung]] = {
    FailureClass.TRANSIENT: frozenset(),
    FailureClass.TOOL: frozenset(),
    FailureClass.IMPLEMENTATION: frozenset(),
    FailureClass.VERIFICATION: frozenset(),
    FailureClass.DEPENDENCY: frozenset(),
    FailureClass.ENVIRONMENT: frozenset({RecoveryRung.REPAIR}),
    FailureClass.INVALID_ASSUMPTION: frozenset(),
    FailureClass.REPEATED: frozenset(),
    FailureClass.STAGNATION: frozenset(),
    FailureClass.SECURITY: frozenset(),
}


#: Transport markers that mean "try again shortly" — throttling and dropped
#: connections. The canonical set (migration M12); `SubagentOrchestrator`
#: aliases this rather than keeping its own list, so the retry loop and the
#: taxonomy cannot disagree about what is transient.
TRANSIENT_MARKERS: tuple[str, ...] = (
    "429", "rate limit", "too many requests", "connection reset",
)

#: Provider refusals that will fail every request the same way, whichever agent makes it: an account or key out of
#: credit (402), a rejected key (401). Unlike `TRANSIENT_MARKERS` there is nothing to wait for, so a fan-out that has
#: seen one should not start the agents still queued behind it.
SYSTEMIC_MARKERS: tuple[str, ...] = (
    "api error 402", "refused this request on billing", "api error 401",
)

#: The failure class for each engine error code (`core/events.py`). **Total by
#: test**: `test_every_error_code_has_a_classification` enumerates the codes and
#: fails on one missing here, so a new code cannot silently take the default.
#:
#: `CODE_TURN_TIMEOUT` and `CODE_PROVIDER_STREAM` are `ENVIRONMENT`, not
#: `TRANSIENT`. Retrying a turn that timed out because the model is too slow is
#: the orchestrator's own documented refusal ("the model is too slow or
#: unreachable — not retrying"), and `ENVIRONMENT` routes to `DIAGNOSTIC` for
#: exactly that reason. `CODE_ITERATION_BUDGET` is the agent's own loop, so it is
#: `IMPLEMENTATION` and routes to `REPAIR`/replan.
CODE_FAILURE_CLASS: dict[str, FailureClass] = {
    CODE_TURN_TIMEOUT: FailureClass.ENVIRONMENT,
    CODE_PROVIDER_STREAM: FailureClass.ENVIRONMENT,
    CODE_TOOL_TIMEOUT: FailureClass.ENVIRONMENT,
    CODE_ITERATION_BUDGET: FailureClass.IMPLEMENTATION,
}


def is_transient_text(text: str | None) -> bool:
    """True when free text names a retryable transport condition."""
    if not text:
        return False
    lowered = text.lower()
    return any(m in lowered for m in TRANSIENT_MARKERS)


def is_systemic_text(text: str | None) -> bool:
    """True when ``text`` is a provider refusal that every agent in a fan-out will hit (billing, bad key)."""
    if not text:
        return False
    lowered = text.lower()
    return any(m in lowered for m in SYSTEMIC_MARKERS)


def describe_failure(error: object, limit: int = 300) -> str:
    """A failure in words a person can act on, on one line. Raw provider JSON is cut mid-sentence, hiding the remedy.

    Billing and key refusals (the systemic ones: see ``SYSTEMIC_MARKERS``) say what happened and what to do; anything
    else is kept in its own words, whitespace collapsed and bounded to ``limit`` characters. Shared by every surface that
    reports an agent failure (the REPL swarm report, the ``fanout`` tool), so they cannot describe the same refusal
    differently.
    """
    text = " ".join(str(error or "no error reported").split())
    low = text.lower()
    if "api error 402" in low or "on billing" in low:
        return ("the provider refused the request on billing (HTTP 402): the account is out of credit or the key's "
                "limit is too low. Add credit or raise the key's limit, then re-run.")
    if "api error 401" in low:
        return "the provider rejected the API key (HTTP 401): check WISP_API_KEY or run /provider."
    return text[:limit]


def classify_failure_signal(message: str | None = None, recoverable: bool = False,
                            code: str | None = None) -> FailureClass:
    """Map the **runtime's** failure signal onto the closed taxonomy (M12).

    The runtime observes failures as an `error` event carrying
    `(message, recoverable, code)`. The taxonomy accepts a `result` (which goes
    through `classify_result`) or semantic flags — neither of which is that — so
    until this adapter existed the ladder could not be driven from a real
    failure at all.

    Precedence is deliberate and mirrors `classify_failure`:

    1. **A refusal outranks everything** — `is_denial_text(message)`, the ONE
       authority for "is this text a denial". P6's rule: a denied call that also
       looked transient is still `SECURITY`, or the no-retry rule leaks.
    2. **A cancellation is an authorization outcome** — same recovery answer,
       `SECURITY`.
    3. **An engine error code** — the closed vocabulary, most specific.
    4. **Transport markers** — throttling and dropped connections.
    5. **`recoverable`** — the engine's own signal that the failure was not
       fatal, which is the bounded-retry case.
    6. Otherwise `IMPLEMENTATION` — the agent's own failure, the conservative
       default. **Not** `SECURITY`: an unrecognised failure must not silently
       acquire the strongest prohibition.

    This is the bridge, not a second classifier: it decides *which* taxonomy
    entry applies, never re-derives what an outcome means.
    """
    if is_denial_text(message):
        return FailureClass.SECURITY
    if message and "cancell" in message.lower():
        return FailureClass.SECURITY
    if code is not None and code in CODE_FAILURE_CLASS:
        return CODE_FAILURE_CLASS[code]
    if is_transient_text(message):
        return FailureClass.TRANSIENT
    if recoverable:
        return FailureClass.TRANSIENT
    return FailureClass.IMPLEMENTATION


def classify_failure(result: Any = None, *, repeated: bool = False,
                     stagnation: bool = False,
                     dependency_blocked: bool = False,
                     environment: bool = False,
                     invalid_assumption: bool = False,
                     verification_inconclusive: bool = False,
                     implementation_failed: bool = False,
                     transient: bool = False) -> FailureClass:
    """Map an observation onto the closed taxonomy.

    Classification of the *outcome* delegates to `classify_result()`
    (`core/events.py`) — the ONE authority for "what kind of outcome is this?".
    A second classifier is what let benchmark error accounting miss every
    structured denial, so this module reuses rather than re-derives.

    The explicit flags cover the classes with no outcome-shaped detection:
    repetition, stagnation, dependency blocking, environment faults, invalid
    assumptions, and an inconclusive verifier. Precedence is deliberate —
    **denial outranks everything**: a denied call that also looked transient
    must still be a `SECURITY` failure, or the no-retry rule leaks.
    """
    if result is not None:
        cls = classify_result(result)
        if cls is OutcomeClass.POLICY_DENIAL:
            return FailureClass.SECURITY
        if cls is OutcomeClass.DENIAL:
            # A user declining is also an authorization outcome; the class is
            # SECURITY because the recovery answer is the same — do not retry.
            return FailureClass.SECURITY
        if cls in (OutcomeClass.TIMEOUT, OutcomeClass.CANCELLATION):
            return FailureClass.SECURITY
        if cls is OutcomeClass.INVALID:
            return FailureClass.SECURITY

    # Explicit signals, most specific first.
    if repeated:
        return FailureClass.REPEATED
    if stagnation:
        return FailureClass.STAGNATION
    if dependency_blocked:
        return FailureClass.DEPENDENCY
    if environment:
        return FailureClass.ENVIRONMENT
    if invalid_assumption:
        return FailureClass.INVALID_ASSUMPTION
    if verification_inconclusive:
        return FailureClass.VERIFICATION
    if implementation_failed:
        return FailureClass.IMPLEMENTATION
    if transient:
        return FailureClass.TRANSIENT

    if result is not None:
        cls = classify_result(result)
        if cls is OutcomeClass.ERROR:
            return FailureClass.TOOL
        if cls is OutcomeClass.SUCCESS:
            # A caller handed us a success and asked what FAILED. Raising beats
            # guessing: silently returning a class would let a broken detection
            # site masquerade as a classified failure, which is the shape of
            # defect this taxonomy exists to remove. Pass
            # `implementation_failed=True` when the failure is the VERIFIER's
            # verdict rather than the tool's outcome.
            raise ValueError(
                "classify_failure() was given a SUCCESS outcome — there is no "
                "failure to classify (pass implementation_failed=True for a "
                "verification failure)")
    return FailureClass.IMPLEMENTATION


def _is_meaningful_progress(progress: ProgressVerdict | str | None) -> bool:
    """True only for `MEANINGFUL_PROGRESS`. Fail closed on anything else.

    The single place that decides whether a progress observation may relax R5.
    An unknown string, `None`, `NO_PROGRESS` and `PROGRESS_UNDETERMINABLE` all
    answer `False` — "cannot tell" must never widen a recovery rule.
    """
    if progress is None:
        return False
    try:
        return ProgressVerdict(progress) is ProgressVerdict.MEANINGFUL_PROGRESS
    except ValueError:
        return False


def _continuation_rungs(failure_class: FailureClass,
                        progress: ProgressVerdict | str | None
                        ) -> frozenset[RecoveryRung]:
    """The rungs a progress observation adds, or nothing at all.

    **Only `MEANINGFUL_PROGRESS` widens.** `NO_PROGRESS` leaves the class's set
    exactly as it was (the conservative recovery the previous mission proved
    terminates honestly), and `PROGRESS_UNDETERMINABLE` does the same: an
    unmeasurable attempt is not evidence of progress, and treating it as such
    would make every unmeasurable objective continue forever.
    """
    if not _is_meaningful_progress(progress):
        return frozenset()
    return PROGRESS_CONTINUATION_RUNGS.get(failure_class, frozenset())


def is_legal_rung(failure_class: FailureClass | str,
                  rung: RecoveryRung | int,
                  *,
                  progress: ProgressVerdict | str | None = None) -> bool:
    """True when `rung` is both allowed and not forbidden for the class.

    Forbidden wins over legal: the two tables are independent, and a rung that
    appears in both is a defect the caller should see rather than one the
    lookup order should hide. That ordering is what keeps the progress
    widening honest — a class that *forbids* a rung (SECURITY, REPEATED,
    STAGNATION) cannot have it reintroduced by a progress observation, because
    the forbidden check runs first and the continuation table is empty for all
    three.
    """
    fc = FailureClass(failure_class)
    r = RecoveryRung(int(rung))
    if r in FORBIDDEN_RUNGS.get(fc, frozenset()):
        return False
    if r in LEGAL_RUNGS[fc]:
        return True
    return r in _continuation_rungs(fc, progress)


# ── Budgets ─────────────────────────────────────────────────────────────


@dataclass
class RecoveryBudget:
    """The four budgets P6 introduces, plus the two it reads.

    Defaults are deliberately small. Recovery is the most expensive thing the
    agent does — it re-plans, re-verifies and re-executes — so an unbounded
    recovery budget is an unbounded cost multiplier.

    **`productive_continuations` (ADR-0047, F61)** is the fifth, and it exists
    because R5's original unit was wrong. R5 forbade *a rung* from repeating,
    which was the right rule while the only thing a rung could carry was a
    failure. Once progress semantics exist, a rung can carry a *success that has
    not finished* — and then the thing that must not repeat is **the same
    strategy against materially unchanged state**, not the rung. Re-choosing a
    rung is permitted only when the attempt measurably advanced the objective,
    and only this many times; the bound is what keeps "productive" from meaning
    "forever".
    """

    local_replans: int = 2
    global_replans: int = 1
    diagnostic_tasks: int = 2
    graph_growth_nodes: int = 64
    #: How many times a rung that already ran may be chosen again, across the
    #: whole objective. Counted separately from the rung's own budget so the
    #: two bounds cannot be confused: `local_replans` bounds *how much replanning*
    #: happens, this bounds *how much continuing* happens.
    productive_continuations: int = 2


#: The budget a re-used rung is charged to. A module constant so the name
#: appears once — a second spelling is how a budget stops being a bound.
PRODUCTIVE_BUDGET = "productive_continuations"


@dataclass
class BudgetGovernor:
    """One place that answers "how much is left?".

    The audit found **five unordered termination modes** and no single object
    that could answer that question. This is that object for the recovery
    budgets; the pre-existing budgets (stream attempts, repair nudges, the
    grind floor, cycle iterations) keep their own owners and are reported here
    read-only via `reported`.
    """

    budget: RecoveryBudget = field(default_factory=RecoveryBudget)
    spent: dict[str, int] = field(default_factory=dict)
    reported: dict[str, Any] = field(default_factory=dict)

    def remaining(self, name: str) -> int:
        total = getattr(self.budget, name, 0)
        return max(0, int(total) - int(self.spent.get(name, 0)))

    def exhausted(self, name: str) -> bool:
        return self.remaining(name) <= 0

    def spend(self, name: str, amount: int = 1) -> bool:
        """Charge `name`. Returns False when the budget is already exhausted.

        Returns rather than raises: exhaustion is an ordinary outcome that the
        ladder routes to escalation, not an exceptional condition. The caller
        decides; this only reports.
        """
        if amount <= 0:
            raise ValueError("spend() requires a positive amount")
        if self.exhausted(name):
            return False
        self.spent[name] = int(self.spent.get(name, 0)) + amount
        return True

    def snapshot(self) -> dict[str, Any]:
        """Everything readable from one place, including what others own."""
        out: dict[str, Any] = {
            name: {"remaining": self.remaining(name),
                   "spent": int(self.spent.get(name, 0)),
                   "total": getattr(self.budget, name)}
            for name in ("local_replans", "global_replans",
                         "diagnostic_tasks", "graph_growth_nodes",
                         PRODUCTIVE_BUDGET)
        }
        out["reported"] = dict(self.reported)
        return out


# ── Decisions and escalation ────────────────────────────────────────────


@dataclass(frozen=True)
class RecoveryDecision:
    """One rung, chosen and justified. A proposal in the P2 sense.

    `evidence` is required to be non-empty by `RecoveryLadder.decide`: a rung
    that cites nothing is an assertion, and the plan requires every rung to
    bear evidence.
    """

    failure_class: FailureClass
    rung: RecoveryRung
    reason: str = ""
    evidence: tuple[str, ...] = ()
    seq: int = 0
    decided_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"failure_class": self.failure_class.value, "rung": int(self.rung),
                "rung_name": self.rung.name, "reason": self.reason,
                "evidence": list(self.evidence), "seq": self.seq,
                "decided_at": self.decided_at}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RecoveryDecision":
        return cls(failure_class=FailureClass(d["failure_class"]),
                   rung=RecoveryRung(int(d["rung"])), reason=d.get("reason", ""),
                   evidence=tuple(d.get("evidence") or ()),
                   seq=int(d.get("seq", 0)),
                   decided_at=d.get("decided_at", 0.0))


class EscalationState(StrEnum):
    """Where an escalation is. `PENDING` is RESUMABLE, not a hang."""

    PENDING = "pending"      # waiting for a human; the run can be parked
    ANSWERED = "answered"
    ABANDONED = "abandoned"


@dataclass(frozen=True)
class HumanIntervention:
    """Escalation as a durable STATE, not a blocking call.

    A blocking call cannot survive a process restart, cannot be answered
    asynchronously, and has no channel for a non-CLI client. Making it state
    means the existing `WebSocketTransport` approval flow can carry it and the
    run can resume when an answer arrives.
    """

    intervention_id: str
    reason: str
    failure_class: FailureClass = FailureClass.IMPLEMENTATION
    ladder_history: tuple[RecoveryDecision, ...] = ()
    state: EscalationState = EscalationState.PENDING
    answer: str = ""
    created_at: float = field(default_factory=time.time)
    answered_at: float | None = None

    @property
    def resumable(self) -> bool:
        return self.state is EscalationState.PENDING

    def to_dict(self) -> dict[str, Any]:
        return {"intervention_id": self.intervention_id, "reason": self.reason,
                "failure_class": self.failure_class.value,
                "state": self.state.value, "answer": self.answer,
                "created_at": self.created_at, "answered_at": self.answered_at,
                "ladder_history": [d.to_dict() for d in self.ladder_history]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "HumanIntervention":
        return cls(
            intervention_id=d["intervention_id"], reason=d.get("reason", ""),
            failure_class=FailureClass(d.get("failure_class", "implementation")),
            ladder_history=tuple(RecoveryDecision.from_dict(x)
                                 for x in d.get("ladder_history") or ()),
            state=EscalationState(d.get("state", "pending")),
            answer=d.get("answer", ""), created_at=d.get("created_at", 0.0),
            answered_at=d.get("answered_at"),
        )


# ── The ladder ──────────────────────────────────────────────────────────


class LadderExhausted(RuntimeError):
    """Raised when a rung is requested and none is available.

    The caller must surface `ESCALATED_TO_HUMAN`; it must **not** swallow this
    and retry, which would be the loop R1 forbids.
    """


@dataclass
class RecoveryLadder:
    """The state machine over the seven rungs.

    One instance per goal. It records what has been tried so R5 — *a rung that
    would repeat an already-failed rung for the same failure is illegal* — is
    enforced structurally rather than remembered.
    """

    governor: BudgetGovernor = field(default_factory=BudgetGovernor)
    history: list[RecoveryDecision] = field(default_factory=list)
    escalated: bool = False
    escalation: HumanIntervention | None = None

    #: Which budget each rung charges. A ClassVar, not a field: it is
    #: configuration, and a per-instance copy would invite one instance to
    #: charge a different budget than another for the same rung.
    _BUDGET_FOR: ClassVar[dict[RecoveryRung, str]] = {
        RecoveryRung.LOCAL_REPLAN: "local_replans",
        RecoveryRung.GLOBAL_REPLAN: "global_replans",
        RecoveryRung.DIAGNOSTIC: "diagnostic_tasks",
    }

    def tried(self, rung: RecoveryRung) -> bool:
        return any(d.rung is rung for d in self.history)

    def legal_rungs(self, failure_class: FailureClass, *,
                    progress: ProgressVerdict | str | None = None,
                    exclude: Iterable[RecoveryRung] = ()
                    ) -> list[RecoveryRung]:
        """Legal, not-forbidden, in budget, and not already tried.

        `progress` widens the class's set with the continuation rungs when the
        previous attempt measurably moved the objective (see
        `PROGRESS_CONTINUATION_RUNGS`), **and it also relaxes R5 for that one
        case**. It is keyword-only and defaults to `None`, so every existing
        caller keeps the exact behaviour it had.

        **R5, refined (ADR-0047).** The rule was *"a rung that would repeat an
        already-failed rung for the same failure is illegal"*. That unit — the
        rung — was right while a rung could only ever carry a failure. A rung
        that produced `MEANINGFUL_PROGRESS` carries something else: a *success
        that has not finished*. What must not repeat is **the same strategy
        against materially unchanged state**, and `MEANINGFUL_PROGRESS` is
        exactly the host-owned witness that the state changed — it is computed
        from the objective's own measurement, so a repeat is permitted only when
        the measurement moved, never on the model's say-so.

        So the rule becomes: *a rung may be re-chosen only when the previous
        attempt measurably advanced the objective, and only while the
        `productive_continuations` budget has room*. No progress → the original
        R5, unchanged. No budget → no re-choice, and the ladder escalates.

        `exclude` is for `_next_rung`'s "chosen but not executable here" retry:
        without it, a rung that was re-legalised by progress could be re-chosen
        by the retry and loop until the retry budget ran out.
        """
        productive = _is_meaningful_progress(progress)
        excluded = set(exclude)
        out: list[RecoveryRung] = []
        for rung in RecoveryRung:
            if rung in excluded:
                continue
            if not is_legal_rung(failure_class, rung, progress=progress):
                continue
            if rung is RecoveryRung.HUMAN:
                continue          # escalation is the fallback, never a choice
            if self.tried(rung):
                if not productive:
                    continue      # R5, unchanged: no progress, no repeat
                if self.governor.exhausted(PRODUCTIVE_BUDGET):
                    continue      # the bound that makes "productive" finite
            budget_name = self._BUDGET_FOR.get(rung)
            if budget_name and self.governor.exhausted(budget_name):
                continue
            out.append(rung)
        return sorted(out)

    def decide(self, failure_class: FailureClass,
               evidence: Iterable[str],
               reason: str = "",
               *,
               tool_name: str = "",
               records: Iterable[Any] = (),
               progress: ProgressVerdict | str | None = None,
               exclude: Iterable[RecoveryRung] = ()) -> RecoveryDecision:
        """Choose the next rung, or escalate.

        Evidence is REQUIRED. A rung that cites nothing cannot be reviewed, and
        the plan's `test_recovery_requires_evidence` makes that a hard rule
        rather than a convention.

        **This is where the compensation declarations get their caller.**
        `runs/compensation.py` says *"No tool wiring"* and nothing called
        `reversibility()` or `rollback_preview()` until P6. When the chosen rung
        is ROLLBACK and a `tool_name` is supplied, the declarations are
        consulted first: an `irreversible` tool (a published `git push`) must not
        be "rolled back", and an undeclared one must not be assumed
        compensable. An unsafe rollback **escalates** rather than proceeding —
        a recovery that makes things worse is the worst outcome available.
        """
        evidence = tuple(evidence)
        if not evidence:
            raise ValueError(
                "a recovery rung must cite evidence — an unjustified rung is "
                "an assertion, not a recovery")
        candidates = self.legal_rungs(failure_class, progress=progress,
                                      exclude=exclude)
        if not candidates:
            return self.escalate(
                failure_class,
                reason=reason or f"no legal rung remains for {failure_class.value}",
                evidence=evidence)
        rung = candidates[0]           # cheapest first: the ladder is cost-ordered

        if rung is RecoveryRung.ROLLBACK and tool_name:
            plan = plan_rollback(tool_name, records)
            if not plan.safe:
                return self.escalate(
                    failure_class,
                    reason=f"rollback unsafe: {plan.reason}",
                    evidence=evidence + (f"reversibility({tool_name})="
                                         f"{plan.reversibility}",))

        # Charge BOTH bounds when a rung is re-chosen: its own budget (which may
        # already be exhausted — in which case `legal_rungs` never offered it)
        # and the productive-continuation budget, which is what bounds the
        # repeat. Charging only the former would make a rung with no budget
        # entry — `REPAIR` — repeat without limit.
        budget_name = self._BUDGET_FOR.get(rung)
        if budget_name:
            self.governor.spend(budget_name)
        if self.tried(rung):
            self.governor.spend(PRODUCTIVE_BUDGET)
        decision = RecoveryDecision(failure_class=failure_class, rung=rung,
                                    reason=reason, evidence=evidence,
                                    seq=len(self.history) + 1)
        self.history.append(decision)
        return decision

    def escalate(self, failure_class: FailureClass, *, reason: str,
                 evidence: Iterable[str] = ()) -> RecoveryDecision:
        """Terminal honesty: exhaustion produces a STATE, not a hang.

        `HumanIntervention` carries the **full ladder history**, so the operator
        receives the audit trail rather than a bare question — the difference
        between "I am stuck" and "here is everything I tried".
        """
        decision = RecoveryDecision(
            failure_class=failure_class, rung=RecoveryRung.HUMAN,
            reason=reason, evidence=tuple(evidence), seq=len(self.history) + 1)
        self.history.append(decision)
        self.escalated = True
        self.escalation = HumanIntervention(
            intervention_id=f"esc-{len(self.history)}",
            reason=reason, failure_class=failure_class,
            ladder_history=tuple(self.history))
        return decision

    @property
    def ladder_state(self) -> str:
        """`ESCALATED_TO_HUMAN` once escalated — never a silent success.

        Named `ladder_state`, not `terminal_outcome` (ADR-0044 R6). This is the
        RECOVERY mechanism's own state, not the turn's terminal evidence and not
        a `GoalState`; sharing the name invited exactly the cross-layer collapse
        ADR-0042 prohibits. Its values are deliberately upper-case member names,
        distinct from `GoalState`'s lower-case values.
        """
        return "ESCALATED_TO_HUMAN" if self.escalated else "IN_PROGRESS"


# ── Durable rollback (the wiring the plan asked for) ────────────────────


@dataclass(frozen=True)
class RollbackPlan:
    """What the Rollback rung would do, and whether it is safe to attempt."""

    tool_name: str
    reversibility: str                 # reversible | irreversible | unknown
    previews: tuple[str, ...] = ()
    safe: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"tool_name": self.tool_name, "reversibility": self.reversibility,
                "previews": list(self.previews), "safe": self.safe,
                "reason": self.reason}


def plan_rollback(tool_name: str,
                  records: Iterable[Any] = ()) -> RollbackPlan:
    """Consult the compensation declarations — the wiring they never had.

    `runs/compensation.py`'s docstring says *"No tool wiring"*, and until P6
    nothing called `reversibility()` or `rollback_preview()`. This is that
    caller: the Rollback rung asks the declarations whether a rollback is safe
    before proposing one.

    Refusal is the default for anything not declared reversible. An
    `irreversible` tool (a published `git push`) must not be silently
    "rolled back", and an `unknown` tool must not be assumed compensable —
    that assumption is how a recovery makes things worse.
    """
    from wisp.runs.compensation import reversibility, rollback_preview

    verdict = reversibility(tool_name)
    records = list(records)
    if verdict != "reversible":
        return RollbackPlan(
            tool_name=tool_name, reversibility=verdict, safe=False,
            reason=(f"{tool_name} is {verdict}: automatic rollback is not "
                    "safe — escalate instead"))
    previews = tuple(rollback_preview(r) for r in records)
    return RollbackPlan(tool_name=tool_name, reversibility=verdict,
                        previews=previews, safe=True,
                        reason=f"{tool_name} is reversible ({len(previews)} record(s))")
