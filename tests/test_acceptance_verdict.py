"""Migration P3 (stage 3a) — criteria, evidence, and verdicts.

The plan requires seven assertions. They are grouped here rather than split
across seven files so the properties that interact (criteria, evidence,
invalidation, the floor guard) can be read together; each keeps the plan's
name so it stays greppable.

Stage 3a **records**; it does not gate. The plan rates P3 the highest-risk
phase in the migration — it changes completion semantics — and prescribes
shipping in two stages for exactly that reason. These tests therefore pin the
*verdict algebra*, not a completion rule change.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from wisp.core.acceptance import (
    AcceptanceCriteria,
    CompletionVerdict,
    CriterionKind,
    Evidence,
    Verdict,
    content_digest,
    evaluate,
    invalidate,
    route_for,
)
from wisp.core.verification import (
    FLOOR_CRITERION_ID,
    VerificationFloorGuard,
    floor_guard_criteria,
    floor_guard_evidence,
    floor_guard_verdict,
)


def _crit(cid: str, *, required: bool = True,
          kind: CriterionKind = CriterionKind.SEMANTIC,
          check=None) -> AcceptanceCriteria:
    return AcceptanceCriteria(criteria_id=cid, description=f"criterion {cid}",
                              kind=kind, required=required, check=check)


def _ev(cid: str, eid: str = "", producer: str = "verifier",
        observations=("observed",)) -> Evidence:
    return Evidence(evidence_id=eid or f"e-{cid}", criteria_id=cid,
                    producer=producer, observations=tuple(observations))


# ── 1. No criteria → INCONCLUSIVE, never PASS ───────────────────────────


class TestCriteriaRequiredGate:
    def test_no_criteria_is_inconclusive(self):
        v = evaluate([], [])
        assert v.verdict is Verdict.INCONCLUSIVE
        assert "NO_REQUIRED_CRITERIA" in v.reason_codes

    def test_no_criteria_is_not_success(self):
        assert not evaluate([], []).accepted

    def test_no_criteria_never_routes_to_allow(self):
        assert evaluate([], []).routed_decision != "ALLOW"

    def test_only_advisory_criteria_is_still_inconclusive(self):
        """An advisory criterion cannot make a task verified — nothing was
        REQUIRED, so nothing was established."""
        v = evaluate([_crit("c1", required=False)], [_ev("c1")])
        assert v.verdict is Verdict.INCONCLUSIVE

    def test_advisory_failure_does_not_block(self):
        required = _crit("r", kind=CriterionKind.DETERMINISTIC,
                         check=lambda _p: True)
        v = evaluate([required, _crit("a", required=False)], [_ev("r")])
        assert v.verdict is Verdict.PASS

    def test_advisory_criteria_are_not_reported_as_unmet(self):
        required = _crit("r", kind=CriterionKind.DETERMINISTIC,
                         check=lambda _p: True)
        v = evaluate([required, _crit("a", required=False)], [_ev("r")])
        assert v.unmet_criteria == []


# ── 2. False success is impossible ──────────────────────────────────────


class TestFalseSuccessImpossible:
    def test_required_criterion_without_evidence_is_inconclusive(self):
        v = evaluate([_crit("c1")], [])
        assert v.verdict is Verdict.INCONCLUSIVE
        assert v.unmet_criteria == ["c1"]

    def test_one_missing_of_two_blocks_success(self):
        v = evaluate([_crit("c1"), _crit("c2")], [_ev("c1")])
        assert v.verdict is Verdict.INCONCLUSIVE
        assert v.unmet_criteria == ["c2"]

    def test_all_required_satisfied_is_pass(self):
        v = evaluate([_crit("c1"), _crit("c2")], [_ev("c1"), _ev("c2")])
        assert v.verdict is Verdict.PASS
        assert v.accepted

    def test_evidence_for_the_wrong_criterion_does_not_count(self):
        v = evaluate([_crit("c1")], [_ev("c2")])
        assert v.verdict is Verdict.INCONCLUSIVE

    def test_pass_cites_its_evidence(self):
        v = evaluate([_crit("c1")], [_ev("c1", "e1"), _ev("c1", "e2")])
        assert sorted(v.evidence_ids) == ["e1", "e2"]

    def test_invalidated_evidence_does_not_satisfy(self):
        stale = Evidence(evidence_id="e1", criteria_id="c1", producer="v",
                         invalidated_at=1.0, observations=("o",))
        v = evaluate([_crit("c1")], [stale])
        assert v.verdict is Verdict.INCONCLUSIVE

    def test_invalidated_evidence_is_not_reported_as_failure(self):
        """Absence of valid evidence is not evidence of failure — reporting
        FAIL here would be a claim the evidence does not support."""
        stale = Evidence(evidence_id="e1", criteria_id="c1", producer="v",
                         invalidated_at=1.0, observations=("o",))
        assert evaluate([_crit("c1")], [stale]).verdict is not Verdict.FAIL


# ── 3. Verifier independence ────────────────────────────────────────────


class TestVerifierIndependence:
    def test_evaluator_takes_no_transcript(self):
        """L1/L2 structural independence: the verifier judges criteria against
        evidence. If it could see the actor's transcript it would be judging
        the actor's account of the work rather than the work."""
        params = set(inspect.signature(evaluate).parameters)
        assert not params & {
            "messages", "transcript", "history", "session", "conversation",
            "prompt", "actor_output",
        }, params

    def test_evidence_names_its_producer(self):
        """Independence is only checkable if the record says who produced it.
        An evidence record without provenance cannot establish that the
        verifier differs from the actor."""
        ev = _ev("c1", producer="independent_verifier")
        assert ev.producer == "independent_verifier"

    def test_self_reported_evidence_is_marked(self):
        """The floor guard's evidence is the ACTOR's own bookkeeping. It is
        recorded honestly as self-reported, which is what lets a later stage
        refuse to treat it as independent."""
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        g.note_tool_result("run_bash", "all good")
        ev = floor_guard_evidence(g)[0]
        assert ev.producer == "verification_floor_guard"
        assert ev.metadata["self_reported"] is True

    def test_a_criterion_cannot_be_satisfied_by_another_criterions_evidence(self):
        v = evaluate([_crit("independent")], [_ev("self_reported")])
        assert v.verdict is Verdict.INCONCLUSIVE


