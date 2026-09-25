"""M16 — the escalation is state, not audit (ADR-0028).

ADR-0027 classified every durable record by what its loss costs, and left one
row flagged:

> `RECOVERY` / `ESCALATION` (P6) — resumability, the escalation *is* the state —
> best-effort, canary; **open item M16**

Revisiting it found the policy wrong in **both** directions, and the read side
was the worse of the two.

**The read side.** M4 made `reconstruction_source()` refuse a gapped journal and
fall back to the blob. That settles which *transcript* to trust — and says
nothing about the journal-only records, which are independent of the
transcript's validity. So a gapped journal whose escalation had survived still
had it, and the fallback **threw it away**: the blob carries no `escalation`
key, so a parked run lost the record of why it was parked, and the loss was
reported only as `_gap`. `test_a_surviving_escalation_is_not_discarded` is that
hole, and it is the reason this file exists.

**The write side.** `_journal_turn_events` swallowed every failure, on ADR-0004's
rule that a turn must never be broken by a failed write. That rule is right for a
*record of what happened*; it is wrong for a *state transition*, where continuing
as though the write landed is not a lost observation but a **false record**.

Neither change makes a write fail-loud in general. ADR-0004 stands.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wisp.core.session import (
    JOURNAL_ONLY_RECORDS,
    JOURNAL_ONLY_SHAPES,
    STATE_BEARING_EVENT_TYPES,
    Session,
    SessionEvent,
    SessionEventType,
    empty_journal_records,
    is_state_bearing,
)
from wisp.core.session_repo import SessionRepository

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repo(tmp_path):
    from wisp.infra.store import UnifiedStore
    return SessionRepository(UnifiedStore(db_path=str(tmp_path / "wisp.db")))


def _escalation() -> dict:
    """A real `HumanIntervention`, produced the way P6 produces one."""
    from wisp.core.recovery import FailureClass, RecoveryLadder

    ladder = RecoveryLadder()
    ladder.decide(FailureClass.IMPLEMENTATION, ["verify: FAIL"])
    ladder.escalate(FailureClass.IMPLEMENTATION, reason="no rung left")
    return ladder.escalation.to_dict()


def _save_blob(repo, sid: str, *, content: str = "the blob's answer") -> None:
    repo._store.save_session({
        "id": sid, "model": "m", "workspace": "/ws", "title": "T",
        "messages": [{"role": "user", "content": "go"},
                     {"role": "assistant", "content": content}],
        "compaction_history": [], "created_at": 1.0, "updated_at": 2.0,
    })


def _gapped_with_escalation(repo, sid: str = "s1") -> dict:
    """A turn that escalated, followed by a LOST write (seq 2 absent).

    The escalation at seq 3 survives; the journal is non-contiguous, so M4's
    `reconstruction_source()` refuses it and falls back to the blob.
    """
    repo.append_events(sid, [
        SessionEvent.user_message(0, "go"),
        SessionEvent.assistant_message(1, "", [
            {"id": "c1", "type": "function",
             "function": {"name": "write_file", "arguments": "{}"}}]),
        # seq 2 LOST — a permitted best-effort write failure (ADR-0004).
        SessionEvent.escalation_event(3, _escalation()),
    ])
    return _escalation()


# ══════════════════════════════════════════════════════════════════════════
# The authority
# ══════════════════════════════════════════════════════════════════════════


class TestTheStateBearingAuthority:
    def test_escalation_is_the_only_state_bearing_kind(self):
        """ADR-0027 carved out exactly one record, and the code should say so.

        A second kind appearing here without an ADR would be a policy change
        smuggled in as a set literal.
        """
        assert STATE_BEARING_EVENT_TYPES == frozenset({SessionEventType.ESCALATION})

    @pytest.mark.parametrize("kind", [
        SessionEventType.PROPOSAL, SessionEventType.OUTCOME,
        SessionEventType.VERDICT, SessionEventType.TASK_GRAPH,
        SessionEventType.NODE_TRANSITION, SessionEventType.RECOVERY,
        SessionEventType.USER_MESSAGE, SessionEventType.ASSISTANT_MESSAGE,
        SessionEventType.TOOL_CALL, SessionEventType.TOOL_RESULT,
        SessionEventType.COMPACTED, SessionEventType.ERROR,
        SessionEventType.DONE,
    ])
    def test_the_audit_kinds_stay_best_effort(self, kind):
        """Each of these is a record *about* a turn. The turn's own behaviour is
        unaffected by its loss, so ADR-0004's best-effort rule still applies —
        and this test is what stops the carve-out widening."""
        assert kind not in STATE_BEARING_EVENT_TYPES
        ev = SessionEvent(kind, 1, {})
        assert not is_state_bearing([ev])

    def test_a_batch_is_state_bearing_if_any_event_is(self):
        """Position must not matter: a caller building a batch must not be able
        to hide an escalation behind an assistant message."""
        assert is_state_bearing([
            SessionEvent.assistant_message(1, "hi"),
            SessionEvent.escalation_event(2, _escalation()),
        ])
        assert not is_state_bearing([
            SessionEvent.assistant_message(1, "hi"),
            SessionEvent.verdict_event(2, {"verdict": "pass"}),
        ])

    def test_an_empty_batch_is_not_state_bearing(self):
        assert not is_state_bearing([])

    def test_the_runtime_reads_the_authority_rather_than_restating_it(self):
        """AST: `runtime.py` must not name `ESCALATION` directly. A second
        decision about which records are special is a second authority for the
        question — the defect class this migration exists to remove."""
        tree = ast.parse((REPO / "wisp" / "core" / "runtime.py")
                         .read_text(encoding="utf-8"))
        literals = [
            n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        offenders = [s for s in literals if s.strip() == "escalation"]
        assert not offenders, (
            "runtime.py names the escalation event type directly; import "
            f"`is_state_bearing` from wisp.core.session instead: {offenders}")

    def test_the_journal_only_list_covers_every_audit_kind(self):
        """The list is what the fallback salvages. An audit kind missing from it
        is a record silently dropped on the blob path.

        NOTE (M13): `STAGNATION` joined the audit kinds in M13. This set is an
        explicit enumeration rather than a totality check over the enum, because
        not every event kind is an audit record (`USER_MESSAGE`, `DONE`,
        `ERROR`, `COMPACTED` are not) — so a new audit kind must be added here
        deliberately, which is what this guard is for.

        NOTE (ADR-0035): `GOAL_STATE` joined in the post-M13 authority phase.
        The guard fired again and was answered the same way — by declaring the
        new kind, not by relaxing the count.
        """
        audit = {
            SessionEventType.PROPOSAL, SessionEventType.OUTCOME,
            SessionEventType.VERDICT, SessionEventType.TASK_GRAPH,
            SessionEventType.NODE_TRANSITION, SessionEventType.RECOVERY,
            SessionEventType.ESCALATION, SessionEventType.STAGNATION,
            SessionEventType.GOAL_STATE,
        }
        # Each kind maps to a Session field; the names are the contract.
        assert len(JOURNAL_ONLY_RECORDS) == len(audit)
        assert set(JOURNAL_ONLY_SHAPES) == set(JOURNAL_ONLY_RECORDS)


# ══════════════════════════════════════════════════════════════════════════
# The read side — the hole
# ══════════════════════════════════════════════════════════════════════════


class TestTheFallbackNoLongerDiscards:
    def test_the_blob_genuinely_lacks_every_journal_only_record(self, repo):
        """The salvage is only worth anything if the blob really has none of
        these. Asserted, so the day `save_session` starts carrying one, the
        duplication becomes visible rather than silent."""
        repo.append_events("s1", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "hi"),
        ])
        _save_blob(repo, "s1")
        blob = repo._store.load_session("s1")
        for name in JOURNAL_ONLY_RECORDS:
            assert name not in blob, f"the blob now carries {name!r}"

    def test_a_surviving_escalation_is_not_discarded(self, repo):
        """**The hole.** A gapped journal whose escalation survived still has it.

        Before M16 the fallback returned the blob's transcript and nothing else,
        so the escalation vanished — a parked run with no record of why it was
        parked, reported as `_gap` and nothing more.
        """
        expected = _gapped_with_escalation(repo)
        _save_blob(repo, "s1")

        out = repo.reconstruct("s1")

        # The transcript is still the blob's — M4's decision, unchanged.
        assert out["_source"] == "blob"
        assert out["messages"][-1]["content"] == "the blob's answer"
        assert out["_gap"] is True
        # …and the escalation survives anyway.
        assert out["_journal"]["escalation"]["intervention_id"] == \
            expected["intervention_id"]
        assert out["_journal"]["escalation"]["reason"] == "no rung left"

    def test_the_loss_is_reported_as_a_named_risk_not_only_a_gap(self, repo):
        """`_gap` says events are missing. It does not say *which records that
        endangers*, and on the blob path the answer is all of them."""
        _gapped_with_escalation(repo)
        _save_blob(repo, "s1")
        out = repo.reconstruct("s1")
        assert out["_journal_records_at_risk"] == list(JOURNAL_ONLY_RECORDS)

    def test_a_clean_journal_puts_nothing_at_risk(self, repo):
        repo.append_events("s1", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "done"),
            SessionEvent.escalation_event(2, _escalation()),
        ])
        out = repo.reconstruct("s1")
        assert out["_source"] == "journal"
        assert out["_gap"] is False
        assert out["_journal_records_at_risk"] == []

    def test_the_pre_p0_fallback_still_works(self, repo):
        """M2's original hazard, unchanged: a session with no turn body in the
        journal must still reconstruct from the blob, not truncate to one
        message. M16 must not have disturbed it."""
        repo.append_events("pre-p0", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.done(1, 0),
        ])
        _save_blob(repo, "pre-p0", content="the pre-P0 answer")
        out = repo.reconstruct("pre-p0")
        assert out["_source"] == "blob"
        assert out["_gap"] is False, "no turn body is not a gap"
        assert out["messages"][-1]["content"] == "the pre-P0 answer"

    def test_a_missing_session_is_still_none(self, repo):
        assert repo.reconstruct("nope") is None


# ══════════════════════════════════════════════════════════════════════════
# The read side — the shape
# ══════════════════════════════════════════════════════════════════════════


class TestTheRecordShape:
    def test_the_journal_path_carries_the_records(self, repo):
        repo.append_events("s1", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "done"),
            SessionEvent.verdict_event(2, {"verdict": "pass"}),
            SessionEvent.escalation_event(3, _escalation()),
        ])
        out = repo.reconstruct("s1")
        assert len(out["_journal"]["verdicts"]) == 1
        assert out["_journal"]["escalation"]["state"] == "pending"

    def test_the_records_are_present_on_both_paths(self, repo):
        """A caller reads `_journal` unconditionally — the whole point of
        `empty_journal_records()` is that it never has to branch on source."""
        repo.append_events("s1", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "done"),
        ])
        journal_path = repo.reconstruct("s1")
        _save_blob(repo, "s1")
        blob_path = repo.reconstruct("s1")
        for out in (journal_path, blob_path):
            assert set(out["_journal"]) == set(JOURNAL_ONLY_RECORDS)
            assert "_journal_records_at_risk" in out

    def test_empty_journal_records_matches_the_real_shape(self):
        """The empty template is built from the same authority, so it cannot
        drift from what a replayed session produces."""
        session = Session(session_id="x")
        session.replay([])
        real, empty = session.journal_records(), empty_journal_records()
        assert tuple(real) == tuple(empty) == JOURNAL_ONLY_RECORDS
        assert {k: type(v) for k, v in real.items()} == \
            {k: type(v) for k, v in empty.items()}

    def test_the_records_are_copies(self):
        """A caller mutating the result must not reach into session state."""
        session = Session(session_id="x")
        session.replay([])
        session.escalation = {"intervention_id": "e1"}
        session.proposals.append({"p": 1})
        records = session.journal_records()
        records["escalation"]["intervention_id"] = "mutated"
        records["proposals"].append({"p": 2})
        assert session.escalation["intervention_id"] == "e1"
        assert len(session.proposals) == 1

    def test_has_escalation_is_the_named_question(self):
        """A resume asks whether an escalation exists, so it gets a name rather
        than every caller reaching into the dict."""
        session = Session(session_id="x")
        session.replay([])
        assert not session.has_escalation
        session.apply(SessionEvent.escalation_event(1, _escalation()))
        assert session.has_escalation

    def test_the_escalation_never_enters_the_transcript(self):
        """Audit-only, like every other record on this path. A second path into
        `messages` would duplicate tool replies on replay (P2's rule)."""
        session = Session(session_id="x")
        session.replay([SessionEvent.user_message(0, "go"),
                        SessionEvent.assistant_message(1, "hi")])
        before = [dict(m) for m in session.messages]
        session.apply(SessionEvent.escalation_event(2, _escalation()))
        assert session.messages == before


# ══════════════════════════════════════════════════════════════════════════
# The write side
# ══════════════════════════════════════════════════════════════════════════


class _FailingRepo:
    """A repository whose every write fails, as a full disk would."""

    def __init__(self):
        self.attempts = 0

    def append_events(self, sid, events):
        self.attempts += 1
        raise RuntimeError("disk full")


def _runtime_with(repo):
    from wisp.core.runtime import AgentRuntime

    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.session_repo = repo
    return runtime


class TestTheWritePolicy:
    def test_a_turn_body_stays_best_effort(self):
        """ADR-0004, unchanged. A turn that ran correctly must not be reported
        as failed because journaling failed — the loss shows up as a gap."""
        repo = _FailingRepo()
        runtime = _runtime_with(repo)
        runtime._journal_turn_events("s", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "hi"),
        ])
        assert repo.attempts == 1, "the write was attempted"

    def test_an_audit_only_batch_stays_best_effort(self):
        """P2/P3/P4's records are records *about* a turn. Widening the carve-out
        to them would be a policy change, not a fix."""
        repo = _FailingRepo()
        runtime = _runtime_with(repo)
        runtime._journal_turn_events("s", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.verdict_event(2, {"verdict": "pass"}),
            SessionEvent.task_graph_event(3, {"run_id": "r"}),
        ])
        assert repo.attempts == 1

    def test_a_state_bearing_batch_propagates(self):
        """**The write side.** The escalation's write IS the state transition.
        Swallowing it lets the turn continue as though the park were recorded —
        a false record, not a lost observation."""
        repo = _FailingRepo()
        runtime = _runtime_with(repo)
        with pytest.raises(RuntimeError, match="disk full"):
            runtime._journal_turn_events("s", [
                SessionEvent.assistant_message(1, "hi"),
                SessionEvent.escalation_event(2, _escalation()),
            ])
        assert repo.attempts == 1

    def test_position_in_the_batch_does_not_hide_it(self):
        repo = _FailingRepo()
        runtime = _runtime_with(repo)
        with pytest.raises(RuntimeError):
            runtime._journal_turn_events("s", [
                SessionEvent.tool_call_event(1, "read_file", {}),
                SessionEvent.tool_result_event(2, "read_file", "ok"),
                SessionEvent.escalation_event(3, _escalation()),
            ])

    def test_no_repository_is_a_no_op(self):
        runtime = _runtime_with(None)
        runtime._journal_turn_events("s", [SessionEvent.escalation_event(1, {})])

    def test_an_empty_batch_is_a_no_op(self):
        repo = _FailingRepo()
        runtime = _runtime_with(repo)
        runtime._journal_turn_events("s", [])
        assert repo.attempts == 0

    def test_a_successful_write_propagates_nothing(self, repo):
        """The policy must not turn a working write into an error."""
        runtime = _runtime_with(repo)
        runtime._journal_turn_events("s1", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "done"),
            SessionEvent.escalation_event(2, _escalation()),
        ])
        assert repo.load_session("s1").has_escalation


