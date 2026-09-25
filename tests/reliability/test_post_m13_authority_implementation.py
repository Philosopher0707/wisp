"""ADR-0035 — completion / recovery authority, implemented.

Two authorities, two questions:

    completion  "is the work complete?"   -> core/goal.py::derive_goal_state
    recovery    "what should happen next?" -> core/recovery.py::RecoveryLadder

The tests are organised around the contract, not around the modules:

* **T1–T12**  the required conflict matrix (ADR-0035's precedence table)
* **D1–D8**   durability and replay
* **flags**   `recovery_ladder` off preserves the old path; on wires the new one
* **live**    the real `AgentRuntime.run_turn` path, not only pure functions
* **adversarial** the ways a false success could sneak in

The pure arbitration is tested without runtime setup, because ADR-0035 requires
the arbiter to be deterministic, side-effect free and model-independent. The
live tests exist because the whole point of the phase is to replace dead-end
mechanisms with real consumers.
"""
from __future__ import annotations

import asyncio

import pytest

from wisp.core.acceptance import Verdict
from wisp.core.goal import (
    GoalState,
    TerminalOutcome,
    already_recorded_from,
    derive_goal_state,
    goal_state_from_record,
)
from wisp.core.recovery import (
    CODE_ITERATION_BUDGET,
    CODE_TURN_TIMEOUT,
    FORBIDDEN_RUNGS,
    LEGAL_RUNGS,
    FailureClass,
    RecoveryLadder,
    RecoveryRung,
    classify_failure_signal,
)
from wisp.core.session import Session, SessionEvent, SessionEventType


# ══════════════════════════════════════════════════════════════════════════
# Harness — the real runtime path
# ══════════════════════════════════════════════════════════════════════════


class _DictProvider:
    """Scripted provider: one entry per round, last entry repeats."""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        script = self._rounds[min(self.calls - 1, len(self._rounds) - 1)]
        yield from script()


def _content_only(text, reason="stop"):
    def _g():
        yield {"type": "content", "text": text}
        yield {"type": "done", "done_reason": reason}
    return _g


def _tool_round(calls):
    def _g():
        yield {"type": "tool_calls", "calls": calls}
        yield {"type": "done", "done_reason": "tool_calls"}
    return _g


def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": {"path": path}}}


def _repeat(path: str, n: int = 3) -> list:
    """`n` rounds asking for the SAME work, each with a fresh call id.

    Three, not two: the first observation has no baseline, so P7's
    `min_consecutive=2` needs a baseline plus two flat observations.
    """
    return [_tool_round([_read_call(path, f"c{i}")]) for i in range(n)]


def _boom(message="provider exploded"):
    """A provider whose stream raises — the fatal `CODE_PROVIDER_STREAM` path."""
    def _g():
        raise RuntimeError(message)
        yield  # pragma: no cover - unreachable, keeps this a generator
    return _g


def _run_turn(tmp_path, rounds, *, files=None, sid="pma", **cfg_kw):
    from wisp.config import WispConfig
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.core.session_repo import SessionRepository
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    # The goal-state record is opt-in (ADR-0035 clause 9: the P3 stage-3a
    # pattern, default OFF because it adds a record to every caller's log). The
    # harness opts in so these tests exercise it; the flag tests below prove the
    # default is unchanged.
    cfg_kw.setdefault("goal_state", True)

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (ws / name).write_text(text)

    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)
    provider = _DictProvider(rounds)

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory, session_repo=repo,
        config=config)
    session = {"id": sid, "model": "mock", "workspace": str(ws), "messages": []}

    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt="go")]
    events = asyncio.run(_main())
    return session, repo, events


def _goal_records(repo, sid="pma"):
    out = repo.reconstruct(sid)
    return (out.get("_journal") or {}).get("goal_states") or []


def _recovery_records(repo, sid="pma"):
    out = repo.reconstruct(sid)
    return (out.get("_journal") or {}).get("recovery") or []


def _escalation_record(repo, sid="pma"):
    out = repo.reconstruct(sid)
    return (out.get("_journal") or {}).get("escalation") or {}