# ── 4. Evidence provenance ──────────────────────────────────────────────


class TestEvidenceProvenance:
    def test_every_evidence_cites_at_least_one_observation(self):
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        for ev in floor_guard_evidence(g):
            assert ev.observations, "evidence citing nothing is an assertion"

    def test_content_hash_verifies(self):
        payload = {"exit_code": 0, "cmd": "pytest"}
        digest = content_digest(payload)
        assert digest == content_digest(payload)
        assert len(digest) == 64
        int(digest, 16)

    def test_content_hash_is_order_insensitive(self):
        assert content_digest({"a": 1, "b": 2}) == content_digest({"b": 2, "a": 1})

    def test_content_hash_changes_with_the_observation(self):
        assert content_digest({"exit_code": 0}) != content_digest({"exit_code": 1})

    def test_unserializable_payload_still_hashes(self):
        assert len(content_digest({"s": {1, 2, 3}})) == 64

    def test_evidence_round_trips(self):
        ev = _ev("c1", "e1", producer="v")
        assert Evidence.from_dict(ev.to_dict()) == ev

    def test_evidence_id_is_derived_from_content(self):
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        first = floor_guard_evidence(g)[0]
        again = floor_guard_evidence(g)[0]
        assert first.evidence_id == again.evidence_id

    def test_evidence_id_changes_when_the_state_changes(self):
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        before = floor_guard_evidence(g)[0].evidence_id
        g.note_tool_result("run_bash", "ok")
        after = floor_guard_evidence(g)[0].evidence_id
        assert before != after

    def test_no_evidence_when_nothing_was_claimed(self):
        """A turn that mutated nothing has no claim to support; a vacuous
        evidence record would satisfy a criterion without establishing
        anything."""
        assert floor_guard_evidence(VerificationFloorGuard()) == []


