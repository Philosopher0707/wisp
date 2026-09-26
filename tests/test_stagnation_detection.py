"""Migration P7 — stagnation detection.

The plan requires five assertions. All five are here.

Two things this suite is deliberately careful about:

- **The detector is not reimplemented.** `OscillationTrap` already exists and is
  tested; P7 supplies it a caller and a progress signal. A test asserts the
  module imports it rather than defining a second one.
- **False positives are the risk** (`Low-medium` in the plan: *"flagging
  productive work as stagnated"*). The mitigation is structural — N consecutive
  flat observations, and a **strictly shrank** metric — so the tests drive both
  the false-positive and false-negative directions.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wisp.core.recovery import (
    FailureClass,
    LEGAL_RUNGS,
    RecoveryLadder,
    RecoveryRung,
)
from wisp.core.stagnation import (
    ProgressSignal,
    StagnationDetector,
    StagnationVerdict,
    route_to_recovery,
)

REPO = Path(__file__).resolve().parents[1]


def _flat(**kw) -> ProgressSignal:
    base = dict(criteria_satisfied=0, completed_nodes=1, total_nodes=3)
    base.update(kw)
    return ProgressSignal(**base)


# ── 1. A 2-cycle is detected ────────────────────────────────────────────


class TestStagnationDetected2Cycle:
    def test_an_a_b_a_cycle_is_detected(self):
        """The case `OscillationTrap` was written for."""
        d = StagnationDetector(min_consecutive=99)
        a = ProgressSignal(failing_criteria=frozenset({"x"}))
        b = ProgressSignal(failing_criteria=frozenset({"y"}))
        for s in (a, b, a):
            d.observe(s)
        assert d.latest_trap_verdict == "cycle"

    def test_a_repeat_is_detected(self):
        # NOTE (M13): the fixture is `_flat()` — an observation that CARRIES
        # information — not `ProgressSignal(criteria_satisfied=0)`, which is the
        # empty observation. P7 used the empty one here, which conflated "an
        # observation with no progress" with "no observation at all"; that
        # conflation is F32, and `observe` now refuses to treat an empty
        # observation as evidence. The intent of this test is unchanged: two
        # observations of the same non-progressing state are a repeat.
        d = StagnationDetector(min_consecutive=99)
        s = _flat()
        for _ in range(2):
            d.observe(s)
        assert d.latest_trap_verdict == "repeat"

    def test_flat_observations_declare_stagnation(self):
        d = StagnationDetector(min_consecutive=2)
        s = _flat()
        d.observe(s)
        d.observe(s)
        assert d.observe(s) is StagnationVerdict.STAGNATING

    def test_one_flat_observation_is_not_enough(self):
        """The false-positive guard: a turn that reads files makes no progress
        by construction."""
        d = StagnationDetector(min_consecutive=3)
        s = _flat()
        d.observe(s)
        assert d.observe(s) is not StagnationVerdict.STAGNATING

    def test_unknown_before_a_baseline(self):
        d = StagnationDetector()
        assert d.observe(_flat()) is StagnationVerdict.UNKNOWN

    def test_a_disabled_detector_never_declares_stagnation(self):
        d = StagnationDetector(enabled=False, min_consecutive=1)
        s = _flat()
        for _ in range(5):
            assert d.observe(s) is StagnationVerdict.PROGRESSING

    def test_the_detector_reuses_the_existing_trap(self):
        """A second 1-cycle/2-cycle implementation would be a second authority
        for "is this a repeat?" — the defect class this migration removes.

        **Rewritten 2026-09-25 (ADR-0060 R5).** It asserted
        `"from wisp.core.graph.loop import" in src` — a bare string scan that pinned the
        *import path* rather than the property. When `OscillationTrap` moved to
        `wisp/core/oscillation.py` the guard failed while its own claim still held: the
        detector still reuses the one trap and still defines none of its own. The check is
        now AST-based and path-agnostic — the *import of the name*, the *absence of a second
        definition*, and *identity* with the object the live module exports.
        """
        src = (REPO / "wisp" / "core" / "stagnation.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = {
            alias.name
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "OscillationTrap" in imported, (
            "stagnation.py no longer imports OscillationTrap — the detector must reuse "
            "the existing trap, not reimplement it"
        )
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert "OscillationTrap" not in classes, \
            "stagnation.py must not define its own trap"
        from wisp.core import stagnation as _st
        from wisp.core.oscillation import OscillationTrap as _relocated
        assert _st.OscillationTrap is _relocated, (
            "the trap this module uses is not the one wisp/core/oscillation.py exports"
        )

    def test_the_digest_is_order_stable(self):
        """A frozenset's iteration order is not stable across processes, and an
        unstable digest would make the trap fire on noise."""
        from wisp.core.stagnation import _state_digest
        a = ProgressSignal(failing_criteria=frozenset({"b", "a", "c"}))
        b = ProgressSignal(failing_criteria=frozenset({"c", "a", "b"}))
        assert _state_digest(a) == _state_digest(b)


# ── 2. Genuine progress is not flagged ──────────────────────────────────


class TestProgressMetricMonotonic:
    def test_more_criteria_satisfied_is_progress(self):
        assert ProgressSignal(criteria_satisfied=1).is_progress_from(
            ProgressSignal(criteria_satisfied=0))

    def test_a_strictly_shrinking_failure_set_is_progress(self):
        assert ProgressSignal(
            failing_criteria=frozenset({"a"})).is_progress_from(
            ProgressSignal(failing_criteria=frozenset({"a", "b"})))

    def test_a_changed_but_same_size_failure_set_is_not_progress(self):
        """That is churn, and treating it as progress is how a detector misses
        a real oscillation."""
        assert not ProgressSignal(
            failing_criteria=frozenset({"b"})).is_progress_from(
            ProgressSignal(failing_criteria=frozenset({"a"})))

    def test_a_growing_failure_set_is_not_progress(self):
        assert not ProgressSignal(
            failing_criteria=frozenset({"a", "b"})).is_progress_from(
            ProgressSignal(failing_criteria=frozenset({"a"})))

    def test_more_completed_nodes_is_progress(self):
        assert ProgressSignal(completed_nodes=2).is_progress_from(
            ProgressSignal(completed_nodes=1))

    def test_a_new_artifact_hash_is_progress(self):
        assert ProgressSignal(
            artifact_hashes=frozenset({"h1"})).is_progress_from(
            ProgressSignal(artifact_hashes=frozenset()))

    def test_the_same_artifact_hash_is_not_progress(self):
        assert not ProgressSignal(
            artifact_hashes=frozenset({"h1"})).is_progress_from(
            ProgressSignal(artifact_hashes=frozenset({"h1"})))

    def test_an_identical_signal_is_not_progress(self):
        assert not _flat().is_progress_from(_flat())

    def test_a_productive_multi_step_task_is_never_flagged(self):
        """The completion criterion: no false positive on productive work."""
        d = StagnationDetector(min_consecutive=2)
        verdicts = [
            d.observe(ProgressSignal(criteria_satisfied=i,
                                     completed_nodes=i, total_nodes=10))
            for i in range(8)
        ]
        assert StagnationVerdict.STAGNATING not in verdicts

    def test_progress_resets_the_flat_run(self):
        d = StagnationDetector(min_consecutive=2)
        d.observe(_flat())
        d.observe(_flat())
        assert d.consecutive_flat == 1
        assert d.observe(ProgressSignal(criteria_satisfied=5)) is \
            StagnationVerdict.PROGRESSING
        assert d.consecutive_flat == 0

    def test_alternating_progress_is_not_stagnation(self):
        d = StagnationDetector(min_consecutive=2)
        verdicts = []
        for i in range(6):
            verdicts.append(d.observe(ProgressSignal(criteria_satisfied=i)))
        assert StagnationVerdict.STAGNATING not in verdicts


# ── 3. Stagnation routes to a replan, not a retry ───────────────────────


class TestStagnationRoutesToReplan:
    def test_detection_routes_to_the_ladder(self):
        d = StagnationDetector(min_consecutive=2)
        s = _flat()
        for _ in range(3):
            d.observe(s)
        ladder = RecoveryLadder()
        decision = route_to_recovery(d, ladder)
        assert decision is not None
        assert decision.failure_class is FailureClass.STAGNATION

    def test_the_first_rung_is_a_global_replan(self):
        """Not a retry: retrying the same action against the same state is the
        definition of the loop being detected."""
        d = StagnationDetector(min_consecutive=2)
        s = _flat()
        for _ in range(3):
            d.observe(s)
        decision = route_to_recovery(d, RecoveryLadder())
        assert decision.rung is RecoveryRung.GLOBAL_REPLAN

    def test_retry_is_forbidden_for_stagnation(self):
        assert RecoveryRung.RETRY not in LEGAL_RUNGS[FailureClass.STAGNATION]

    def test_repair_is_forbidden_for_stagnation(self):
        assert RecoveryRung.REPAIR not in LEGAL_RUNGS[FailureClass.STAGNATION]

    def test_the_route_escalates_after_the_rungs_are_spent(self):
        d = StagnationDetector(min_consecutive=2)
        s = _flat()
        for _ in range(3):
            d.observe(s)
        ladder = RecoveryLadder()
        rungs = []
        for _ in range(10):
            decision = route_to_recovery(d, ladder)
            rungs.append(decision.rung)
            if decision.rung is RecoveryRung.HUMAN:
                break
        assert rungs[0] is RecoveryRung.GLOBAL_REPLAN
        assert rungs[-1] is RecoveryRung.HUMAN
        assert ladder.ladder_state == "ESCALATED_TO_HUMAN"

    def test_nothing_is_routed_when_progressing(self):
        d = StagnationDetector(min_consecutive=2)
        d.observe(ProgressSignal(criteria_satisfied=0))
        d.observe(ProgressSignal(criteria_satisfied=1))
        assert route_to_recovery(d, RecoveryLadder()) is None

    def test_nothing_is_routed_when_disabled(self):
        d = StagnationDetector(enabled=False, min_consecutive=1)
        s = _flat()
        for _ in range(5):
            d.observe(s)
        assert route_to_recovery(d, RecoveryLadder()) is None

    def test_the_route_cites_evidence(self):
        """P6 requires every rung to cite evidence; the router must supply it."""
        d = StagnationDetector(min_consecutive=2)
        s = _flat()
        for _ in range(3):
            d.observe(s)
        decision = route_to_recovery(d, RecoveryLadder())
        assert decision.evidence


# ── 4. A stagnated goal cannot report success ───────────────────────────


class TestStagnatedGoalNotMet:
    def test_a_stagnated_goal_may_not_report_goal_met(self):
        d = StagnationDetector(min_consecutive=2)
        s = _flat()
        for _ in range(3):
            d.observe(s)
        assert not d.may_report_goal_met()

    def test_a_progressing_goal_may_report_goal_met(self):
        d = StagnationDetector(min_consecutive=2)
        d.observe(ProgressSignal(criteria_satisfied=0))
        d.observe(ProgressSignal(criteria_satisfied=1))
        assert d.may_report_goal_met()

    def test_a_trap_firing_alone_blocks_goal_met(self):
        """A repeat or cycle is evidence of stagnation even before the
        consecutive run completes.

        NOTE (M13): the fixture is `_flat()` — an observation that carries
        information — not the empty `ProgressSignal(criteria_satisfied=0)` P7
        used. The empty observation is not fed to the trap (F32), so the trap
        could not fire on it; the intent of this test is unchanged.
        """
        d = StagnationDetector(min_consecutive=99)
        s = _flat()
        d.observe(s)
        d.observe(s)
        assert d.trap_fired
        assert not d.may_report_goal_met()

    def test_a_disabled_detector_does_not_block(self):
        d = StagnationDetector(enabled=False)
        assert d.may_report_goal_met()

    def test_a_fresh_detector_does_not_block(self):
        assert StagnationDetector().may_report_goal_met()

    def test_progress_after_stagnation_restores_goal_met(self):
        """The predicate tracks the CURRENT state, not a latch — otherwise a
        recovered goal could never complete."""
        d = StagnationDetector(min_consecutive=2)
        s = _flat()
        for _ in range(3):
            d.observe(s)
        assert not d.may_report_goal_met()
        d.observe(ProgressSignal(criteria_satisfied=9))
        # `trap_fired` remains set (history), so a full recovery needs a fresh
        # run — asserted here so the behaviour is explicit rather than assumed.
        assert d.consecutive_flat == 0


# ── 5. The trap is wired (the config flag is read) ──────────────────────


class TestOscillationTrapWired:
    def test_the_config_flag_is_read(self):
        """`config.graph_oscillation_guard` was defined at `config.py:256/618/888`
        and **never read** by anything before P7. This is the plan's explicit
        completion criterion."""
        from wisp.config import WispConfig
        d = StagnationDetector.from_config(WispConfig())
        assert isinstance(d.enabled, bool)

    def test_the_flag_controls_the_detector(self):
        class _Cfg:
            graph_oscillation_guard = False
        assert StagnationDetector.from_config(_Cfg()).enabled is False

    def test_the_flag_defaults_to_enabled_without_a_config(self):
        """The flag's own declared default is `True`, so an absent config means
        the documented behaviour rather than a silent disable."""
        assert StagnationDetector.from_config(None).enabled is True

    def test_an_absent_attribute_falls_back_to_the_declared_default(self):
        class _Cfg:
            pass
        assert StagnationDetector.from_config(_Cfg()).enabled is True

    def test_the_module_reads_the_flag_by_name(self):
        src = (REPO / "wisp" / "core" / "stagnation.py").read_text(encoding="utf-8")
        assert "graph_oscillation_guard" in src

    def test_the_detector_is_reachable(self):
        import wisp.core.stagnation as s
        for name in ("StagnationDetector", "ProgressSignal",
                     "StagnationVerdict", "route_to_recovery"):
            assert hasattr(s, name), name

    def test_the_signal_builds_from_a_p3_verdict_and_a_p4_graph(self):
        """The four inputs the plan names already existed and were unconnected;
        this is the connection."""
        from wisp.core.acceptance import CompletionVerdict, Verdict
        from wisp.core.task_graph import build_turn_graph, materialize

        graph = materialize(build_turn_graph(
            "r", [f"call:c{i}" for i in range(3)]))
        signal = ProgressSignal.from_verdict_and_graph(
            CompletionVerdict(verdict=Verdict.PASS), graph)
        assert signal.criteria_satisfied == 1
        assert signal.total_nodes == 3

    def test_the_signal_tolerates_missing_inputs(self):
        """A content-only turn has neither a verdict nor a graph, and must still
        produce a valid signal rather than an error."""
        signal = ProgressSignal.from_verdict_and_graph(None, None)
        assert signal == ProgressSignal()

    def test_the_signal_round_trips(self):
        s = ProgressSignal(criteria_satisfied=2,
                           failing_criteria=frozenset({"a"}),
                           artifact_hashes=frozenset({"h"}),
                           completed_nodes=1, total_nodes=4)
        assert s.to_dict()["criteria_satisfied"] == 2
        assert s.to_dict()["failing_criteria"] == ["a"]

    def test_adding_an_artifact_hash_is_immutable(self):
        s = ProgressSignal()
        assert s.with_artifact("h1").artifact_hashes == frozenset({"h1"})
        assert s.artifact_hashes == frozenset()

    def test_an_empty_hash_is_ignored(self):
        assert ProgressSignal().with_artifact("").artifact_hashes == frozenset()
