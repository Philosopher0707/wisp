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

**What this does NOT do.** It does not make the engine withhold `done` on a declared
failure: the verdict is computed after the engine has emitted `done`, and the turn-level
`done` gate (ADR-0036) is unchanged. The consumption point is `goal.derive_goal_state`,
which already reads the verdict — so a declared failure lands `GOAL_FAILED` (row 3) where
a floor-only verdict would have landed `GOAL_UNVERIFIED`. That routing is stated in the
ADR, not implied here.
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
                  enabled: bool) -> TurnCriteria:
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
    measurement = CommandProbe(declaration.specs).measure(workspace)

    return TurnCriteria(
        criteria=base.criteria + tuple(derivation.criteria),
        evidence=base.evidence + tuple(measurement.evidence),
        observations=dict(measurement.observations),
        declared=True,
        lines=tuple(measurement.lines),
    )


def turn_acceptance_verdict(guard: Any, prompt: str, workspace: str, *,
                            enabled: bool) -> tuple[CompletionVerdict, TurnCriteria]:
    """The turn's acceptance verdict, and the criteria set it was computed over."""
    tc = turn_criteria(guard, prompt, workspace, enabled=enabled)
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