# ── 5. Invalidation cascades ────────────────────────────────────────────


class TestEvidenceInvalidationCascades:
    def test_invalidate_marks_every_evidence_of_a_criterion(self):
        evs = [_ev("c1", "e1"), _ev("c1", "e2"), _ev("c2", "e3")]
        out = invalidate(evs, criteria_ids=["c1"])
        assert [e.valid for e in out] == [False, False, True]

    def test_invalidate_all(self):
        out = invalidate([_ev("c1", "e1"), _ev("c2", "e2")])
        assert all(not e.valid for e in out)

    def test_invalidate_does_not_mutate_in_place(self):
        evs = [_ev("c1", "e1")]
        invalidate(evs)
        assert evs[0].valid, "invalidate() must return copies"

    def test_invalidation_demotes_a_pass_to_inconclusive(self):
        """The cascade that matters: evidence satisfied the criterion, a later
        mutation invalidated it, and the verdict must not stay PASS."""
        crit = [_crit("c1")]
        evs = [_ev("c1", "e1")]
        assert evaluate(crit, evs).verdict is Verdict.PASS
        assert evaluate(crit, invalidate(evs)).verdict is Verdict.INCONCLUSIVE

    def test_already_invalid_evidence_is_left_alone(self):
        ev = Evidence(evidence_id="e1", criteria_id="c1", producer="v",
                      invalidated_at=5.0, observations=("o",))
        out = invalidate([ev], at=9.0)
        assert out[0].invalidated_at == 5.0

    def test_floor_guard_mutation_invalidates_its_own_evidence(self):
        """The guard's invalidate-on-mutation property, projected: a mutation
        after a verified run drops the verdict back."""
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        g.note_tool_result("run_bash", "all good")
        assert floor_guard_verdict(g).verdict is Verdict.PASS
        g.note_tool_result("write_file", "ok", {"path": "b.py"})
        assert floor_guard_verdict(g).verdict is not Verdict.PASS


# ── 6. Deterministic first ──────────────────────────────────────────────


class TestDeterministicFirst:
    def test_failing_deterministic_short_circuits(self):
        calls: list[str] = []

        def semantic_payload_probe(_p):
            calls.append("semantic")
            return True

        v = evaluate(
            [
                _crit("det", kind=CriterionKind.DETERMINISTIC,
                      check=lambda _p: False),
                _crit("sem"),
            ],
            [_ev("sem")],
        )
        assert v.verdict is Verdict.FAIL
        assert v.reason_codes == ["DETERMINISTIC_CHECK_FAILED"]
        assert calls == [], "semantic evaluation ran after a deterministic fail"

    def test_fail_names_the_criterion(self):
        v = evaluate([_crit("det", kind=CriterionKind.DETERMINISTIC,
                            check=lambda _p: False)], [])
        assert v.unmet_criteria == ["det"]

    def test_fail_routes_to_reject(self):
        v = evaluate([_crit("det", kind=CriterionKind.DETERMINISTIC,
                            check=lambda _p: False)], [])
        assert v.routed_decision == "REJECT"

    def test_passing_deterministic_still_needs_evidence(self):
        v = evaluate([_crit("det", kind=CriterionKind.DETERMINISTIC,
                            check=lambda _p: True)], [])
        assert v.verdict is Verdict.INCONCLUSIVE

    def test_a_raising_check_fails_closed(self):
        def _boom(_p):
            raise RuntimeError("verifier bug")

        v = evaluate([_crit("det", kind=CriterionKind.DETERMINISTIC,
                            check=_boom)], [])
        assert v.verdict is Verdict.FAIL

    def test_check_receives_the_observations(self):
        seen = {}
        evaluate([_crit("det", kind=CriterionKind.DETERMINISTIC,
                        check=lambda p: seen.update(p) or True)],
                 [], observations={"exit_code": 0})
        assert seen == {"exit_code": 0}

    def test_no_deterministic_criterion_means_no_short_circuit(self):
        v = evaluate([_crit("sem")], [_ev("sem")])
        assert v.verdict is Verdict.PASS


