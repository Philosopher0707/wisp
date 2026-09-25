"""ADR-0036 / ADR-0037 — behavioural validation of the bounded completion gate.

**Validation only.** This file adds cases; it changes no production behaviour and
no shipped default. Its purpose is to answer one operational question — does the
gate behave correctly and safely under the ratified monotonic-latch semantics,
and is the evidence sufficient to consider *enablement*?

The harness is imported from `test_post_m13_completion_enforcement` deliberately.
Re-implementing the plumbing would (a) create a second producer of one structure,
and (b) make this suite look independent while inheriting the same blind spots.
The new evidence here is the **cases that suite does not cover**:

* the intervention bound against the **loop** bound (a sweep, not one point)
* a detector disabled by `graph_oscillation_guard`
* the combined verification-rejection + closed-predicate pass
* the audit gap **measured** (predicate recorded, zero `STAGNATION` events)
* the isolation claim's missing half — no `ContextVar`, no stored detector
* the controlled enablement experiment, and a guard on the shipped default
* the engine/runtime asymmetry in guarding the *same* predicate call

Nothing here asserts reopening. Under ADR-0037 a closed predicate staying closed
is the **expected** behaviour, so a test that expected reopening would be stale
by construction.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from tests.reliability.test_post_m13_completion_enforcement import (
    REPO,
    _boom,
    _build,
    _content_only,
    _drive_core,
    _goal_records,
    _message_of,
    _read_call,
    _repeat,
    _replans,
    _run_turn,
    _tool_round,
)


def _journal(repo, sid: str = "pce") -> dict:
    """The journal-derived record lists for a session."""
    return (repo.reconstruct(sid).get("_journal") or {})


def _stagnation_events(repo, sid: str = "pce") -> list:
    """The `STAGNATION` records the turn wrote — the audit surface."""
    return _journal(repo, sid).get("stagnations") or []


def _always_closed():
    return False


def _raise_predicate(*_a, **_kw):
    raise RuntimeError("predicate exploded")


# ══════════════════════════════════════════════════════════════════════════
# 1. The bound is the LOOP bound, not merely two
# ══════════════════════════════════════════════════════════════════════════


class TestTheBoundInteractsWithTheLoopBound:
    """ADR-0036 §4's third condition — `iteration + 1 < max_iterations`.

    The bound is two interventions, but only while a further round exists. A
    gate that withheld on the last iteration would end the loop, run the budget
    wrap-up, and convert an honest surrender into a fatal
    `CODE_ITERATION_BUDGET`. The observable count is therefore
    `min(2, max_iterations - 1)` — which no single-point test can show.
    """

    @pytest.mark.parametrize("max_iterations,expected", [
        (1, 0),    # case A — the only iteration cannot be withheld
        (2, 1),    # case B — one further round exists, exactly once
        (3, 2),    # the bound binds exactly here
        (4, 2),    # case C — the budget, not the loop, is now binding
        (30, 2),   # the shipped default
    ])
    def test_interventions_are_capped_by_the_remaining_rounds(
            self, max_iterations, expected):
        _session, events = _drive_core(
            [_content_only("done")],
            max_iterations=max_iterations,
            completion_gate=_always_closed,
        )
        assert len(_replans(events)) == expected, (
            f"max_iterations={max_iterations}: expected {expected} "
            f"interventions, saw {len(_replans(events))}")
        assert [e for e in events if e.get("type") == "done"], (
            f"max_iterations={max_iterations} never reached `done` — the gate "
            "withheld past the loop bound")

    def test_no_third_intervention_is_ever_observed(self):
        """The hard exit criterion, on the shape that would expose an unbounded
        gate: a permanently closed predicate and the largest loop bound the
        config allows."""
        _session, events = _drive_core(
            [_content_only("done")], max_iterations=200,
            completion_gate=_always_closed)
        assert len(_replans(events)) == 2, (
            f"a third intervention was observed: {len(_replans(events))}")


# ══════════════════════════════════════════════════════════════════════════
# 2. Case F — the detector itself can be switched off
# ══════════════════════════════════════════════════════════════════════════


class TestADisabledDetectorCannotIntervene:
    def test_graph_oscillation_guard_off_means_no_intervention(self, tmp_path):
        """`graph_oscillation_guard` disables the **detector**; `stagnation_gate`
        gates **enforcement**. These are two rollback levels, not one.

        With the detector off the predicate is open by construction, so even an
        enabled gate must never intervene — and the goal record must show the
        predicate **open**, not "not evaluated".
        """
        rounds = _repeat("a.txt", 2) + [_content_only("done")]
        session, repo, events, _p = _run_turn(
            tmp_path, rounds, files={"a.txt": "hello"},
            stagnation_gate=True, graph_oscillation_guard=False)

        assert _replans(events) == [], (
            "the gate intervened with the detector disabled")
        record = _goal_records(repo)[0]
        assert record["stagnation_allows_goal_met"] is True
        assert record["goal_state"] != "goal_stagnated"


# ══════════════════════════════════════════════════════════════════════════
# 3. Verification authority — the combined pass
# ══════════════════════════════════════════════════════════════════════════


class TestVerificationAuthorityOnTheSamePass:
    """§11's combined case: the floor guard rejects **and** the predicate is
    closed, on the same turn.

    The floor guard cannot be made to reject on a live turn in this environment:
    `jsonschema` is absent (F8), so every tool call is refused before dispatch
    and `wrote_code` never becomes True. The guard is therefore subclassed to
    reject deterministically, and the *behavioural* question is asked — when both
    gates want to act, which one acts, and are the two budgets independent?
    """

    @staticmethod
    def _install_rejecting_guard(monkeypatch, rejections: int):
        from wisp.core.verification import VerificationFloorGuard as _Real

        class _Rejecting(_Real):
            """Rejects `rejections` times, then surrenders honestly."""

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._remaining = rejections

            def rejection(self):
                if self._remaining > 0:
                    self._remaining -= 1
                    return "verification floor rejection (validation stub)"
                return None

        monkeypatch.setattr(
            "wisp.core.verification.VerificationFloorGuard", _Rejecting)

    def test_the_floor_guard_owns_the_pass_it_rejects(
            self, tmp_path, monkeypatch):
        self._install_rejecting_guard(monkeypatch, rejections=1)
        _session, events = _drive_core(
            [_content_only("done")], max_iterations=30,
            completion_gate=_always_closed)

        nudges = [m for m in (_message_of(e) for e in events) if m]
        assert nudges, "neither gate emitted anything"
        assert not nudges[0].startswith("[SYSTEM] Stagnation loop"), (
            "the stagnation gate acted on a pass the verification floor owned — "
            "the ordering in the source is not holding in the field")

    @pytest.mark.parametrize("floor_rejections", [0, 1, 2])
    def test_the_two_budgets_are_independent(
            self, tmp_path, monkeypatch, floor_rejections):
        """The floor's blocking must neither consume nor extend the stagnation
        budget: the stagnation bound is exactly 2 whatever the floor does."""
        self._install_rejecting_guard(monkeypatch, rejections=floor_rejections)
        _session, events = _drive_core(
            [_content_only("done")], max_iterations=30,
            completion_gate=_always_closed)

        assert len(_replans(events)) == 2, (
            f"the floor guard's {floor_rejections} rejection(s) changed the "
            f"stagnation budget ({len(_replans(events))} interventions)")
        assert [e for e in events if e.get("type") == "done"], (
            "the turn never reached `done`")


# ══════════════════════════════════════════════════════════════════════════
# 4. The audit gap, measured rather than described
# ══════════════════════════════════════════════════════════════════════════


class TestTheAuditGapIsUnchanged:
    """ADR-0037's documented limitation, **measured**.

    When the trap closes the predicate below `min_consecutive`, `observe()`
    returns `UNKNOWN`, so the runtime's `stagnation_seen` stays False and the
    turn writes **zero** `STAGNATION` events — while the goal record still
    carries the predicate. The gap is in the *observations*, not in the decision.
    """

    def test_a_trap_closed_turn_records_the_predicate_and_no_event(
            self, tmp_path):
        session, repo, _ev, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"})

        record = _goal_records(repo)[0]
        assert record["stagnation_allows_goal_met"] is False, (
            "the predicate did not close below the threshold — the scenario "
            "no longer reproduces the gap")
        assert record["stagnation_verdict"] == "progressing", (
            "the verdict reached STAGNATING below the threshold — the gap "
            "must be re-measured")
        assert _stagnation_events(repo) == [], (
            "a STAGNATION event was recorded for a below-threshold closure — "
            "the documented audit gap has CHANGED")

    def test_a_run_at_the_threshold_does_record_one(self, tmp_path):
        """The contrast that makes the gap precise: it is the *below-threshold*
        case only. At or above `min_consecutive` the verdict is reached and the
        event IS written — and this is independent of `stagnation_gate`."""
        session, repo, _ev, _p = _run_turn(
            tmp_path, _repeat("a.txt", 4) + [_content_only("done")],
            files={"a.txt": "hello"})

        assert _stagnation_events(repo), (
            "the at-threshold case no longer records a STAGNATION event")
        assert _goal_records(repo)[0]["stagnation_verdict"] == "stagnating"


# ══════════════════════════════════════════════════════════════════════════
# 5. Isolation — the missing half of §15
# ══════════════════════════════════════════════════════════════════════════


class TestNoSharedStateWasIntroduced:
    """§15's explicit list: no global counter, no `ContextVar` counter, no
    shared detector, no shared mutable gate state.

    The counter is already pinned as a local by the enforcement suite. What is
    checked here is the rest of the list.
    """

    def test_no_contextvar_in_the_engine_or_the_runtime(self):
        for rel in ("wisp/core/stateless.py", "wisp/core/runtime.py"):
            src = (REPO / rel).read_text()
            assert "ContextVar" not in src, (
                f"{rel} introduced a ContextVar — a task-local counter is still "
                "shared state across turns of one task")

    def test_the_detector_and_the_gate_are_locals_never_attributes(self):
        src = (REPO / "wisp" / "core" / "runtime.py").read_text()
        attrs = {n.attr for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Attribute)}
        assert "stagnation_detector" not in attrs, (
            "the detector is stored on an object — it would be shared state")
        assert "completion_gate" not in attrs, (
            "the gate closure is stored on an object — it would outlive the turn")

    def test_no_module_level_mutable_stagnation_state(self):
        """A module-level list/dict/set/call bound to a stagnation name would be
        process-global state. The one module-level constant that exists
        (`_MAX_STAGNATION_INTERVENTIONS`) is an int, and is pinned separately."""
        for rel in ("wisp/core/stateless.py", "wisp/core/runtime.py",
                    "wisp/core/stagnation.py"):
            tree = ast.parse((REPO / rel).read_text())
            for node in tree.body:
                if not isinstance(node, ast.Assign):
                    continue
                names = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if not any("stagnation" in n or "completion_gate" in n
                           for n in names):
                    continue
                assert isinstance(node.value, ast.Constant), (
                    f"{rel}: module-level {names} is bound to "
                    f"{type(node.value).__name__}, not a constant — process-"
                    "global mutable state")


# ══════════════════════════════════════════════════════════════════════════
# 6. The engine guards its call to the predicate; the runtime does not
# ══════════════════════════════════════════════════════════════════════════


class TestThePredicateCallIsGuardedInOnePlaceOnly:
    """An **observed asymmetry**, reported rather than repaired.

    ADR-0036 §5 requires the engine to fail open. The runtime's post-turn read
    of the *same* predicate (`_stagnating`) is not guarded. `may_report_goal_met`
    is a pure, total function over dataclass fields, so this cannot be reached in
    production — the test exists so the asymmetry is a known difference rather
    than a surprise if it ever becomes reachable.
    """

    def test_the_engine_fails_open_and_the_runtime_read_propagates(
            self, tmp_path, monkeypatch):
        from wisp.core.stagnation import StagnationDetector

        monkeypatch.setattr(
            StagnationDetector, "may_report_goal_met", _raise_predicate)

        runtime, session, _repo, _p = _build(
            tmp_path, [_content_only("done")], stagnation_gate=True)
        seen: list[dict] = []

        async def _main():
            async for ev in runtime.run_turn(session, prompt="go"):
                seen.append(ev)

        with pytest.raises(RuntimeError, match="predicate exploded"):
            asyncio.run(_main())

        assert [e for e in seen if e.get("type") == "done"], (
            "the engine did not fail open — a raising predicate withheld `done`")

    def test_the_engine_seam_alone_fails_open_with_no_runtime(self):
        """The engine's half of the same fact, with no runtime involved."""
        _session, events = _drive_core(
            [_content_only("done")], completion_gate=_raise_predicate)
        assert [e for e in events if e.get("type") == "done"]
        assert _replans(events) == []