# ══════════════════════════════════════════════════════════════════════════
# The policy does not break the normal path
# ══════════════════════════════════════════════════════════════════════════


class TestTheInvariantHolds:
    def test_a_real_escalating_session_round_trips(self, repo):
        """The end-to-end property: an escalation written through the real
        journal, replayed, and read back by name."""
        _gapped_with_escalation(repo, "clean")
        # Make it contiguous by writing the missing event.
        repo.append_events("clean2", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "", [
                {"id": "c1", "type": "function",
                 "function": {"name": "write_file", "arguments": "{}"}}]),
            SessionEvent.tool_result_event(2, "write_file", "ok"),
            SessionEvent.escalation_event(3, _escalation()),
        ])
        session = repo.load_session("clean2")
        assert not session.gap_detected
        assert session.has_escalation
        assert session.escalation["state"] == "pending"

    def test_the_escalation_survives_a_replay_cycle(self, repo):
        """Write, replay, re-derive: the intervention the operator receives is
        the one the ladder produced, including the audit trail inside it."""
        expected = _escalation()
        repo.append_events("s1", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "done"),
            SessionEvent.escalation_event(2, expected),
        ])
        got = repo.load_session("s1").escalation
        assert got["intervention_id"] == expected["intervention_id"]
        assert len(got["ladder_history"]) == len(expected["ladder_history"])
        assert got["ladder_history"], "the operator gets the trail, not a bare question"

    def test_a_session_without_an_escalation_reports_none(self, repo):
        repo.append_events("s1", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "done"),
        ])
        assert not repo.load_session("s1").has_escalation
        assert repo.reconstruct("s1")["_journal"]["escalation"] == {}
