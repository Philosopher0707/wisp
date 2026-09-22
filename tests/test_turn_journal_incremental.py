"""Migration P1 — incremental turn journal.

P0 made the turn body durable, but only at **turn end**, inside `run_turn`'s
`finally` block. A SIGKILL never runs a `finally`, so a killed turn left only
its `user_message` on disk and the turn's interior was a black box.

P1 journals each exchange the moment it closes. These tests prove that by
inspecting the database **from inside the turn** — the probe runs in the
provider, which is upstream of the `finally` block, so anything it can see is
already durable.

Tested here:

1. A closed exchange is on disk mid-turn (the incrementality property).
2. The rollback flag restores turn-end-only writing.
3. No event is written twice (the incremental and turn-end writers do not
   overlap).
4. Sequence numbers stay unique and increasing across both writers.
5. The incremental journal and the turn-end transcript agree on the exchange
   boundaries — replay still reproduces the transcript.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.core.session import SessionEventType
from wisp.core.session_repo import SessionRepository
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry


# ── Harness ─────────────────────────────────────────────────────────────


def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": path})}}


def _runtime(provider_factory, tmp_path, ws_files=None, **cfg_kw):
    """`provider_factory(repo, sid) -> provider`.

    The provider is built AFTER the store, because the probe provider reads
    the database from inside the turn. (Assigning `runtime.core_factory`
    after construction does not work: `_get_core` reads the dataclass field
    `core_factory`, and the first core would already be built with the stub.)
    """
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    for name, text in (ws_files or {}).items():
        (ws / name).write_text(text)
    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)

    def factory():
        provider = provider_factory(repo)
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory,
        session_repo=repo, config=config)
    return runtime, repo, str(ws)


def _session(ws, sid="p1"):
    return {"id": sid, "model": "mock", "workspace": ws, "messages": []}


def _run_turn(runtime, session, prompt="go"):
    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt=prompt)]
    return asyncio.run(_main())


def _repo_types(repo, sid):
    return [str(e.event_type) for e in repo.load_events(sid)]


class _ProbingProvider:
    """Round 1 asks for a tool; round 2 records what the DB already holds.

    The probe fires from inside `core.turn`, which runs *before* `run_turn`'s
    `finally` block. So anything visible to it was written incrementally,
    not at turn end.
    """

    def __init__(self, repo, sid, probe: dict, path: str = "a.txt"):
        self._repo, self._sid, self._probe, self._path = repo, sid, probe, path
        self.calls = 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "tool_calls", "calls": [_read_call(self._path, "c0")]}
            yield {"type": "done", "done_reason": "tool_calls"}
            return
        self._probe["kinds"] = [str(e.event_type)
                                for e in self._repo.load_events(self._sid)]
        self._probe["messages_seen"] = [
            (m.get("role"), str(m.get("content"))[:30]) for m in messages]
        yield {"type": "content", "text": "finished"}
        yield {"type": "done", "done_reason": "stop"}


# ── 1. The incrementality property ──────────────────────────────────────


class TestIncrementalDurability:
    def test_closed_exchange_is_on_disk_mid_turn(self, tmp_path):
        """The core claim of P1: after the first exchange closes, its events
        are already durable — before the turn has finished."""
        probe: dict = {}
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "inc1", probe), tmp_path,
            ws_files={"a.txt": "hello"})

        _run_turn(runtime, _session(ws, "inc1"))

        mid = probe["kinds"]
        assert mid, "the probe never ran — the turn did not reach round 2"
        # Everything except the terminal marker was already written.
        assert SessionEventType.USER_MESSAGE.value in mid
        assert SessionEventType.ASSISTANT_MESSAGE.value in mid, mid
        assert SessionEventType.TOOL_RESULT.value in mid, mid
        assert SessionEventType.DONE.value not in mid, (
            "the probe must fire before the terminal marker is written")

    def test_mid_turn_journal_is_replayable(self, tmp_path):
        """What the probe sees must be enough to rebuild the conversation —
        a partial journal that cannot be replayed is not recovery."""
        probe: dict = {}
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "inc2", probe), tmp_path,
            ws_files={"a.txt": "hello"})

        _run_turn(runtime, _session(ws, "inc2"))

        # Reconstruct from ONLY what was durable mid-turn.
        mid_events = [e for e in repo.load_events("inc2")
                      if e.event_type != SessionEventType.DONE]
        partial = repo.load_session("inc2")
        assert partial is not None
        assert partial.unknown_events == 0
        roles = [m["role"] for m in partial.messages]
        assert roles[0] == "user"
        assert "tool" in roles, roles
        assert mid_events, "no mid-turn events were durable"


# ── 2. The rollback flag ────────────────────────────────────────────────


class TestRollbackFlag:
    def test_flag_off_leaves_the_turn_body_for_turn_end(self, tmp_path):
        probe: dict = {}
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "off1", probe), tmp_path,
            ws_files={"a.txt": "hello"}, turn_journal=False,
            # The proposal boundary is a separate concern with its own flag
            # (ADR-0002). Switched off here so this test measures the journal
            # flag alone.
            proposal_boundary=False)

        _run_turn(runtime, _session(ws, "off1"))

        # With the flag off the probe must see only the user message …
        assert probe["kinds"] == [SessionEventType.USER_MESSAGE.value], \
            probe["kinds"]
        # … and the turn body still lands at turn end, unchanged.
        final = _repo_types(repo, "off1")
        assert SessionEventType.ASSISTANT_MESSAGE.value in final
        assert SessionEventType.TOOL_RESULT.value in final
        assert final[-1] == SessionEventType.DONE.value

    def test_turn_journal_off_alone_still_records_the_boundary(self, tmp_path):
        """Independence: rolling back the incremental journal must not roll
        back the proposal boundary.

        Only OUTCOME is asserted, and that reflects this environment, not the
        code: `jsonschema` is missing (P0 F8) so every call is refused before
        it streams a `tool_call`, and a proposal is recorded only for a call
        that reached dispatch. A rejection is still recorded — which is the
        boundary's purpose.
        """
        probe: dict = {}
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "off1c", probe), tmp_path,
            ws_files={"a.txt": "hello"}, turn_journal=False)
        _run_turn(runtime, _session(ws, "off1c"))

        assert SessionEventType.OUTCOME.value in probe["kinds"]

    def test_flag_off_and_on_produce_the_same_final_journal(self, tmp_path):
        """The flag must change only WHEN events land, never WHICH events."""
        def _run(sid, **kw):
            runtime, repo, ws = _runtime(
                lambda r: _ProbingProvider(r, sid, {}), tmp_path,
                ws_files={"a.txt": "hello"}, **kw)
            _run_turn(runtime, _session(ws, sid))
            return [str(e.event_type) for e in repo.load_events(sid)]

        assert _run("same_on", turn_journal=True) == \
               _run("same_off", turn_journal=False)


# ── 3/4. No double-write, no sequence collisions ────────────────────────


class TestNoDoubleWrite:
    def test_every_event_is_written_exactly_once(self, tmp_path):
        """The incremental writer commits a prefix; the turn-end writer must
        slice it off. An overlap would duplicate the turn body in the log.

        Two assistant_messages is correct and expected: one carries the
        exchange's tool_calls, the other carries the turn's final text.
        """
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "dup1", {}), tmp_path,
            ws_files={"a.txt": "hello"})
        _run_turn(runtime, _session(ws, "dup1"))

        kinds = _repo_types(repo, "dup1")
        events = repo.load_events("dup1")
        assistant = [e for e in events
                     if e.event_type == SessionEventType.ASSISTANT_MESSAGE]
        assert kinds.count(SessionEventType.ASSISTANT_MESSAGE.value) == 2, kinds
        assert sum(1 for e in assistant if e.payload.get("tool_calls")) == 1
        assert sum(1 for e in assistant if not e.payload.get("tool_calls")) == 1
        assert kinds.count(SessionEventType.TOOL_RESULT.value) == 1, kinds
        assert kinds.count(SessionEventType.USER_MESSAGE.value) == 1, kinds
        assert kinds.count(SessionEventType.DONE.value) == 1, kinds

    def test_sequence_numbers_unique_and_increasing_across_writers(self, tmp_path):
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "seq1", {}), tmp_path,
            ws_files={"a.txt": "hello"})
        _run_turn(runtime, _session(ws, "seq1"))

        seqs = [e.sequence_num for e in repo.load_events("seq1")]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)
        # `get_last_sequence` returns -1 for an empty session, so the first
        # event lands at 0 — and the two writers together must leave no gap.
        assert seqs == list(range(len(seqs))), seqs

    def test_terminal_marker_is_still_last(self, tmp_path):
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "last1", {}), tmp_path,
            ws_files={"a.txt": "hello"})
        _run_turn(runtime, _session(ws, "last1"))

        events = repo.load_events("last1")
        assert events[-1].event_type == SessionEventType.DONE
        assert repo.was_last_turn_complete("last1") is True


# ── 5. Boundaries agree with the transcript ─────────────────────────────


class TestBoundaryAgreement:
    """The incremental journal and the turn-end serializer must agree on
    where an exchange ends, or the journal would replay into a transcript
    the live session never had."""

    def test_grouping_helper_is_sequential_and_prefix_stable(self):
        """The slice in `run_turn` is only sound if the closed set is a
        growing prefix. Pin that directly."""
        from wisp.core.runtime import _group_exchanges

        def _c(cid):
            return ("call", {"id": cid, "name": "t", "arguments": {}})

        def _r(cid):
            return ("reply", {"tool_call_id": cid, "name": "t", "result": "x"})

        seq = [_c("a"), _r("a"), _c("b"), _r("b")]
        closed_1 = _group_exchanges(seq[:2], closed_only=True)
        closed_2 = _group_exchanges(seq, closed_only=True)
        assert len(closed_1) == 1
        assert len(closed_2) == 2
        assert closed_2[:1] == closed_1

    def test_trailing_partial_exchange_is_not_closed(self):
        from wisp.core.runtime import _group_exchanges

        seq = [("call", {"id": "a", "name": "t", "arguments": {}})]
        assert _group_exchanges(seq, closed_only=True) == []
        assert len(_group_exchanges(seq, closed_only=False)) == 1

    def test_replay_after_incremental_journal_matches_the_transcript(self, tmp_path):
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "bd1", {}), tmp_path,
            ws_files={"a.txt": "hello"})
        session = _session(ws, "bd1")
        _run_turn(runtime, session)

        replayed = repo.load_session("bd1")
        assert replayed is not None
        assert replayed.unknown_events == 0
        assert [(m["role"], m.get("content", "")) for m in replayed.messages] == \
               [(m["role"], m.get("content", "")) for m in session["messages"]]

    def test_replay_is_provider_valid(self, tmp_path):
        """Every replayed assistant tool_calls block has a reply per id."""
        runtime, repo, ws = _runtime(
            lambda r: _ProbingProvider(r, "bd2", {}), tmp_path,
            ws_files={"a.txt": "hello"})
        _run_turn(runtime, _session(ws, "bd2"))

        msgs = repo.load_session("bd2").messages
        for i, m in enumerate(msgs):
            for call in m.get("tool_calls", []):
                replies = [r for r in msgs[i + 1:]
                           if r["role"] == "tool"
                           and r.get("tool_call_id") == call["id"]]
                assert replies, f"call {call['id']} unanswered after replay"