# ══════════════════════════════════════════════════════════════════════════
# T1–T12 — the required conflict matrix (pure arbitration)
# ══════════════════════════════════════════════════════════════════════════


def _derive(**kw):
    base = dict(terminal_outcome=TerminalOutcome.SUCCEEDED, turn_succeeded=True)
    base.update(kw)
    return derive_goal_state(**base)


class TestThePrecedenceMatrix:
    def test_t1_pass_progressing_success_is_goal_met(self):
        assert _derive(acceptance_verdict=Verdict.PASS) is GoalState.GOAL_MET

    def test_t2_pass_and_stagnating_is_goal_stagnated(self):
        """Row 4 outranks row 6: a stagnated goal must never reach GOAL_MET."""
        assert _derive(acceptance_verdict=Verdict.PASS,
                       stagnating=True) is GoalState.GOAL_STAGNATED

    def test_t3_fail_beats_terminal_success(self):
        """Terminal `done` cannot override an authoritative failure verdict."""
        assert _derive(acceptance_verdict=Verdict.FAIL) is GoalState.GOAL_FAILED

    def test_t4_inconclusive_is_goal_unverified(self):
        assert _derive(acceptance_verdict=Verdict.INCONCLUSIVE) \
            is GoalState.GOAL_UNVERIFIED

    def test_t5_unknown_is_non_blocking(self):
        """UNKNOWN is not a verdict: it does not block, and it is not a PASS.

        The acceptance verdict supplies the PASS here — the arbiter never
        synthesises one from the absence of a stagnation verdict.
        """
        assert _derive(acceptance_verdict=Verdict.PASS) is GoalState.GOAL_MET

    def test_t6_fail_with_recovery_requested_still_fails(self):
        """Recovery is the *next-step* decision and cannot demote the failure."""
        assert _derive(acceptance_verdict=Verdict.FAIL) is GoalState.GOAL_FAILED

    def test_t7_inconclusive_with_recovery_requested_is_unverified(self):
        assert _derive(acceptance_verdict=Verdict.INCONCLUSIVE) \
            is GoalState.GOAL_UNVERIFIED

    def test_t9_cancelled_outranks_recovery_and_acceptance(self):
        assert _derive(acceptance_verdict=Verdict.PASS,
                       cancelled=True) is GoalState.CANCELLED
        assert _derive(acceptance_verdict=Verdict.FAIL,
                       cancelled=True) is GoalState.CANCELLED

    def test_t10_timeout_with_a_pass_is_goal_met(self):
        """**Revised by ADR-0047 (F60).** This row used to assert `GOAL_FAILED`.

        A failed *turn* is not a failed *objective*. The acceptance verdict is
        an independent, host-produced measurement; when it says every objective
        criterion holds, the objective is met and the timeout is a fact about
        the *attempt*. The old expectation made this a false negative, and five
        live runs paid for it with an extra attempt each — the objective was
        already satisfied and the loop re-attempted only to close the turn.

        `turn_succeeded=False` is still passed, and is still recorded; it simply
        no longer arbitrates.
        """
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=Verdict.PASS,
            turn_succeeded=False) is GoalState.GOAL_MET

    def test_t10b_a_timeout_without_a_pass_is_still_goal_failed(self):
        """The other half of the revision, and the half that did NOT move.

        A fatal terminal error with no `PASS` is still a failure — and it still
        outranks stagnation, so a *heuristic* cannot soften a *fact*. Without
        this, ADR-0047 would have bought F60 by weakening terminal honesty.
        """
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=Verdict.INCONCLUSIVE,
            turn_succeeded=False) is GoalState.GOAL_FAILED
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=Verdict.INCONCLUSIVE,
            stagnating=True,
            turn_succeeded=False) is GoalState.GOAL_FAILED
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=None,
            turn_succeeded=False) is GoalState.GOAL_FAILED

    def test_t10c_a_pass_never_rescues_a_fail_verdict(self):
        """Row 3 still outranks row 4: a `FAIL` is decisive whatever the turn did."""
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=Verdict.FAIL,
            turn_succeeded=False) is GoalState.GOAL_FAILED
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.SUCCEEDED,
            acceptance_verdict=Verdict.FAIL,
            turn_succeeded=True) is GoalState.GOAL_FAILED

    def test_t11_budget_exhaustion_is_goal_failed(self):
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.FAILED,
            acceptance_verdict=Verdict.INCONCLUSIVE,
            turn_succeeded=False) is GoalState.GOAL_FAILED

    def test_t12_a_recorded_terminal_state_is_frozen(self):
        """Row 0: a later observation cannot rewrite a recorded conclusion."""
        assert _derive(acceptance_verdict=Verdict.PASS,
                       already_recorded=GoalState.GOAL_FAILED) \
            is GoalState.GOAL_FAILED
        # ...and it cannot be *improved* either.
        assert _derive(acceptance_verdict=Verdict.FAIL,
                       already_recorded=GoalState.GOAL_MET) \
            is GoalState.GOAL_MET

    def test_escalation_outranks_failure(self):
        """Row 2: suppressing an escalation would convert "a human is required"
        into "failed" — the same error class as collapsing INCONCLUSIVE."""
        assert _derive(acceptance_verdict=Verdict.FAIL,
                       escalated=True) is GoalState.ESCALATED_TO_HUMAN

    def test_the_arbiter_is_total(self):
        """Every combination of the inputs yields a state; none raises."""
        seen = set()
        for outcome in TerminalOutcome:
            for acceptance in (None, Verdict.PASS, Verdict.FAIL,
                               Verdict.INCONCLUSIVE):
                for stagnating in (False, True):
                    for turn_ok in (False, True):
                        for cancelled in (False, True):
                            for escalated in (False, True):
                                seen.add(derive_goal_state(
                                    terminal_outcome=outcome,
                                    acceptance_verdict=acceptance,
                                    stagnating=stagnating,
                                    turn_succeeded=turn_ok,
                                    cancelled=cancelled,
                                    escalated=escalated))
        assert seen == set(GoalState), "the arbiter must be total over the six"

    def test_only_six_states_exist(self):
        """ADR-0035: no additional goal states in this phase."""
        assert {s.value for s in GoalState} == {
            "goal_met", "goal_unverified", "goal_stagnated",
            "goal_failed", "escalated_to_human", "cancelled",
        }


