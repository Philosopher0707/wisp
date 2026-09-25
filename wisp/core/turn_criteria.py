"""The turn path's acceptance-criteria set — the floor guard's criteria, plus the
objective's declared criteria when the turn's prompt carries a declaration.

**ADR-0053.** ADR-0051 R1 states the enablement precondition:

    The gate may be enabled only when the turn path's required-criteria set contains
    at least one required criterion not derivable from `VerificationFloorGuard`'s own
    state.

Measured (ADR-0051), it did not: the turn path's verdict was `floor_guard_verdict(guard)`
— a pure projection of the guard — and over the 192-state guard space `verdict == FAIL`
agreed with `guard.rejection()` **192/192** times. So the gate was redundant (FAIL-keyed)
or harmful (non-PASS-keyed) and ADR-0051 added no flag.

This module is the criteria source that satisfies R1. **It re-implements nothing:**

* the floor half is `verification.floor_guard_criteria` / `floor_guard_evidence`;
* the declared half is `convergence.parse_criteria_declaration` (ADR-0050 R2/R3) and
  `convergence.explain_acceptance(..., use_declaration=True)`;
* the evidence is `convergence.CommandProbe.measure` — the ONE probe, bounded by each
  spec's own `timeout_s`, and the same object the objective-level loop uses;
* the verdict is `acceptance.evaluate`, unchanged.

**The gate's condition is `verdict_keys_on_declared()`** — a `FAIL` whose deciding
criterion is not the floor's. It is a *different* condition from the floor guard's, and
the difference is drivable: a declared `symbol_defined` criterion fails when the named
file does not define the symbol, which can be true while `guard.rejection()` returns
`None` (nothing was mutated, so the floor guard is vacuously satisfied). See
`tests/reliability/test_criteria_source_on_turn_path.py`.

**What this does NOT do.** It does not *itself* withhold `done`: the verdict is computed after the
engine has emitted `done`, and the turn-level `done` gate (ADR-0036) is unchanged by ADR-0053. The
consumption point there is `goal.derive_goal_state`, which already reads the verdict — so a declared
failure lands `GOAL_FAILED` (row 3) where a floor-only verdict landed `GOAL_UNVERIFIED`.

**ADR-0054 adds the withholding**, and it does so where it can: `DeclaredCriteriaGate` is a read-only
callable the engine asks at its pre-`done` gate, behind `acceptance_gate` (default OFF). At that moment
the workspace is final, so a probe taken there is valid; the runtime-side verdict is unchanged and still
the one `derive_goal_state` reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from wisp.core.acceptance import (
    AcceptanceCriteria,
    CompletionVerdict,
    Evidence,
    Verdict,
    evaluate,
)
from wisp.core.verification import (
    FLOOR_CRITERION_ID,
    floor_guard_criteria,
    floor_guard_evidence,
)

#: The flag (ADR-0053 R7). Read ONCE, at `AgentRuntime.run_turn`'s entry, beside the
#: other per-concern flags (ADR-0002). Default **OFF**: with it off the turn path's
#: criteria set is exactly `floor_guard_criteria(guard)`, which is today's behaviour.
TURN_CRITERIA_SOURCE_ENV = "WISP_TURN_CRITERIA_SOURCE"


@dataclass(frozen=True)
class TurnCriteria:
    """The turn's criteria set, its evidence, and what the host observed."""

    criteria: tuple[AcceptanceCriteria, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    observations: dict[str, Any] = field(default_factory=dict)
    #: True when the prompt carried a declaration and its criteria are in the set.
    declared: bool = False
    #: The probe's human lines, for the record. Empty when nothing was declared.
    lines: tuple[str, ...] = ()

    @property
    def declared_ids(self) -> tuple[str, ...]:
        """The criteria ids the declaration contributed — never the floor's."""
        return tuple(c.criteria_id for c in self.criteria
                     if c.criteria_id != FLOOR_CRITERION_ID)


def floor_only(guard: Any) -> TurnCriteria:
    """Today's criteria set: the floor guard's one criterion, and its evidence."""
    return TurnCriteria(criteria=tuple(floor_guard_criteria(guard)),
                        evidence=tuple(floor_guard_evidence(guard)))


def turn_criteria(guard: Any, prompt: str, workspace: str, *,
                  enabled: bool,
                  measurement: Any = None) -> TurnCriteria:
    """The turn's required-criteria set, with the declaration's criteria unioned in.

    `enabled` is the flag's value, read once by the caller — never re-read here, so a
    flag cannot disagree with itself (ADR-0002).

    **A malformed declaration raises** (`CriteriaDeclarationRejected`), because ADR-0050
    R4 forbids falling back: measuring the floor alone while appearing to measure what
    the caller declared is exactly the silent downgrade R4 exists to prevent. A block
    that is *not at the head* is not a declaration at all and returns `None` from the
    parser — it never reaches this branch.
    """
    base = floor_only(guard)
    if not enabled:
        return base

    from wisp.core.convergence import (
        CommandProbe,
        explain_acceptance,
        parse_criteria_declaration,
    )

    declaration = parse_criteria_declaration(prompt, workspace)   # raises per R4
    if declaration is None or not declaration.specs:
        return base

    # The declared criteria come from the SAME derivation the objective-level loop uses,
    # so the turn path and the loop cannot disagree about what a declaration means.
    derivation = explain_acceptance(prompt, workspace, use_declaration=True)
    # Reuse a measurement the gate already took when there is one: the declared command
    # is the expensive part, and one turn should pay for it once (ADR-0054 R3).
    if measurement is None:
        measurement = CommandProbe(declaration.specs).measure(workspace)

    return TurnCriteria(
        criteria=base.criteria + tuple(derivation.criteria),
        evidence=base.evidence + tuple(measurement.evidence),
        observations=dict(measurement.observations),
        declared=True,
        lines=tuple(measurement.lines),
    )


def turn_acceptance_verdict(guard: Any, prompt: str, workspace: str, *,
                            enabled: bool,
                            measurement: Any = None) -> tuple[CompletionVerdict, TurnCriteria]:
    """The turn's acceptance verdict, and the criteria set it was computed over."""
    tc = turn_criteria(guard, prompt, workspace, enabled=enabled,
                       measurement=measurement)
    return evaluate(tc.criteria, tc.evidence, tc.observations), tc


def verdict_keys_on_declared(verdict: CompletionVerdict) -> bool:
    """True when the verdict was decided by a criterion the floor guard does not own.

    This is **the gate's condition** (ADR-0053 R4). `evaluate` returns `FAIL` with the
    deciding criterion in `unmet_criteria` (rule 2's deterministic short-circuit names
    it there too), so the question is whether *every* named criterion is the floor's.

    A verdict that is `FAIL` for a criterion the floor guard already enforces returns
    `False`: that case is the floor guard's own, and a gate keyed on it would duplicate
    `guard.rejection()` — the redundancy ADR-0051 measured.
    """
    if verdict.verdict is not Verdict.FAIL:
        return False
    named = tuple(verdict.unmet_criteria or ())
    return bool(named) and all(cid != FLOOR_CRITERION_ID for cid in named)


class DeclaredCriteriaGate:
    """The engine's read-only predicate for the declared criteria (**ADR-0054 R3**).

    ADR-0053 §10 recorded that `verdict_keys_on_declared` had **no production consumer**,
    and that ADR-0051's enablement decision is what would consume it. **This is that
    consumer**, and it is a **callable**: the engine asks it at its pre-`done` gate
    without ever receiving the criteria, the specs or the probe. The engine keeps its
    zero coupling to the criteria source — the same shape ADR-0036 established for the
    stagnation predicate.

    **Why the engine and not the runtime.** ADR-0053 §9's residual 2: the verdict is
    computed *after* the engine has emitted `done`, so a runtime-side gate could only
    record, never withhold. Withholding requires asking **before** `done` — and at that
    moment the workspace is final, so a probe taken there is valid.

    `last_measurement` caches the probe's result so the runtime's verdict site reuses it
    instead of running the declared command a second time: the command is the expensive
    part, and one turn pays for it once.
    """

    def __init__(self, criteria: Any, specs: Any, workspace: str) -> None:
        self._criteria = tuple(criteria)
        self._specs = tuple(specs)
        self._workspace = workspace
        self.last_measurement: Any = None
        #: How many times the engine asked. Observability only; nothing reads it.
        self.evaluations = 0

    def __call__(self) -> bool:
        """True when the declared criteria are satisfied — the engine's question."""
        from wisp.core.convergence import CommandProbe

        self.evaluations += 1
        self.last_measurement = CommandProbe(self._specs).measure(self._workspace)
        verdict = evaluate(self._criteria, self.last_measurement.evidence,
                           self.last_measurement.observations)
        # The declared criteria are the WHOLE set here, so a FAIL whose named criterion
        # is non-floor is exactly "a declared criterion failed" — ADR-0053 R4's condition,
        # asked of a declared-only set.
        return not verdict_keys_on_declared(verdict)


def declared_criteria_gate(prompt: str, workspace: str) -> "DeclaredCriteriaGate | None":
    """The engine's gate for a prompt that declares criteria, or `None` if it declares none.

    Built by the **runtime** and handed to the engine as a read-only callable. Raises
    `CriteriaDeclarationRejected` for a malformed head block — ADR-0050 R4's loud rule,
    and the caller must not wrap it in a handler that would turn it into a silent
    floor-only run (ADR-0053 R6).
    """
    from wisp.core.convergence import explain_acceptance, parse_criteria_declaration

    declaration = parse_criteria_declaration(prompt, workspace)
    if declaration is None or not declaration.specs:
        return None
    # The SAME derivation the verdict site uses, so the gate and the record cannot
    # disagree about what the declaration means.
    derivation = explain_acceptance(prompt, workspace, use_declaration=True)
    return DeclaredCriteriaGate(derivation.criteria, declaration.specs, workspace)


def compose_declared_nudge(attempt: int = 1) -> str:
    """The intervention text for a declared-criteria withholding (**ADR-0054 R4**).

    Home-module rule (GH#27): the text lives beside the condition it describes, so the
    intervention cannot drift from the signal. It names the **condition** (a criterion
    the objective declared is not satisfied) and the **instruction** (satisfy it, or say
    plainly why it cannot be), and never a specific next action — a nudge that names one
    would be a plan, and the model's own transcript carries what it needs.
    """
    ordinal = {1: "first", 2: "second"}.get(attempt, f"{attempt}th")
    return (
        f"[DECLARED CRITERIA] This is the {ordinal} check of the criteria this "
        "objective declared, and at least one of them is not satisfied. The "
        "declaration is part of the task, not a suggestion: satisfy the named "
        "criterion, or state plainly that it cannot be satisfied and why. Do not "
        "restate the plan — change what the criterion measures."
    )
