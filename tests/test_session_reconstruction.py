"""Migration M2 — journal-first reconstruction with blob fallback.

P1's remainder, and one half of the prerequisite that unblocks M11–M15.

The hazard this exists to avoid: `UnifiedStore.load_session` (the blob) is read
by five production consumers, while the journal was a different function with a
different shape. Switching them naively would make old sessions reconstruct
**worse** than the blob does — because **pre-P0 sessions have no turn body in
the log**, so the journal holds only a user message and a terminal marker.

`test_a_pre_p0_session_is_not_truncated` is the test that matters. A naive
`if events:` check picks the journal for such a session and returns one message.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

from wisp.core.session import SessionEvent, SessionEventType
from wisp.core.session_repo import SessionRepository

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def repo(tmp_path):
    from wisp.infra.store import UnifiedStore
    return SessionRepository(UnifiedStore(tmp_path / "wisp.db"))


def _blob(repo, session_id: str, messages, title: str = "T", model: str = "m"):
    """Write a session the way the BLOB path does — no journal body."""
    repo._store.save_session({
        "id": session_id, "model": model, "workspace": "/ws", "title": title,
        "messages": messages, "compaction_history": [],
        "created_at": 1.0, "updated_at": 2.0,
    })


def _pre_p0_journal(repo, session_id: str):
    """The exact shape a pre-P0 session has in the log: a user message and a
    terminal marker, and NO turn body."""
    repo.append_events(session_id, [
        SessionEvent.user_message(1, "hello"),
        SessionEvent(SessionEventType.DONE, 2, {"reason": "natural"}),
    ])


# ── The hazard ──────────────────────────────────────────────────────────


class TestThePreP0Hazard:
    def test_a_pre_p0_session_is_not_truncated(self, repo):
        """The whole reason the fallback exists. The journal HAS events for this
        session, so a naive `if events:` check would choose it and return a
        session truncated to one message."""
        _pre_p0_journal(repo, "old")
        _blob(repo, "old", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "a real answer"},
            {"role": "user", "content": "and a follow-up"},
        ])
        out = repo.reconstruct("old")
        assert len(out["messages"]) == 3, "the blob's history was lost"
        assert out["_source"] == "blob"

    def test_the_source_check_prefers_the_blob_for_a_pre_p0_session(self, repo):
        _pre_p0_journal(repo, "old")
        _blob(repo, "old", [{"role": "user", "content": "hello"}])
        assert repo.reconstruction_source("old") == "blob"

    def test_a_journal_with_only_a_user_message_is_not_enough(self, repo):
        """`messages` is the test, not `events`."""
        repo.append_events("only-user", [SessionEvent.user_message(1, "hi")])
        assert repo.reconstruction_source("only-user") == "none"

    def test_a_pre_p0_session_with_no_blob_is_none(self, repo):
        _pre_p0_journal(repo, "ghost")
        assert repo.reconstruction_source("ghost") == "none"
        assert repo.reconstruct("ghost") is None


# ── The journal path ────────────────────────────────────────────────────


class TestJournalFirst:
    def test_a_journaled_session_reconstructs_from_the_journal(self, repo):
        repo.append_events("new", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "done"),
        ])
        out = repo.reconstruct("new")
        assert out["_source"] == "journal"
        assert [m["role"] for m in out["messages"]] == ["user", "assistant"]

    def test_the_journal_carries_what_the_blob_never_had(self, repo):
        """The reason journal-first is worth doing: the audit records."""
        repo.append_events("audit", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "ok"),
            SessionEvent.proposal_event(3, {"tool_call_id": "c1", "name": "read_file"}),
            SessionEvent.outcome_event(4, {"tool_call_id": "c1", "status": "ok"}),
        ])
        replayed = repo.load_session("audit")
        assert len(replayed.proposals) == 1
        assert len(replayed.outcomes) == 1

    def test_the_source_check_reports_journal(self, repo):
        repo.append_events("new", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "ok"),
        ])
        assert repo.reconstruction_source("new") == "journal"

    def test_an_unknown_session_is_none(self, repo):
        assert repo.reconstruction_source("nope") == "none"
        assert repo.reconstruct("nope") is None


# ── Shape compatibility ─────────────────────────────────────────────────


BLOB_KEYS = {"id", "model", "workspace", "title", "messages",
             "compaction_history", "created_at", "updated_at"}


class TestShapeCompatibility:
    def test_the_journal_result_has_the_blob_shape(self, repo):
        """Adoption must be a one-line change per consumer, not a rewrite."""
        repo.append_events("s", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "ok"),
        ])
        assert BLOB_KEYS <= set(repo.reconstruct("s"))

    def test_the_blob_fallback_result_has_the_blob_shape(self, repo):
        _pre_p0_journal(repo, "old")
        _blob(repo, "old", [{"role": "user", "content": "x"}])
        assert BLOB_KEYS <= set(repo.reconstruct("old"))

    def test_the_title_survives_from_the_blob(self, repo):
        """`title` is blob-only; the journal never carried it."""
        repo.append_events("s", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "ok"),
        ])
        _blob(repo, "s", [], title="My Session")
        assert repo.reconstruct("s")["title"] == "My Session"

    def test_the_result_is_json_serializable(self, repo):
        """Consumers hand this to a transport."""
        repo.append_events("s", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "ok"),
        ])
        json.dumps(repo.reconstruct("s"))

    def test_the_source_is_recorded_on_the_result(self, repo):
        repo.append_events("s", [
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "ok"),
        ])
        assert repo.reconstruct("s")["_source"] == "journal"

    def test_the_source_field_does_not_break_the_blob_shape(self, repo):
        """`_source` is additive: every documented key is still present."""
        _pre_p0_journal(repo, "old")
        _blob(repo, "old", [{"role": "user", "content": "x"}])
        out = repo.reconstruct("old")
        assert BLOB_KEYS <= set(out) and out["_source"] == "blob"


# ── End to end through a real turn ──────────────────────────────────────


class TestEndToEnd:
    def test_a_real_turn_is_reconstructible_from_the_journal(self, tmp_path):
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
        session = {"id": "e2e", "model": "mock", "workspace": str(ws),
                   "messages": []}

        async def _main():
            return [ev async for ev in runtime.run_turn(session, prompt="go")]
        asyncio.run(_main())

        assert repo.reconstruction_source("e2e") == "journal"
        out = repo.reconstruct("e2e")
        assert out["_source"] == "journal"
        assert any(m["role"] == "user" for m in out["messages"])
        assert any(m["role"] == "assistant" for m in out["messages"])


# ── Reachability and honesty ────────────────────────────────────────────


class TestReachabilityAndHonesty:
    def test_the_methods_are_on_the_repository(self):
        assert hasattr(SessionRepository, "reconstruct")
        assert hasattr(SessionRepository, "reconstruction_source")

    def test_the_repository_already_holds_both_sources(self):
        """It is the natural home: `self._store` is the blob, and the same
        object owns the journal."""
        src = inspect.getsource(SessionRepository.__init__)
        assert "self._store = store" in src

    def test_the_five_consumers_still_read_the_blob(self):
        """Honest accounting: adoption is per-consumer and is NOT done here.

        This test documents the remaining step rather than letting the method's
        existence imply the switch happened. It fails when the consumers are
        migrated — at which point update the report and delete it.
        """
        consumers = {
            "wisp/__main__.py": "load_session",
            "wisp/supervisor.py": "load_session",
            "wisp/sdk.py": "load_session",
            "wisp/acp_session.py": "load_session",
            "wisp/server/routes/sessions.py": "load_session",
        }
        still_blob = []
        for rel, needle in consumers.items():
            p = REPO / rel
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if needle in text and "reconstruct(" not in text:
                still_blob.append(rel)
        assert len(still_blob) == len(consumers), (
            "a consumer was migrated to reconstruct() — update "
            f"PHASE_M2_REPORT.md and delete this test. Migrated: "
            f"{sorted(set(consumers) - set(still_blob))}")

    def test_the_fallback_is_documented_in_the_code(self):
        src = (REPO / "wisp" / "core" / "session_repo.py").read_text(encoding="utf-8")
        assert "pre-P0" in src
        assert "journal first, blob as the fallback" in src