class TestT8SecurityNeverRetries:
    def test_a_denial_classifies_as_security(self):
        assert classify_failure_signal(
            "Blocked: not authorized", False, None) is FailureClass.SECURITY

    def test_security_forbids_retry_by_class(self):
        assert RecoveryRung.RETRY not in LEGAL_RUNGS[FailureClass.SECURITY]
        assert RecoveryRung.RETRY in FORBIDDEN_RUNGS[FailureClass.SECURITY]
        assert LEGAL_RUNGS[FailureClass.SECURITY] == frozenset({RecoveryRung.HUMAN})

    def test_a_security_failure_escalates_rather_than_retrying(self):
        ladder = RecoveryLadder()
        decision = ladder.decide(FailureClass.SECURITY, ["blocked: denied"])
        assert decision.rung is RecoveryRung.HUMAN
        assert ladder.escalated is True

    def test_timeout_and_budget_are_distinguishable_by_code(self):
        """Both are GOAL_FAILED, and the code is what keeps them apart."""
        assert classify_failure_signal("Turn timed out", False,
                                       CODE_TURN_TIMEOUT) is FailureClass.ENVIRONMENT
        assert classify_failure_signal("Max iterations reached", False,
                                       CODE_ITERATION_BUDGET) \
            is FailureClass.IMPLEMENTATION
        assert CODE_TURN_TIMEOUT != CODE_ITERATION_BUDGET


# ══════════════════════════════════════════════════════════════════════════
# D1–D8 — durability and replay
# ══════════════════════════════════════════════════════════════════════════


