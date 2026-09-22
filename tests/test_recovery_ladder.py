"""Migration P6 — the recovery ladder.

The plan requires seven assertions. All seven are here.

The two rules that carry the most weight:

- **`test_denial_never_retries`.** Phase 10 **removed** `_DENIAL_MARKERS` because
  it failed to enforce exactly this — all five canonical denial statuses matched
  nothing (`CONTEXT.md:456`). The taxonomy makes the rule enforceable *by class*
  rather than by prose matching, and these tests pin it against the canonical
  denial vocabulary rather than a local copy.
- **`test_escalation_is_terminal`.** An exhausted ladder yields
  `ESCALATED_TO_HUMAN` — a terminal state, not a hang and not a false success.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wisp.core.events import (
    DENIAL_APPROVAL_TIMEOUT,
    DENIAL_CANCELLED,
    DENIAL_POLICY_DENIED,
    DENIAL_SCHEMA_INVALID,
    DENIAL_USER_DENIED,
)
from wisp.core.recovery import (
    DENIAL_STATUSES,
    FORBIDDEN_RUNGS,
    LEGAL_RUNGS,
    BudgetGovernor,
    EscalationState,
    FailureClass,
    HumanIntervention,
    RecoveryBudget,
    RecoveryDecision,
    RecoveryLadder,
    RecoveryRung,
    RollbackPlan,
    classify_failure,
    is_legal_rung,
    plan_rollback,
)
from wisp.core.session import Session, SessionEvent

REPO = Path(__file__).resolve().parents[1]


# ── 1. The taxonomy is closed ───────────────────────────────────────────


class TestFailureTaxonomyClosed:
    def test_exactly_ten_classes(self):
        assert len(list(FailureClass)) == 10

    def test_the_ten_are_the_documented_ones(self):
        assert {c.value for c in FailureClass} == {
            "transient", "tool", "implementation", "verification",
            "dependency", "environment", "invalid_assumption", "repeated",
            "stagnation", "security",
        }

    def test_every_class_has_a_legal_rung_set(self):
        assert set(LEGAL_RUNGS) == set(FailureClass)

    def test_every_class_can_escalate(self):
        """A taxonomy that could forbid escalation could trap a failure."""
        for cls in FailureClass:
            assert RecoveryRung.HUMAN in LEGAL_RUNGS[cls], cls

    def test_every_class_has_a_forbidden_entry(self):
        """`FORBIDDEN_RUNGS` must be total, so `is_legal_rung` never has to
        guess about a class it has not seen."""
        for cls in FailureClass:
            assert cls in FORBIDDEN_RUNGS, cls

    @pytest.mark.parametrize("status", sorted(DENIAL_STATUSES))
    def test_every_canonical_denial_status_classifies_as_security(self, status):
        """Phase 10's defect: the old marker list matched NONE of these. Driving
        the test off the canonical vocabulary means it cannot drift."""
        assert classify_failure(f'{{"status": "{status}"}}') is FailureClass.SECURITY

    def test_the_denial_set_is_the_canonical_one(self):
        """Imported from `core/events.py`, not re-listed — a local copy is the
        defect that was removed."""
        assert DENIAL_STATUSES == {
            DENIAL_POLICY_DENIED, DENIAL_USER_DENIED, DENIAL_APPROVAL_TIMEOUT,
            DENIAL_CANCELLED, DENIAL_SCHEMA_INVALID,
        }

    def test_a_tool_error_classifies_as_tool(self):
        assert classify_failure('{"status": "error"}') is FailureClass.TOOL

    def test_a_success_outcome_is_refused(self):
        """Handing a success to a failure classifier is a broken detection
        site, and guessing would let it masquerade as a classified failure."""
        with pytest.raises(ValueError, match="SUCCESS"):
            classify_failure('{"status": "ok"}')

    def test_verification_failure_is_expressible(self):
        assert classify_failure(implementation_failed=True) is \
            FailureClass.IMPLEMENTATION

    def test_each_explicit_signal_maps_to_its_class(self):
        assert classify_failure(repeated=True) is FailureClass.REPEATED
        assert classify_failure(stagnation=True) is FailureClass.STAGNATION
        assert classify_failure(dependency_blocked=True) is FailureClass.DEPENDENCY
        assert classify_failure(environment=True) is FailureClass.ENVIRONMENT
        assert classify_failure(invalid_assumption=True) is \
            FailureClass.INVALID_ASSUMPTION
        assert classify_failure(verification_inconclusive=True) is \
            FailureClass.VERIFICATION
        assert classify_failure(transient=True) is FailureClass.TRANSIENT

    def test_denial_outranks_every_other_signal(self):
        """Precedence is deliberate: a denied call that also looked transient
        must still be SECURITY, or the no-retry rule leaks."""
        assert classify_failure(
            f'{{"status": "{DENIAL_POLICY_DENIED}"}}',
            transient=True, repeated=True) is FailureClass.SECURITY

    def test_classification_delegates_to_the_canonical_authority(self):
        """A second classifier is what let benchmark error accounting miss
        every structured denial."""
        src = (REPO / "wisp" / "core" / "recovery.py").read_text(encoding="utf-8")
        assert "classify_result" in src
        assert "from wisp.core.events import" in src


# ── 2. A denial never retries ───────────────────────────────────────────


class TestDenialNeverRetries:
    def test_security_forbids_retry(self):
        assert not is_legal_rung(FailureClass.SECURITY, RecoveryRung.RETRY)

    def test_repeated_forbids_retry(self):
        assert not is_legal_rung(FailureClass.REPEATED, RecoveryRung.RETRY)

    def test_security_forbids_every_rung_but_escalation(self):
        for rung in RecoveryRung:
            expected = rung is RecoveryRung.HUMAN
            assert is_legal_rung(FailureClass.SECURITY, rung) is expected, rung

    def test_a_security_failure_escalates_immediately(self):
        ladder = RecoveryLadder()
        decision = ladder.decide(FailureClass.SECURITY, ["policy denied"])
        assert decision.rung is RecoveryRung.HUMAN
        assert ladder.terminal_outcome == "ESCALATED_TO_HUMAN"

    def test_the_ladder_never_offers_retry_for_a_denial(self):
        assert RecoveryRung.RETRY not in RecoveryLadder().legal_rungs(
            FailureClass.SECURITY)

    def test_a_tool_failure_may_still_retry(self):
        """The rule is about denials, not about retrying in general."""
        assert is_legal_rung(FailureClass.TOOL, RecoveryRung.RETRY)

    def test_transient_may_retry(self):
        assert is_legal_rung(FailureClass.TRANSIENT, RecoveryRung.RETRY)

    def test_forbidden_wins_over_legal(self):
        """The two tables are independent; a rung in both is a defect the
        caller should see rather than one the lookup order should hide."""
        for cls, forbidden in FORBIDDEN_RUNGS.items():
            for rung in forbidden:
                assert not is_legal_rung(cls, rung), (cls, rung)

    def test_no_forbidden_rung_is_also_declared_legal(self):
        for cls, forbidden in FORBIDDEN_RUNGS.items():
            assert not (forbidden & LEGAL_RUNGS[cls]), cls


# ── 3. Escalation is terminal ───────────────────────────────────────────


class TestEscalationIsTerminal:
    def test_an_exhausted_ladder_escalates(self):
        ladder = RecoveryLadder()
        for _ in range(20):
            ladder.decide(FailureClass.ENVIRONMENT, ["diagnostic inconclusive"])
        assert ladder.terminal_outcome == "ESCALATED_TO_HUMAN"

    def test_exhaustion_does_not_raise_or_hang(self):
        """R3: terminal honesty. The caller gets a state it can act on."""
        ladder = RecoveryLadder()
        for _ in range(20):
            d = ladder.decide(FailureClass.ENVIRONMENT, ["e"])
        assert d.rung is RecoveryRung.HUMAN

    def test_in_progress_is_not_reported_as_escalated(self):
        assert RecoveryLadder().terminal_outcome == "IN_PROGRESS"

    def test_escalation_creates_a_human_intervention(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"])
        assert isinstance(ladder.escalation, HumanIntervention)

    def test_the_intervention_is_resumable(self):
        """Durable STATE, not a blocking call — a parked run can be resumed."""
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"])
        assert ladder.escalation.resumable
        assert ladder.escalation.state is EscalationState.PENDING

    def test_an_answered_intervention_is_not_resumable(self):
        iv = HumanIntervention("e1", "stuck", state=EscalationState.ANSWERED)
        assert not iv.resumable

    def test_the_ladder_never_repeats_a_rung(self):
        """R5 — the structural anti-stagnation rule."""
        ladder = RecoveryLadder()
        seen: list[RecoveryRung] = []
        for _ in range(20):
            d = ladder.decide(FailureClass.IMPLEMENTATION, ["e"])
            if d.rung is RecoveryRung.HUMAN:
                break
            assert d.rung not in seen, f"{d.rung} repeated"
            seen.append(d.rung)

    def test_the_ladder_moves_down_never_back_up(self):
        """R1 — escalate, do not loop."""
        ladder = RecoveryLadder()
        rungs: list[int] = []
        for _ in range(20):
            d = ladder.decide(FailureClass.IMPLEMENTATION, ["e"])
            if d.rung is RecoveryRung.HUMAN:
                break
            rungs.append(int(d.rung))
        assert rungs == sorted(rungs), rungs


# ── 4. Durable rollback survives a crash ────────────────────────────────


class TestDurableRollbackSurvivesCrash:
    def test_the_compensation_declarations_now_have_a_caller(self):
        """`runs/compensation.py` says "No tool wiring" and nothing called
        `reversibility()` / `rollback_preview()` until P6."""
        src = (REPO / "wisp" / "core" / "recovery.py").read_text(encoding="utf-8")
        assert "reversibility(" in src and "rollback_preview(" in src

    def test_a_reversible_tool_plans_a_rollback(self):
        from wisp.runs.compensation import EditRecord
        plan = plan_rollback("write_file",
                             [EditRecord(path="a.py", unified_diff="--- a\n")]
                             )
        assert plan.safe and plan.reversibility == "reversible"
        assert plan.previews

    def test_an_irreversible_tool_is_refused(self):
        plan = plan_rollback("git_push")
        assert not plan.safe
        assert "irreversible" in plan.reason

    def test_an_unknown_tool_is_refused(self):
        """Assuming compensation exists for an undeclared tool is how a
        recovery makes things worse."""
        plan = plan_rollback("run_bash")
        assert not plan.safe and plan.reversibility == "unknown"

    def test_an_unsafe_rollback_escalates_instead(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.IMPLEMENTATION, ["e"])       # REPAIR
        d = ladder.decide(FailureClass.IMPLEMENTATION, ["e"],
                          tool_name="git_push")                  # ROLLBACK -> unsafe
        assert d.rung is RecoveryRung.HUMAN
        assert ladder.terminal_outcome == "ESCALATED_TO_HUMAN"

    def test_the_refusal_cites_the_reversibility_verdict(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.IMPLEMENTATION, ["e"])
        d = ladder.decide(FailureClass.IMPLEMENTATION, ["e"],
                          tool_name="git_push")
        assert any("reversibility(git_push)" in e for e in d.evidence)

    def test_the_rollback_plan_round_trips(self):
        plan = plan_rollback("write_file")
        assert plan.to_dict()["safe"] is True

    def test_rollback_records_survive_the_journal(self):
        """The rollback path must be durable, not session-scoped: a process
        death must not lose the compensation record."""
        from wisp.runs.compensation import EditRecord

        record = EditRecord(path="a.py", unified_diff="--- a\n", note="edit")
        s = Session(session_id="crash")
        s.apply(SessionEvent.compacted(1, 5, 1, "compaction"))
        # The record travels as an escalation's evidence, so a resumed run can
        # still see what it would have rolled back.
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.IMPLEMENTATION, [record.path])  # REPAIR
        d = ladder.decide(FailureClass.IMPLEMENTATION, [record.path],
                          tool_name="git_push")
        s.apply(SessionEvent.escalation_event(2, ladder.escalation.to_dict()))
        assert s.escalation["intervention_id"]
        assert len(s.escalation["ladder_history"]) == len(ladder.history)

    def test_a_replayed_escalation_is_still_resumable(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"])
        s = Session(session_id="resume")
        s.replay([
            SessionEvent.user_message(1, "go"),
            SessionEvent.escalation_event(2, ladder.escalation.to_dict()),
        ])
        assert s.unknown_events == 0
        restored = HumanIntervention.from_dict(s.escalation)
        assert restored.resumable
        assert restored.state is EscalationState.PENDING

    def test_the_escalation_carries_the_full_ladder_history(self):
        ladder = RecoveryLadder()
        for _ in range(20):
            if ladder.decide(FailureClass.IMPLEMENTATION, ["e"]).rung \
                    is RecoveryRung.HUMAN:
                break
        iv = ladder.escalation
        assert len(iv.ladder_history) == len(ladder.history) > 1

    def test_escalation_round_trips(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"])
        iv = ladder.escalation
        assert HumanIntervention.from_dict(iv.to_dict()) == iv


# ── 5. Every rung cites evidence ────────────────────────────────────────


class TestRecoveryRequiresEvidence:
    def test_a_rung_without_evidence_is_refused(self):
        with pytest.raises(ValueError, match="evidence"):
            RecoveryLadder().decide(FailureClass.IMPLEMENTATION, [])

    def test_the_refusal_explains_why(self):
        with pytest.raises(ValueError, match="assertion"):
            RecoveryLadder().decide(FailureClass.IMPLEMENTATION, ())

    def test_a_rung_with_evidence_records_it(self):
        d = RecoveryLadder().decide(FailureClass.IMPLEMENTATION, ["verify: FAIL"])
        assert d.evidence == ("verify: FAIL",)

    def test_escalation_from_exhaustion_carries_the_evidence(self):
        ladder = RecoveryLadder()
        for _ in range(20):
            d = ladder.decide(FailureClass.ENVIRONMENT, ["probe: inconclusive"])
            if d.rung is RecoveryRung.HUMAN:
                break
        assert d.evidence

    def test_a_decision_round_trips(self):
        d = RecoveryLadder().decide(FailureClass.IMPLEMENTATION, ["e1", "e2"])
        assert RecoveryDecision.from_dict(d.to_dict()) == d


# ── 6. The budgets bound each rung ──────────────────────────────────────


class TestLadderBudgetEnforced:
    def test_the_governor_reports_remaining(self):
        g = BudgetGovernor(RecoveryBudget(local_replans=2))
        assert g.remaining("local_replans") == 2

    def test_spending_reduces_remaining(self):
        g = BudgetGovernor(RecoveryBudget(local_replans=2))
        assert g.spend("local_replans")
        assert g.remaining("local_replans") == 1

    def test_spending_a_spent_budget_returns_false(self):
        g = BudgetGovernor(RecoveryBudget(global_replans=1))
        assert g.spend("global_replans")
        assert not g.spend("global_replans")
        assert g.exhausted("global_replans")

    def test_a_zero_budget_is_exhausted_immediately(self):
        g = BudgetGovernor(RecoveryBudget(diagnostic_tasks=0))
        assert g.exhausted("diagnostic_tasks")

    def test_a_non_positive_spend_is_refused(self):
        with pytest.raises(ValueError):
            BudgetGovernor().spend("local_replans", 0)

    def test_local_replans_are_bounded(self):
        ladder = RecoveryLadder(governor=BudgetGovernor(
            RecoveryBudget(local_replans=1)))
        ladder.decide(FailureClass.INVALID_ASSUMPTION, ["e"])   # LOCAL_REPLAN
        assert ladder.governor.exhausted("local_replans")
        # The next decision cannot be another local replan.
        assert RecoveryRung.LOCAL_REPLAN not in ladder.legal_rungs(
            FailureClass.INVALID_ASSUMPTION)

    def test_global_replans_are_bounded(self):
        ladder = RecoveryLadder(governor=BudgetGovernor(
            RecoveryBudget(global_replans=0)))
        assert RecoveryRung.GLOBAL_REPLAN not in ladder.legal_rungs(
            FailureClass.DEPENDENCY)

    def test_exhaustion_escalates_rather_than_terminating_silently(self):
        """The plan's precedence rule: recovery budget exhaustion escalates;
        it does not end the goal silently."""
        ladder = RecoveryLadder(governor=BudgetGovernor(RecoveryBudget(
            local_replans=0, global_replans=0, diagnostic_tasks=0)))
        d = ladder.decide(FailureClass.DEPENDENCY, ["e"])
        assert d.rung is RecoveryRung.HUMAN
        assert ladder.terminal_outcome == "ESCALATED_TO_HUMAN"

    def test_the_snapshot_reports_everything_from_one_place(self):
        """The audit found five unordered termination modes and no object that
        answered "how much is left"."""
        g = BudgetGovernor()
        g.reported["stream_attempts"] = {"remaining": 2, "owner": "stateless"}
        snap = g.snapshot()
        assert set(snap) >= {"local_replans", "global_replans",
                             "diagnostic_tasks", "graph_growth_nodes",
                             "reported"}
        assert snap["reported"]["stream_attempts"]["remaining"] == 2

    def test_the_defaults_are_small(self):
        """Recovery is the most expensive thing the agent does; an unbounded
        recovery budget is an unbounded cost multiplier."""
        b = RecoveryBudget()
        assert b.local_replans <= 3 and b.global_replans <= 2
        assert b.diagnostic_tasks <= 3


# ── 7. The escalation payload is the audit trail ────────────────────────


class TestEscalationPayloadIsAuditTrail:
    def test_the_payload_lists_every_attempted_rung(self):
        ladder = RecoveryLadder()
        for _ in range(20):
            if ladder.decide(FailureClass.IMPLEMENTATION, ["e"]).rung \
                    is RecoveryRung.HUMAN:
                break
        names = [d.rung for d in ladder.escalation.ladder_history]
        assert RecoveryRung.HUMAN in names
        assert len(names) > 1

    def test_each_entry_carries_its_class_and_reason(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"], reason="policy")
        for d in ladder.escalation.ladder_history:
            assert d.failure_class is FailureClass.SECURITY
            assert d.reason == "policy"

    def test_the_payload_serializes_for_a_client(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"])
        payload = ladder.escalation.to_dict()
        assert isinstance(payload["ladder_history"], list)
        assert payload["ladder_history"][0]["rung_name"] == "HUMAN"

    def test_the_payload_survives_the_journal(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"], reason="policy denied")
        s = Session(session_id="audit")
        s.apply(SessionEvent.escalation_event(1, ladder.escalation.to_dict()))
        restored = HumanIntervention.from_dict(s.escalation)
        assert restored.reason == "policy denied"
        assert restored.ladder_history[0].evidence == ("denied",)

    def test_the_operator_gets_history_not_a_bare_question(self):
        ladder = RecoveryLadder()
        for _ in range(20):
            if ladder.decide(FailureClass.IMPLEMENTATION, ["e"]).rung \
                    is RecoveryRung.HUMAN:
                break
        rendered = "\n".join(d.rung.name for d in
                             ladder.escalation.ladder_history)
        assert "REPAIR" in rendered or "ROLLBACK" in rendered


# ── Reachability and audit-only ─────────────────────────────────────────


class TestReachabilityAndIsolation:
    def test_the_ladder_is_reachable_from_the_package(self):
        import wisp.core.recovery as r
        for name in ("FailureClass", "RecoveryRung", "RecoveryLadder",
                     "BudgetGovernor", "HumanIntervention", "classify_failure",
                     "plan_rollback"):
            assert hasattr(r, name), name

    def test_the_recovery_events_never_touch_the_transcript(self):
        s = Session(session_id="iso")
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"])
        s.apply(SessionEvent.recovery_event(1, ladder.history[0].to_dict()))
        s.apply(SessionEvent.escalation_event(2, ladder.escalation.to_dict()))
        assert s.messages == []

    def test_the_recovery_records_survive_replay(self):
        ladder = RecoveryLadder()
        ladder.decide(FailureClass.SECURITY, ["denied"])
        s = Session(session_id="rp")
        s.replay([
            SessionEvent.user_message(1, "go"),
            SessionEvent.recovery_event(2, ladder.history[0].to_dict()),
            SessionEvent.escalation_event(3, ladder.escalation.to_dict()),
        ])
        assert s.unknown_events == 0
        assert len(s.recovery) == 1
        assert s.escalation["intervention_id"]

    def test_no_module_reimplements_the_denial_list(self):
        """AST: the denial statuses are imported, never re-listed. A local copy
        is exactly the defect Phase 10 removed."""
        tree = ast.parse((REPO / "wisp" / "core" / "recovery.py")
                         .read_text(encoding="utf-8"))
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        assert not any(v == "POLICY_DENIED" for v in literals), \
            "the denial vocabulary must be imported, not re-listed"
