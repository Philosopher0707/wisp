"""Stagnation detection (migration P7).

Detects "working but not progressing", and routes it to the recovery ladder.

**The detector already exists — it is orphaned, not missing.** `OscillationTrap`
(`core/graph/loop.py:112`) detects exact 1-cycle repeats and 2-cycle oscillations of diff hashes, and
it is a genuine progress signal. What is missing is a caller:

| Piece | State before P7 |
|---|---|
| `OscillationTrap` | used **only** inside `ExecutionGraph.run` (`loop.py:142`) |
| `ExecutionGraph` | **zero** production callers — referenced only by the package re-export and `tests/test_architectural_upgrade.py` |
| `config.graph_oscillation_guard` | defined at `config.py:256/618/888` and **never read** by anything |

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

1. **N consecutive** non-progressing evaluations are required before stagnation is declared.
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
from wisp.core.graph.loop import OscillationTrap, diff_hash


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
            completed_nodes=self.completed_nodes, total_nodes=self.total_nodes)

    def is_progress_from(self, previous: "ProgressSignal") -> bool:
        """True when this signal is a STRICT improvement on `previous`.

        Any one of four strict improvements counts; merely being different does
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
        return False

    def to_dict(self) -> dict[str, Any]:
        return {"criteria_satisfied": self.criteria_satisfied,
                "failing_criteria": sorted(self.failing_criteria),
                "artifact_hashes": sorted(self.artifact_hashes),
                "completed_nodes": self.completed_nodes,
                "total_nodes": self.total_nodes}


@dataclass
class StagnationDetector:
    """Watches the progress signal and declares stagnation.

    `enabled` is fed from `config.graph_oscillation_guard` — the flag that has
    existed since before this migration and was **never read**. Reading it is
    the plan's explicit completion criterion.

    `min_consecutive` is the false-positive guard: one flat observation is
    normal (a turn that reads files makes no progress by construction), so
    stagnation requires a *run* of them.
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
    def trap_fired(self) -> bool:
        """True when the reused `OscillationTrap` saw a repeat or a cycle.

        A repeat is *evidence* of stagnation, but not sufficient on its own:
        an identical state can legitimately recur while progress continues
        elsewhere. It is reported, and `observe` remains the authority.
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
        """
        if not self.enabled:
            return True
        if self.consecutive_flat >= self.min_consecutive:
            return False
        return not self.trap_fired

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled,
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