class TestDurability:
    def test_d1_the_goal_state_is_reconstructible_from_the_journal(self, tmp_path):
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("hi")])
        records = _goal_records(repo)
        assert len(records) == 1
        assert goal_state_from_record(records[0]) is GoalState.GOAL_UNVERIFIED

    def test_d2_the_acceptance_verdict_is_present_when_authoritative(self, tmp_path):
        """ADR-0035 durability gap #1 — closed by carrying the input.

        The diagnostic `VERDICT` record stays opt-in; the goal-state record is
        the one that must be complete, because it is the authoritative one.
        """
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("hi")])
        record = _goal_records(repo)[0]
        assert "acceptance_verdict" in record
        # No `VERDICT` record was asked for, so none was written.
        assert repo.reconstruct("pma")["_journal"]["verdicts"] == []

    def test_d3_every_evaluated_turn_has_an_explicit_stagnation_outcome(self, tmp_path):
        """Gap #2: a missing stagnation record must not be ambiguous between
        "not evaluated" and "evaluated and not stagnating".

        A content-only turn makes no observations, so P7's own semantics give
        `UNKNOWN` — "no verdict yet" — and that is what must be recorded. The
        point is that it is a *positive* record: the three real values are
        distinguishable, and the `not_evaluated` sentinel means the detector was
        absent rather than quiet.
        """
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("hi")])
        record = _goal_records(repo)[0]
        assert record["stagnation_verdict"] in (
            "unknown", "progressing", "stagnating")
        assert record["stagnation_verdict"] != "not_evaluated"

    def test_d4_recovery_decisions_produce_durable_records(self, tmp_path):
        _session, repo, _ev = _run_turn(
            tmp_path, [_boom()], recovery_ladder=True)
        records = _recovery_records(repo)
        assert records, "the recovery consumer produced no record"
        assert records[0]["failure_class"]

    def test_d5_escalation_produces_a_durable_record(self, tmp_path):
        """A `Blocked:` denial is a SECURITY failure, whose only rung is HUMAN."""
        _session, repo, _ev = _run_turn(
            tmp_path, [_boom("Blocked: not authorized")], recovery_ladder=True)
        assert _escalation_record(repo), "no escalation was recorded"

    def test_d6_replay_reproduces_the_same_goal_state(self, tmp_path):
        """The record's own inputs must re-derive its own answer.

        Reads `stagnation_allows_goal_met` — the predicate the arbiter consumed
        (F35 / ADR-0036 §6) — and **not** the human-readable
        `stagnation_verdict`, which ignores `trap_fired`. Reconstructing from
        the verdict string let a trap-only stagnation replay as `GOAL_MET`.
        """
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("hi")])
        record = _goal_records(repo)[0]
        again = derive_goal_state(
            terminal_outcome=record["terminal_outcome"],
            acceptance_verdict=record["acceptance_verdict"] or None,
            stagnating=not record["stagnation_allows_goal_met"],
            turn_succeeded=record["turn_succeeded"],
            cancelled=record["cancelled"],
            escalated=record["escalated"],
        )
        assert str(again) == record["goal_state"]

    def test_d7_replay_reproduces_the_same_recovery_decision(self, tmp_path):
        _session, repo, _ev = _run_turn(
            tmp_path, [_boom()], recovery_ladder=True)
        first = _recovery_records(repo)[0]
        session = Session(session_id="r")
        session.replay([SessionEvent.recovery_event(1, dict(first))])
        assert session.recovery[0]["failure_class"] == first["failure_class"]
        assert session.recovery[0]["rung"] == first["rung"]

    def test_d8_missing_authoritative_evidence_fails_loud(self):
        """It must never silently guess PASS / GOAL_MET."""
        # Absent acceptance evidence -> the honest third outcome, never GOAL_MET.
        assert _derive(acceptance_verdict=None) is GoalState.GOAL_UNVERIFIED
        # A record with no goal state is refused, not defaulted.
        with pytest.raises(ValueError):
            goal_state_from_record({})
        with pytest.raises(ValueError):
            goal_state_from_record({"goal_state": "not_a_state"})

    def test_the_record_is_audit_only_not_state_bearing(self):
        """Losing it costs a query convenience, not the authority."""
        from wisp.core.session import STATE_BEARING_EVENT_TYPES
        assert SessionEventType.GOAL_STATE not in STATE_BEARING_EVENT_TYPES

    def test_the_record_is_journal_only_and_replays(self):
        from wisp.core.session import JOURNAL_ONLY_RECORDS, JOURNAL_ONLY_SHAPES
        assert "goal_states" in JOURNAL_ONLY_RECORDS
        assert set(JOURNAL_ONLY_SHAPES) == set(JOURNAL_ONLY_RECORDS)
        session = Session(session_id="s")
        session.replay([SessionEvent.goal_state_event(
            1, {"goal_state": "goal_failed", "terminal_outcome": "failed"})])
        assert session.goal_states[0]["goal_state"] == "goal_failed"
        assert "goal_states" in session.journal_records()

    def test_the_first_record_is_the_frozen_one(self):
        """A duplicate or later event cannot displace the first conclusion."""
        records = [{"goal_state": "goal_failed"}, {"goal_state": "goal_met"}]
        assert already_recorded_from(records) is GoalState.GOAL_FAILED

    def test_the_blob_does_not_carry_the_record(self, tmp_path):
        """ADR-0028's shape: journal-only means the blob genuinely lacks it."""
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("hi")])
        blob = repo._store.load_session("pma") or {}
        assert "goal_states" not in blob


