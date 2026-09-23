"""M4 — durability as a correctness precondition (ADR-0004 revisited).

ADR-0004 declared every durable write best-effort, with its own reversal
condition:

> *"Once a phase requires durable state as a **correctness** precondition (P2's
> proposal boundary is the likely candidate), silent best-effort is no longer
> acceptable for that path and must become fail-loud. Revisit at P2."*

P2 landed, and so did P3–P6. But the decisive change was **M2**: it promoted the
journal from secondary to primary, which is what turns a *permitted* silent write
failure into a **silently truncated session**.

`test_a_gapped_journal_is_a_provider_invalid_transcript` is the hole. Without M4,
journal-first returns an assistant `tool_calls` block with no reply and reports
no error — a transcript a strict provider rejects, presented as authoritative.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from wisp.core.session import Session, SessionEvent
from wisp.core.session_repo import SessionRepository

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repo(tmp_path):
    from wisp.infra.store import UnifiedStore
    return SessionRepository(UnifiedStore(db_path=str(tmp_path / "wisp.db")))


def _gapped_journal(repo, sid: str = "gapped"):
    """The journal a best-effort write failure leaves behind.

    `TOOL_CALL` at seq 1 is written, its `TOOL_RESULT` at seq 2 is LOST, and the
    turn continues at seq 3. ADR-0004 permits exactly this.
    """
    repo.append_events(sid, [
        SessionEvent.user_message(0, "go"),
        SessionEvent.assistant_message(1, "", [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]),
        # seq 2 — the TOOL_RESULT — was lost to a failed write
        SessionEvent.assistant_message(3, "all done"),
    ])


# ── The hole ────────────────────────────────────────────────────────────


class TestTheGapHole:
    def test_a_gapped_journal_is_a_provider_invalid_transcript(self, repo):
        """The defect M4 closes: an assistant `tool_calls` block with no reply.

        A strict provider rejects this shape. Before M4 the journal was chosen
        anyway, and `unknown_events` stayed 0 — so nothing reported a problem.
        """
        _gapped_journal(repo)
        session = repo.load_session("gapped")
        roles = [m["role"] for m in session.messages]
        assert roles == ["user", "assistant", "assistant"]
        # The assistant declared a tool call; no tool reply follows it.
        assert session.messages[1].get("tool_calls")
        assert "tool" not in roles

    def test_replay_alone_does_not_report_the_gap(self, repo):
        """Which is why `gap_detected` had to be added — `unknown_events` is
        about unrecognised event KINDS, not missing ones."""
        _gapped_journal(repo)
        assert repo.load_session("gapped").unknown_events == 0

    def test_the_gap_is_now_detected(self, repo):
        _gapped_journal(repo)
        assert repo.load_session("gapped").gap_detected is True

    def test_a_gapped_journal_is_not_chosen(self, repo):
        _gapped_journal(repo)
        assert repo.reconstruction_source("gapped") != "journal"

    def test_it_falls_back_to_the_blob(self, repo):
        _gapped_journal(repo)
        repo._store.save_session({
            "id": "gapped", "model": "m", "workspace": "/ws", "title": "T",
            "messages": [{"role": "user", "content": "go"},
                         {"role": "assistant", "content": "complete answer"}],
            "compaction_history": [], "created_at": 1.0, "updated_at": 2.0,
        })
        out = repo.reconstruct("gapped")
        assert out["_source"] == "blob"
        assert len(out["messages"]) == 2

    def test_the_gap_flag_is_reported_on_the_result(self, repo):
        repo.append_events("ok", [
            SessionEvent.user_message(0, "go"),
            SessionEvent.assistant_message(1, "done"),
        ])
        assert repo.reconstruct("ok")["_gap"] is False


# ── `gap_detected` semantics ────────────────────────────────────────────


class TestGapDetected:
    def test_a_contiguous_sequence_is_not_a_gap(self):
        s = Session(session_id="x")
        s.replay([SessionEvent.user_message(0, "a"),
                  SessionEvent.assistant_message(1, "b")])
        assert s.gap_detected is False

    def test_a_hole_is_a_gap(self):
        s = Session(session_id="x")
        s.replay([SessionEvent.user_message(0, "a"),
                  SessionEvent.assistant_message(2, "b")])
        assert s.gap_detected is True

    def test_a_single_event_is_not_a_gap(self):
        """Contiguity is measured from the minimum present, not from zero, so
        applying one event — as tests and `append_events` callers do — is fine."""
        s = Session(session_id="x")
        s.replay([SessionEvent.user_message(1, "a")])
        assert s.gap_detected is False

    def test_a_non_zero_start_is_not_a_gap(self):
        s = Session(session_id="x")
        s.replay([SessionEvent.user_message(5, "a"),
                  SessionEvent.assistant_message(6, "b")])
        assert s.gap_detected is False

    def test_an_empty_session_is_not_a_gap(self):
        assert Session(session_id="x").gap_detected is False

    def test_replay_resets_the_gap_state(self):
        """A second replay must not inherit the first's sequences — otherwise
        every session would look gapped after a re-replay."""
        s = Session(session_id="x")
        s.replay([SessionEvent.user_message(0, "a"),
                  SessionEvent.assistant_message(2, "b")])
        assert s.gap_detected is True
        s.replay([SessionEvent.user_message(0, "a"),
                  SessionEvent.assistant_message(1, "b")])
        assert s.gap_detected is False

    def test_a_long_contiguous_run_is_not_a_gap(self):
        s = Session(session_id="x")
        s.replay([SessionEvent.user_message(i, f"m{i}") for i in range(50)])
        assert s.gap_detected is False

    def test_a_gap_at_the_end_is_detected(self):
        s = Session(session_id="x")
        s.replay([SessionEvent.user_message(0, "a"),
                  SessionEvent.assistant_message(1, "b"),
                  SessionEvent.user_message(3, "c")])
        assert s.gap_detected is True

    def test_a_gap_at_the_start_is_detected(self):
        s = Session(session_id="x")
        s.replay([SessionEvent.user_message(0, "a"),
                  SessionEvent.assistant_message(2, "b"),
                  SessionEvent.user_message(3, "c")])
        assert s.gap_detected is True


# ── The invariant holds in production ───────────────────────────────────


class TestTheInvariantHolds:
    """`gap_detected` is only useful if a real turn produces a gap-free journal.
    If it did not, the check would reject every real session."""

    def test_a_real_turn_produces_no_gap(self, tmp_path):
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session_repo import SessionRepository as Repo
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
        repo = Repo(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "inv", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        replayed = repo.load_session("inv")
        assert replayed.gap_detected is False, (
            "a real turn produced a gapped journal — the contiguity invariant "
            "does not hold, and `gap_detected` would reject every session")
        assert repo.reconstruction_source("inv") == "journal"

    def test_a_real_turn_still_reconstructs_from_the_journal(self, tmp_path):
        """The check must not have broken M2's happy path."""
        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session_repo import SessionRepository as Repo
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
        repo = Repo(store)

        def factory():
            return WispAgentCore(config=config, provider=_P(),
                                 security=SecurityPolicy(), tool_executor=None)

        runtime = AgentRuntime(
            store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
            telemetry=Telemetry(), core_factory=factory,
            session_repo=repo, config=config)
        session = {"id": "inv2", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        out = repo.reconstruct("inv2")
        assert out["_source"] == "journal"
        assert out["_gap"] is False


# ── Reachability and honesty ────────────────────────────────────────────


class TestReachabilityAndHonesty:
    def test_gap_detected_is_on_the_session(self):
        assert isinstance(Session.gap_detected, property)

    def test_the_reason_is_documented_in_the_code(self):
        src = (REPO / "wisp" / "core" / "session.py").read_text(encoding="utf-8")
        assert "ADR-0004" in src, "the property must cite why it exists"

    def test_the_source_check_cites_the_gap_condition(self):
        src = (REPO / "wisp" / "core" / "session_repo.py").read_text(encoding="utf-8")
        i = src.index("def reconstruction_source")
        body = src[i:i + 2000]
        assert "gap" in body
