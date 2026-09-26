"""Stagnation detection (migration P7).

Detects "working but not progressing", and routes it to the recovery ladder.

**The detector already existed — it was orphaned, not missing.** `OscillationTrap` detects exact
1-cycle repeats and 2-cycle oscillations of diff hashes, and it is a genuine progress signal. What was
missing was a caller:

| Piece | State before P7 |
|---|---|
| `OscillationTrap` | used **only** inside `ExecutionGraph.run` |
| `ExecutionGraph` | **zero** production callers — referenced only by the package re-export and `tests/test_architectural_upgrade.py` |
| `config.graph_oscillation_guard` | defined at `config.py:256/618/888` and **never read** by anything |

*(ADR-0060 R5, 2026-09-25 — `OscillationTrap` and `diff_hash` have since **moved** to
`wisp/core/oscillation.py`. This module is on the live turn path, and it was importing them from
`wisp/core/graph/` — the layer ADR-0001 named *disowned*, which made that claim false. The trap is
unchanged; only its module moved, and `core/graph/loop.py` re-exports both names so nothing that
imported them from there breaks. The table above describes the state **before P7** and is kept as
history.)*

So the whole Layer C phase loop — trap, graph, ceiling — is a self-consistent mechanism with no
production entry point. P7 does not reimplement the trap; it **uses** it, and supplies the progress
signal it needs.

**The four inputs already existed and were unconnected** (the plan's second item):

| Input | Source | Was |
|---|---|---|
| criteria satisfied | `core/acceptance.py::evaluate` (P3) | no turn-path caller |
| artifact content hashes | `graph/types.py::GraphArtifact.content_hash` | not fed to the turn loop |
| failing-criteria set | the acceptance verdict's `unmet_criteria` | absent |
| completed-node count | `core/task_graph.py::TaskGraph` (P4) | not connected to anything |

**False positives are the risk** (`Low-medium` in the plan: *"flagging productive work as
stagnated"*). Two mitigations, both structural rather than tuned:

1. **N consecutive** non-progressing evaluations are required before the **verdict** declares
   stagnation (`min_consecutive`; `observe()` and `verdict`). This is **not** the completion
   predicate's threshold: `may_report_goal_met()` closes on the **first** flat observation, because
   it also closes on `trap_fired` and the reused `OscillationTrap` latches. The effective detection
   threshold for the predicate is therefore **1** (ADR-0037).
2. The metric uses the **strictly shrank** formulation — a set that merely *changed* is not progress,
   and neither is one that failed to grow. Only a strict improvement counts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

# Reuse the existing, tested detector rather than reimplementing it. A second
# 1-cycle/2-cycle implementation would be a second authority for "is this a
# repeat?", which is the defect class this migration exists to remove.
from wisp.core.oscillation import OscillationTrap, diff_hash


class StagnationVerdict(StrEnum):
    PROGRESSING = "progressing"
    STAGNATING = "stagnating"
    UNKNOWN = "unknown"        # not enough observations to say


@dataclass(frozen=True)
class ProgressSignal:
    """One observation of "how far along are we?".

    Every field is derived from a mechanism that already exists; P7 only
    connects them. Defaults are the *empty* observation, so a caller that can
    only supply some of them still produces a valid signal rather than a
    half-built one.
    """

    criteria_satisfied: int = 0
    failing_criteria: frozenset[str] = frozenset()
    artifact_hashes: frozenset[str] = frozenset()
    completed_nodes: int = 0
    total_nodes: int = 0
    #: The **action identities** of the work observed so far (migration M13).
    #: Each is an `action_key(tool, args)` digest — the same identity P1 stamps
    #: on the journal — so *repeating* one action is visible as a repeat.
    #:
    #: NOT `TaskNode.work_unit`. That is the protocol `tool_call_id`, and a
    #: repeated call gets a **fresh** one, so it would make every repeat look
    #: like new work. The identity stagnation needs is the action's, not the
    #: call's (ADR-0034, F33).
    work_units: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        """True when this observation carries **no information at all**.

        Not the same as "nothing changed". `from_verdict_and_graph(None, None)`
        yields this, because both of its inputs are opt-in records that default
        off — so on a default configuration the turn-end signal is empty on
        every turn. Counting two empty observations as "a flat run" declared
        every multi-turn session stagnating (F32). An empty observation is
        therefore not fed to the trap and not counted as flat.
        """
        return not (self.criteria_satisfied or self.failing_criteria
                    or self.artifact_hashes or self.completed_nodes
                    or self.total_nodes or self.work_units)

    @classmethod
    def from_verdict_and_graph(cls, verdict: Any = None,
                               graph: Any = None) -> "ProgressSignal":
        """Build a signal from a P3 verdict and a P4 graph.

        Both are optional: a turn may have neither (a content-only turn), and
        the resulting signal is then the empty observation rather than an
        error. That is what makes the detector usable on every turn instead of
        only on graph-driven ones.
        """
        satisfied = 0
        failing: frozenset[str] = frozenset()
        if verdict is not None:
            verdict_value = getattr(verdict, "verdict", None)
            if verdict_value is not None and str(verdict_value) == "pass":
                satisfied = 1
            failing = frozenset(getattr(verdict, "unmet_criteria", ()) or ())

        hashes: frozenset[str] = frozenset()
        completed = total = 0
        if graph is not None:
            nodes = list(getattr(graph, "nodes", ()) or ())
            total = len(nodes)
            completed = sum(
                1 for n in nodes
                if str(getattr(getattr(n, "status", ""), "value",
                               getattr(n, "status", ""))) in (
                    "success", "failure", "timeout", "cancelled", "skipped",
                    "invalidated", "superseded")
            )
        return cls(criteria_satisfied=satisfied, failing_criteria=failing,
                   artifact_hashes=hashes, completed_nodes=completed,
                   total_nodes=total)

    def with_artifact(self, content_hash: str) -> "ProgressSignal":
        if not content_hash:
            return self
        return ProgressSignal(
            criteria_satisfied=self.criteria_satisfied,
            failing_criteria=self.failing_criteria,
            artifact_hashes=self.artifact_hashes | {content_hash},
            completed_nodes=self.completed_nodes, total_nodes=self.total_nodes,
            work_units=self.work_units)

    def with_work(self, *, work_unit: str = "",
                  outcome_hash: str = "") -> "ProgressSignal":
        """Fold one observed work unit — and its outcome — in, immutably.

        Mirrors `with_artifact`: a new signal, never a mutation, so the detector
        keeps every observation it was given. The live turn path accumulates
        this way, because the signal it builds must come from state that is
        **always present** rather than from the opt-in records `from_verdict_and_graph`
        reads (F32).

        An empty fold returns `self`, so a caller that observed nothing does not
        manufacture a new object that looks like an observation.
        """
        if not work_unit and not outcome_hash:
            return self
        return ProgressSignal(
            criteria_satisfied=self.criteria_satisfied,
            failing_criteria=self.failing_criteria,
            artifact_hashes=(self.artifact_hashes | {outcome_hash}
                             if outcome_hash else self.artifact_hashes),
            completed_nodes=self.completed_nodes, total_nodes=self.total_nodes,
            work_units=(self.work_units | {work_unit}
                        if work_unit else self.work_units))

    def is_progress_from(self, previous: "ProgressSignal") -> bool:
        """True when this signal is a STRICT improvement on `previous`.

        Any one of five strict improvements counts; merely being different does
        not. A changed failing-criteria set of the same size is not progress —
        that is churn, and treating it as progress is how a detector misses a
        real oscillation.
        """
        if self.criteria_satisfied > previous.criteria_satisfied:
            return True
        if self.failing_criteria < previous.failing_criteria:      # strict subset
            return True
        if self.completed_nodes > previous.completed_nodes:
            return True
        if self.artifact_hashes - previous.artifact_hashes:        # a NEW artifact
            return True
        if self.work_units - previous.work_units:                  # a NEW action
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"criteria_satisfied": self.criteria_satisfied,
                "failing_criteria": sorted(self.failing_criteria),
                "artifact_hashes": sorted(self.artifact_hashes),
                "completed_nodes": self.completed_nodes,
                "total_nodes": self.total_nodes,
                "work_units": sorted(self.work_units)}


@dataclass
class StagnationDetector:
    """Watches the progress signal and declares stagnation.

    `enabled` is fed from `config.graph_oscillation_guard` — the flag that has
    existed since before this migration and was **never read**. Reading it is
    the plan's explicit completion criterion.

    `min_consecutive` is the false-positive guard: one flat observation is
    normal (a turn that reads files makes no progress by construction), so the
    **verdict** requires a *run* of them. It does **not** gate the completion
    predicate — `may_report_goal_met()` closes on the first flat observation
    (ADR-0037).
    """

    enabled: bool = True
    min_consecutive: int = 2
    observations: list[ProgressSignal] = field(default_factory=list)
    consecutive_flat: int = 0
    #: The existing detector, reused. Fed a hash of the observable state so it
    #: sees the same 1-cycle/2-cycle structure it was written for.
    trap: OscillationTrap = field(default_factory=OscillationTrap)
    trap_verdicts: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, config: Any = None, **kwargs: Any) -> "StagnationDetector":
        """Build from a config, reading `graph_oscillation_guard`.

        Defaults to enabled when the config or the attribute is absent — the
        flag's own declared default is `True`, so an absent config means the
        documented behaviour rather than a silent disable.
        """
        enabled = True
        if config is not None:
            enabled = bool(getattr(config, "graph_oscillation_guard", True))
        return cls(enabled=enabled, **kwargs)

    def observe(self, signal: ProgressSignal) -> StagnationVerdict:
        """Fold one observation in and return the current verdict.

        Returns `UNKNOWN` until there is a baseline to compare against, and
        `PROGRESSING` whenever the signal strictly improved. Only a run of
        `min_consecutive` flat observations yields `STAGNATING`.
        """
        if not self.enabled:
            return StagnationVerdict.PROGRESSING

        if signal.is_empty:
            # "We learned nothing" is not "nothing changed" (F32). An empty
            # observation is not appended, not fed to the trap and not counted
            # as flat: the alternative declared every multi-turn session
            # stagnating on a default configuration, because both of the
            # turn-end signal's inputs are opt-in records that default off.
            return StagnationVerdict.UNKNOWN

        previous = self.observations[-1] if self.observations else None
        self.observations.append(signal)

        # Feed the existing trap the observable state's identity, so a repeat
        # or an A→B→A oscillation is caught by the code written for it.
        trap_verdict = self.trap.observe(diff_hash(_state_digest(signal)))
        if trap_verdict:
            self.trap_verdicts.append(trap_verdict)

        if previous is None:
            return StagnationVerdict.UNKNOWN

        if signal.is_progress_from(previous):
            self.consecutive_flat = 0
            return StagnationVerdict.PROGRESSING

        self.consecutive_flat += 1
        if self.consecutive_flat >= self.min_consecutive:
            return StagnationVerdict.STAGNATING
        return StagnationVerdict.UNKNOWN

    @property
    def verdict(self) -> StagnationVerdict:
        """The current verdict, derived from the accumulated observations.

        The single authority for "what does the detector say now?", so a record
        written from `to_dict()` is self-describing rather than leaving a reader
        to re-derive the conclusion from `consecutive_flat`.
        """
        if not self.enabled:
            return StagnationVerdict.PROGRESSING
        if not self.observations:
            return StagnationVerdict.UNKNOWN
        if self.consecutive_flat >= self.min_consecutive:
            return StagnationVerdict.STAGNATING
        return StagnationVerdict.PROGRESSING

    @property
    def trap_fired(self) -> bool:
        """True when the reused `OscillationTrap` saw a repeat or a cycle.

        **The latch.** `trap_verdicts` is only ever appended (`observe`), so
        once this is true it stays true for the detector's lifetime. The only
        reset is the turn boundary, where `run_turn` builds a fresh detector
        (`core/runtime.py:731`). Later genuine progress does not clear it:
        `is_progress_from()` may reset `consecutive_flat` to 0, and the
        predicate is still `False`.

        For the **completion predicate** this is decisive —
        `may_report_goal_met()` returns `not trap_fired`, so a repeat *is*
        sufficient to close it (ADR-0037). `observe`/`verdict` remain the
        authority for the **verdict**, a different fact with a different
        threshold (`min_consecutive`).
        """
        return bool(self.trap_verdicts)

    @property
    def latest_trap_verdict(self) -> str:
        return self.trap_verdicts[-1] if self.trap_verdicts else ""

    def may_report_goal_met(self) -> bool:
        """False once stagnating — a stagnated goal must never reach `GOAL_MET`.

        This is the plan's fourth item, expressed as a predicate rather than a
        convention: the caller cannot report success without asking, and the
        answer is `False` while stagnation stands.

        **Monotonic (ADR-0037).** The predicate is `False` from the **first**
        flat observation — *before* `min_consecutive` is reached — because it
        closes on `trap_fired` as well as on the flat run, and `trap_fired`
        latches. Once closed it never reopens inside the detector's lifetime:

        * `is_progress_from()` returning `True` (and resetting
          `consecutive_flat` to 0) does **not** reopen it — progress after the
          trap does not restore `GOAL_MET`;
        * a replan intervention may continue execution and improve the work,
          and still cannot reopen it;
        * the only exit is the end of the detector's lifetime — a new turn
          constructs a new detector (`core/runtime.py:731`), so the latch is
          **per turn**, never per session and never global.

        `min_consecutive` therefore gates the **verdict** only
        (`observe`/`verdict`). It is not this predicate's threshold, and it
        cannot be: a predicate depending on `consecutive_flat` alone would
        reopen the moment progress reset the counter.

        The arbiter consumes this predicate's **value** as its row-4 input
        (`core/runtime.py:1176`), and the record carries the value it saw
        (`stagnation_allows_goal_met`, `core/runtime.py:1265`) so replay
        reproduces the goal state without re-deriving it.
        """
        if not self.enabled:
            return True
        if self.consecutive_flat >= self.min_consecutive:
            return False
        return not self.trap_fired

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled,
                "verdict": str(self.verdict),
                "min_consecutive": self.min_consecutive,
                "consecutive_flat": self.consecutive_flat,
                "observations": [o.to_dict() for o in self.observations],
                "trap_verdicts": list(self.trap_verdicts)}


def _state_digest(signal: ProgressSignal) -> str:
    """A stable string identity for the trap to hash.

    Ordered and explicit: `frozenset` iteration order is not stable across
    processes, and an unstable digest would make the trap fire on noise.
    """
    return "|".join((
        f"c={signal.criteria_satisfied}",
        "f=" + ",".join(sorted(signal.failing_criteria)),
        "a=" + ",".join(sorted(signal.artifact_hashes)),
        "w=" + ",".join(sorted(signal.work_units)),
        f"n={signal.completed_nodes}/{signal.total_nodes}",
    ))


def route_to_recovery(detector: StagnationDetector, ladder: Any,
                      evidence: Iterable[str] = ()) -> Any:
    """Route a detected stagnation to the recovery ladder.

    Uses P6's `FailureClass.STAGNATION`, whose legal rungs are Global Replan →
    Diagnostic → Escalate — *not* Retry. The plan is explicit that detection
    must trigger a replan rather than a retry: retrying the same action against
    the same state is the definition of the loop being detected.

    Returns the ladder's `RecoveryDecision`, or `None` when there is nothing to
    route (not stagnating, or the detector is disabled).
    """
    from wisp.core.recovery import FailureClass

    if not detector.enabled or detector.consecutive_flat < detector.min_consecutive:
        return None
    ev = tuple(evidence) or (
        f"{detector.consecutive_flat} consecutive non-progressing observations",)
    return ladder.decide(FailureClass.STAGNATION, ev,
                         reason="progress signal flat")


def compose_replan_nudge(attempt: int = 1) -> str:
    """Build M13's in-turn replan intervention (ADR-0036).

    *attempt* is the run-context detail — the first or second intervention this
    turn — exactly as `reason` is for `verification.compose_nudge`. The
    invariant, the rejection and the instruction all come from this module, so
    the prose can never drift from the gate that emits it (GH#27).

    **Replan, never retry.** `route_to_recovery`'s rule above applies here too:
    retrying the same action against the same state *is* the loop being
    detected, and `FORBIDDEN_RUNGS[FailureClass.STAGNATION]` contains `RETRY`
    (`core/recovery.py:131`). The text therefore asks for a different next step
    and **never names the repeated action** — the model's own transcript already
    carries it, and naming it would require the completion seam to carry more
    than a predicate.

    The `[SYSTEM]` prefix is load-bearing: it is how the runtime decides an
    injection is provider-visible and must be persisted into the transcript
    (`core/runtime.py`, the `system` branch), so a replan the model saw is a
    replan that survives resume.

    The second attempt differs deliberately. A verbatim repeat after a full
    provider round carries no new signal — the same reasoning
    `verification.SHORT_REPEAT_NUDGE` records for its short form.
    """
    if attempt <= 1:
        return (
            "[SYSTEM] Stagnation loop: your recent steps are repeating without "
            "progress. Do not repeat the same action against the same state. "
            "Reconsider the approach and take a materially different next step, "
            "or state plainly what is blocking progress."
        )
    return (
        "[SYSTEM] Stagnation loop (repeat): progress is still stalled after the "
        "previous instruction. Repeating the current approach will not work. "
        "Change strategy now, or report the work as blocked or UNVERIFIED "
        "instead of claiming success."
    )