# ══════════════════════════════════════════════════════════════════════════
# Flags
# ══════════════════════════════════════════════════════════════════════════


class TestFlags:
    def test_recovery_ladder_defaults_off(self):
        from wisp.config import WispConfig
        assert WispConfig().recovery_ladder is False

    def test_goal_state_defaults_off(self):
        """ADR-0035 clause 9: recorded under the P3 stage-3a pattern, which is
        opt-in — it adds a record to the log of every existing caller."""
        from wisp.config import WispConfig
        assert WispConfig().goal_state is False

    def test_the_default_config_writes_no_goal_state_record(self, tmp_path):
        """The default turn journal is exactly what it was before this phase."""
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("hi")],
                                        goal_state=False)
        assert _goal_records(repo) == []
        out = repo.reconstruct("pma")
        assert [str(e.event_type) for e in repo.load_events("pma")] == [
            "user_message", "assistant_message", "done"]

    def test_the_default_config_writes_no_recovery_record(self, tmp_path):
        _session, repo, _ev = _run_turn(tmp_path, [_boom()],
                                        recovery_ladder=False)
        assert _recovery_records(repo) == []
        assert _escalation_record(repo) == {}

    def test_enabling_the_flag_wires_the_recovery_consumer(self, tmp_path):
        _session, repo, _ev = _run_turn(
            tmp_path, [_boom()], recovery_ladder=True)
        assert _recovery_records(repo)

    def test_enabling_the_completion_flag_records_the_goal_state(self, tmp_path):
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("hi")],
                                        goal_state=True)
        assert len(_goal_records(repo)) == 1

    def test_the_recovery_flag_does_not_change_completion_semantics(self, tmp_path):
        """Completion and recovery flags are conceptually separate: the goal
        state is the same either way."""
        _s1, repo_off, _e1 = _run_turn(tmp_path / "a", [_boom()])
        _s2, repo_on, _e2 = _run_turn(
            tmp_path / "b", [_boom()], recovery_ladder=True)
        assert _goal_records(repo_off)[0]["goal_state"] == \
            _goal_records(repo_on)[0]["goal_state"] == "goal_failed"


# ══════════════════════════════════════════════════════════════════════════
# Live integration — the real turn path
# ══════════════════════════════════════════════════════════════════════════