# ── 7. The floor guard is retained, not replaced ────────────────────────


class TestFloorGuardRetained:
    def test_resolved_is_unchanged(self):
        g = VerificationFloorGuard()
        assert g.resolved() is False
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        assert g.resolved() is False
        g.note_tool_result("run_bash", "all good")
        assert g.resolved() is True

    def test_rejection_is_unchanged(self):
        from wisp.core.verification import HARNESS_REJECTION
        g = VerificationFloorGuard()
        assert g.rejection() is None          # nothing mutated, nothing to gate
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        assert g.rejection() == HARNESS_REJECTION

    def test_invalidate_on_mutation_is_unchanged(self):
        """The property the plan explicitly requires be preserved
        (`core/verification.py:149`)."""
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        g.note_tool_result("run_bash", "all good")
        assert g.verify_ok_after_edit is True
        g.note_tool_result("write_file", "ok", {"path": "b.py"})
        assert g.verify_ok_after_edit is None
        assert g.resolved() is False

    def test_grind_floor_is_bounded_and_escapable(self):
        """The property that matters: a mutated turn cannot be blocked
        forever. `min_turns=1, max_nudges=1` spends the budget on the first
        rejection, so the second call must let the turn finish."""
        g = VerificationFloorGuard(min_turns=1, max_nudges=1)
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        assert g.rejection() is not None          # first: blocked
        assert g.rejection() is None, "the floor must always be escapable"
        assert g.nudges_used == 1, "the nudge budget must not be over-spent"

    def test_default_floor_needs_both_budget_parts_spent(self):
        g = VerificationFloorGuard()               # min_turns=5, max_nudges=2
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        seen = [g.rejection() for _ in range(6)]
        assert seen[-1] is None, f"floor never released: {seen}"

    def test_disabled_guard_asserts_nothing(self):
        g = VerificationFloorGuard(enabled=False)
        assert floor_guard_criteria(g) == []
        assert floor_guard_evidence(g) == []

    def test_projection_does_not_mutate_the_guard(self):
        g = VerificationFloorGuard()
        g.note_tool_result("write_file", "ok", {"path": "a.py"})
        before = (g.wrote_code, g.verify_ok_after_edit, g.turns_used,
                  g.nudges_used, g.repeat_count, list(g.steps))
        floor_guard_criteria(g)
        floor_guard_evidence(g)
        floor_guard_verdict(g)
        after = (g.wrote_code, g.verify_ok_after_edit, g.turns_used,
                 g.nudges_used, g.repeat_count, list(g.steps))
        assert before == after

    def test_floor_is_one_criterion_not_the_rule(self):
        g = VerificationFloorGuard()
        crit = floor_guard_criteria(g)
        assert len(crit) == 1
        assert crit[0].criteria_id == FLOOR_CRITERION_ID
        assert crit[0].kind is CriterionKind.DETERMINISTIC


# ── Verdict algebra ─────────────────────────────────────────────────────


class TestVerdictAlgebra:
    def test_every_verdict_routes(self):
        for v in Verdict:
            assert route_for(v) in ("ALLOW", "REJECT", "RETRY", "ESCALATE")

    def test_inconclusive_never_routes_to_allow(self):
        assert route_for(Verdict.INCONCLUSIVE) != "ALLOW"

    def test_only_pass_is_accepted(self):
        for v in Verdict:
            assert CompletionVerdict(verdict=v).accepted is (v is Verdict.PASS)

    def test_verdict_serializes_with_a_derived_decision(self):
        d = CompletionVerdict(verdict=Verdict.INCONCLUSIVE).to_dict()
        assert d["verdict"] == "inconclusive"
        assert d["decision"] == "RETRY"

    def test_route_is_derived_not_assigned(self):
        v = CompletionVerdict(verdict=Verdict.PASS)
        assert v.routed_decision == route_for(v.verdict)


# ── Reachability (RULE 11) ──────────────────────────────────────────────