# ══════════════════════════════════════════════════════════════════════════
# 7. The controlled enablement experiment (isolated config only)
# ══════════════════════════════════════════════════════════════════════════


class TestTheControlledEnablementExperiment:
    """§17 — `stagnation_gate=True` exercised in an **isolated** config.

    Every case overrides the flag on a `tmp_path` config. The repository default
    is never touched; the final test asserts that.
    """

    def test_case_1_no_stagnation(self, tmp_path):
        session, repo, events, _p = _run_turn(
            tmp_path, [_content_only("done")], stagnation_gate=True)
        assert _replans(events) == []
        assert _goal_records(repo)[0]["goal_state"] != "goal_stagnated"

    def test_case_2_one_flat_observation_closes_and_intervenes(self, tmp_path):
        session, repo, events, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=True)
        assert len(_replans(events)) == 2
        assert _goal_records(repo)[0]["goal_state"] == "goal_stagnated"

    def test_case_3_persistent_flat_state(self, tmp_path):
        session, repo, events, _p = _run_turn(
            tmp_path, _repeat("a.txt", 6) + [_content_only("done")],
            files={"a.txt": "hello"}, stagnation_gate=True)
        assert len(_replans(events)) == 2, "a persistent flat run overspent"
        assert [e for e in events if e.get("type") == "done"]

    def test_case_4_progress_after_the_trap(self, tmp_path):
        """Progress after the trap does not reopen the predicate (ADR-0037), so
        the intervention still happens and the outcome is still `GOAL_STAGNATED`."""
        rounds = [_tool_round([_read_call("a.txt", "c0")]),
                  _tool_round([_read_call("a.txt", "c1")]),
                  _tool_round([_read_call("b.txt", "c2")]),
                  _content_only("done")]
        session, repo, events, _p = _run_turn(
            tmp_path, rounds, files={"a.txt": "x", "b.txt": "y"},
            stagnation_gate=True)
        assert len(_replans(events)) == 2
        record = _goal_records(repo)[0]
        assert record["stagnation_allows_goal_met"] is False
        assert record["goal_state"] == "goal_stagnated"

    def test_case_5_the_final_iteration(self):
        _session, events = _drive_core(
            [_content_only("done")], max_iterations=1,
            completion_gate=_always_closed)
        assert _replans(events) == []
        assert [e for e in events if e.get("type") == "done"]

    def test_case_6_a_verification_rejection(self, tmp_path, monkeypatch):
        TestVerificationAuthorityOnTheSamePass._install_rejecting_guard(
            monkeypatch, rejections=1)
        _session, events = _drive_core(
            [_content_only("done")], max_iterations=30,
            completion_gate=_always_closed)
        nudges = [m for m in (_message_of(e) for e in events) if m]
        assert nudges and not nudges[0].startswith("[SYSTEM] Stagnation loop")
        assert len(_replans(events)) == 2

    def test_case_7_a_terminal_error(self, tmp_path):
        session, repo, events, _p = _run_turn(
            tmp_path, _repeat("a.txt", 2) + [_boom()],
            files={"a.txt": "hello"}, stagnation_gate=True)
        assert _replans(events) == [], (
            "the gate intervened on a turn that was going to fail fatally")
        record = _goal_records(repo)[0]
        assert record["terminal_outcome"] == "failed"
        assert record["goal_state"] == "goal_failed"

    def test_case_8_a_predicate_exception(self):
        _session, events = _drive_core(
            [_content_only("done")], completion_gate=_raise_predicate)
        assert _replans(events) == []
        assert [e for e in events if e.get("type") == "done"]


class TestTheShippedDefaultIsUnchanged:
    """The phase's own exit criterion: `DEFAULT_ENABLEMENT = NOT_CHANGED`."""

    def test_the_declared_default_is_off(self):
        """Read from the config module's own schema, so ambient environment
        cannot make this pass."""
        from wisp.config import SETTINGS_SCHEMA

        assert SETTINGS_SCHEMA["stagnation_gate"]["default"] is False, (
            "the shipped default for `stagnation_gate` changed")

    def test_a_config_built_with_no_overrides_is_off(self):
        from wisp.config import WispConfig

        cfg = WispConfig()
        assert cfg.stagnation_gate is False
        assert cfg.goal_state is False
        assert cfg.recovery_ladder is False
        assert cfg.record_verdict is False
        assert cfg.task_graph is False
        assert cfg.graph_oscillation_guard is True, (
            "the detector's own switch changed default")