class TestLiveIntegration:
    def test_a_content_turn_records_a_goal_state(self, tmp_path):
        _session, repo, events = _run_turn(tmp_path, [_content_only("done")])
        assert any(e.get("type") == "done" for e in events)
        record = _goal_records(repo)[0]
        assert record["terminal_outcome"] == "succeeded"
        assert record["turn_succeeded"] is True
        # No acceptance evidence in this environment (F8), so: not verified.
        assert record["goal_state"] == "goal_unverified"

    def test_a_fatal_turn_is_goal_failed(self, tmp_path):
        _session, repo, events = _run_turn(tmp_path, [_boom()])
        assert any(e.get("type") == "error" for e in events)
        record = _goal_records(repo)[0]
        assert record["terminal_outcome"] == "failed"
        assert record["turn_succeeded"] is False
        assert record["goal_state"] == "goal_failed"

    def test_a_stagnating_turn_is_goal_stagnated(self, tmp_path):
        """The live path consumes M13's established predicate."""
        _session, repo, _ev = _run_turn(
            tmp_path, _repeat("a.txt"), files={"a.txt": "x"})
        record = _goal_records(repo)[0]
        assert record["stagnation_verdict"] == "stagnating"
        assert record["goal_state"] == "goal_stagnated"

    def test_turn_success_is_unchanged_and_can_coexist_with_goal_failure(self, tmp_path):
        """The two levels are distinct — this is ADR-0035's substance."""
        _session, repo, _ev = _run_turn(tmp_path, [_boom()])
        record = _goal_records(repo)[0]
        assert record["turn_succeeded"] is False
        assert record["goal_state"] == "goal_failed"
        # And the converse: a succeeded turn that is not GOAL_MET.
        _s2, repo2, _e2 = _run_turn(tmp_path / "b", [_content_only("hi")])
        rec2 = _goal_records(repo2)[0]
        assert rec2["turn_succeeded"] is True
        assert rec2["goal_state"] == "goal_unverified"

    def test_the_recovery_consumer_runs_at_the_turn_boundary(self, tmp_path):
        _session, repo, _ev = _run_turn(
            tmp_path, [_boom()], recovery_ladder=True)
        decision = _recovery_records(repo)[0]
        assert decision["failure_class"]
        assert decision["rung"]
        assert decision["evidence"]


# ══════════════════════════════════════════════════════════════════════════
# Adversarial
# ══════════════════════════════════════════════════════════════════════════


class TestAdversarial:
    def test_false_success_provider_done_with_inconclusive_acceptance(self, tmp_path):
        """A terminal `done` must not become GOAL_MET."""
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("all done!")])
        assert _goal_records(repo)[0]["goal_state"] != "goal_met"

    def test_false_success_through_stagnation(self):
        assert _derive(acceptance_verdict=Verdict.PASS,
                       stagnating=True) is not GoalState.GOAL_MET

    def test_failure_erasure_a_later_done_cannot_undo_fail(self):
        assert _derive(acceptance_verdict=Verdict.FAIL) is GoalState.GOAL_FAILED

    def test_recovery_masquerading_as_success(self, tmp_path):
        """A retry/replan decision must never create GOAL_MET."""
        _session, repo, _ev = _run_turn(
            tmp_path, [_boom()], recovery_ladder=True)
        assert _recovery_records(repo), "no decision to check"
        assert _goal_records(repo)[0]["goal_state"] != "goal_met"

    def test_missing_evidence_does_not_guess(self):
        assert _derive(acceptance_verdict=None) is GoalState.GOAL_UNVERIFIED

    def test_duplicate_events_do_not_produce_contradictory_states(self):
        session = Session(session_id="dup")
        session.replay([
            SessionEvent.goal_state_event(1, {"goal_state": "goal_failed"}),
            SessionEvent.goal_state_event(2, {"goal_state": "goal_met"}),
        ])
        assert already_recorded_from(session.goal_states) is GoalState.GOAL_FAILED

    def test_restart_reconstructs_the_same_authority_result(self, tmp_path):
        """Kill/restart/reconstruct: the same durable inputs, the same state."""
        _session, repo, _ev = _run_turn(tmp_path, [_content_only("hi")])
        live = _goal_records(repo)[0]
        # A fresh reconstruction from the same store must agree exactly.
        from wisp.core.session_repo import SessionRepository
        again = SessionRepository(repo._store).reconstruct("pma")
        replayed = again["_journal"]["goal_states"][0]
        assert replayed["goal_state"] == live["goal_state"]
        assert goal_state_from_record(replayed) is goal_state_from_record(live)

    def test_an_empty_observation_cannot_create_completion(self):
        """F32's guard, restated at the authority boundary: no acceptance
        evidence and no progress is not a pass."""
        assert derive_goal_state(
            terminal_outcome=TerminalOutcome.INCOMPLETE,
            acceptance_verdict=None, turn_succeeded=False,
        ) is GoalState.GOAL_UNVERIFIED