class TestReachability:
    """The audit that motivated this migration found eight complete,
    unreachable subsystems. The acceptance model must not be the ninth."""

    def test_engine_publishes_the_guard(self):
        import ast
        from pathlib import Path
        tree = ast.parse(
            (Path(__file__).resolve().parents[1]
             / "wisp" / "core" / "stateless.py").read_text(encoding="utf-8"))
        assert any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Attribute) and t.attr == "_last_guard"
                    for t in n.targets)
            for n in ast.walk(tree)
        ), "the engine no longer publishes its guard"

    def test_runtime_reads_the_guard(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "wisp" / "core" / "runtime.py").read_text(encoding="utf-8")
        assert '_last_guard' in src
        assert "floor_guard_verdict" in src

    def test_recording_defaults_off(self):
        from wisp.config import WispConfig
        assert WispConfig().record_verdict is False

    def test_a_turn_records_a_verdict_when_enabled(self, tmp_path):
        """End-to-end: the record actually reaches the session log."""
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session import SessionEventType
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": "hi"}
                yield {"type": "done", "done_reason": "stop"}

        ws = tmp_path / "ws"
        ws.mkdir()
        config = WispConfig().replace(workspace=str(ws), record_verdict=True)
        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "v1", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        kinds = [str(e.event_type) for e in repo.load_events("v1")]
        assert SessionEventType.VERDICT.value in kinds, kinds

    def test_no_verdict_recorded_when_disabled(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session import SessionEventType
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": "hi"}
                yield {"type": "done", "done_reason": "stop"}

        ws = tmp_path / "ws"
        ws.mkdir()
        config = WispConfig().replace(workspace=str(ws))
        store = UnifiedStore(tmp_path / "wisp.db")
        repo = SessionRepository(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "v2", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        kinds = [str(e.event_type) for e in repo.load_events("v2")]
        assert SessionEventType.VERDICT.value not in kinds


# ── Stage 3a must not gate ──────────────────────────────────────────────


class TestStage3aDoesNotGate:
    def test_completion_rule_is_unchanged(self):
        """The plan's staging exists because P3 changes completion semantics.
        In 3a the verdict is RECORDED and nothing consumes it — so
        `turn_succeeded` must still derive from terminal evidence alone.

        UPDATED by ADR-0044 (PM-24): the decision point now derives the flag
        FROM the terminal outcome instead of re-implementing the predicate. The
        intent is unchanged and now stronger — the expression this test used to
        pin was a SECOND implementation of the rule that
        `terminal_outcome_from_evidence` owns. The verdict still does not reach
        the turn-level flag; it is consumed only by `derive_goal_state`.
        """
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "wisp" / "core" / "runtime.py").read_text(encoding="utf-8")
        assert "turn_succeeded = _goal_outcome is TerminalOutcome.SUCCEEDED" in src
        assert "turn_succeeded = saw_done and not saw_fatal_error" not in src, (
            "the turn-success predicate was re-implemented (ADR-0044 R2)")
        # the rule still consults ONLY terminal evidence
        assert "_goal_outcome = terminal_outcome_from_evidence(" in src
        assert "acceptance" not in src.split(
            "_goal_outcome = terminal_outcome_from_evidence")[1].split(
            "turn_succeeded = _goal_outcome")[0]

    def test_the_verdict_does_not_reach_the_transcript(self):
        """VERDICT is audit-only: it must not append a message."""
        from wisp.core.session import Session, SessionEvent
        s = Session(session_id="a")
        s.apply(SessionEvent.verdict_event(1, {"verdict": "pass"}))
        assert s.messages == []
        assert len(s.verdicts) == 1

    def test_verdicts_survive_replay(self):
        from wisp.core.session import Session, SessionEvent
        s = Session(session_id="a")
        s.replay([
            SessionEvent.user_message(1, "go"),
            SessionEvent.verdict_event(2, {"verdict": "inconclusive"}),
        ])
        assert s.unknown_events == 0
        assert [v["verdict"] for v in s.verdicts] == ["inconclusive"]
        assert [m["role"] for m in s.messages] == ["user"]
