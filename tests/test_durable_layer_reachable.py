"""Migration P0 — durable layer reachability + journal fidelity.

This file exists because the Phase 0 audit found **eight** complete, tested,
unreachable subsystems. Adding a ninth would be the worst possible outcome of
this migration, so every path P0 wires gets a reachability test — one that
drives a *production entry point* and asserts the durable artifact appears.

Covered here:

1. `AgentRuntime.run_turn` journals `assistant_message` / `tool_call` /
   `tool_result` events into `session_events` (before P0 the log held only
   `user_message` / `error` / `done`, so `load_session()` could never
   reconstruct a turn).
2. `Session.apply` handles `TOOL_CALL`, and a replay round-trips the
   transcript.
3. `Session.apply` fails loud on an unknown event type instead of silently
   dropping it.
4. The rollback flag restores the pre-migration log exactly.
5. `CompositionRoot` constructs `BackgroundAgentManager` WITH a run store,
   so a launch writes `background_runs` rows.
6. `ToolExecutor`'s lazy background-manager fallback inherits the store
   instead of silently degrading to in-memory.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from wisp.config import WispConfig
from wisp.core.engine import WispAgentCore
from wisp.core.runtime import AgentRuntime
from wisp.core.session import Session, SessionEvent, SessionEventType
from wisp.core.session_repo import SessionRepository
from wisp.infra.extensions import ExtensionHost
from wisp.infra.security import SecurityPolicy
from wisp.infra.store import UnifiedStore
from wisp.infra.telemetry import Telemetry


# ── Harness (mirrors tests/reliability/test_13h5_success_derivation.py) ──


def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file", "arguments": {"path": path}}}


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


def _hang():
    """Stall past the stream deadline so the turn ends without a `done`."""
    def _g():
        threading.Event().wait(30)
        yield {"type": "done", "done_reason": "stop"}
    return _g


def _runtime(provider, tmp_path, ws_files=None, with_trace_store=False,
             **cfg_kw):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    for name, text in (ws_files or {}).items():
        (ws / name).write_text(text)
    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    trace_store = None
    if with_trace_store:
        from wisp.trace.store import SQLiteTraceStore
        trace_store = SQLiteTraceStore(store)

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory,
        session_repo=repo, config=config, trace_store=trace_store)
    return runtime, repo, str(ws)


def _session(ws, sid="p0"):
    return {"id": sid, "model": "mock", "workspace": ws, "messages": []}


def _run_turn(runtime, session, prompt="go"):
    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt=prompt)]
    return asyncio.run(_main())


def _repo_events(repo, sid):
    return repo.load_events(sid)


def _repo_types(repo, sid):
    return [str(e.event_type) for e in _repo_events(repo, sid)]


def _span_count(runtime):
    return runtime.store._get_conn().execute(
        "SELECT COUNT(*) AS n FROM trace_spans").fetchone()["n"]


def _span_trace_ids(runtime):
    rows = runtime.store._get_conn().execute(
        "SELECT DISTINCT trace_id FROM trace_spans ORDER BY trace_id"
    ).fetchall()
    return [r["trace_id"] for r in rows]


def _all_spans(runtime):
    out = []
    for tid in _span_trace_ids(runtime):
        out.extend(runtime.trace_store.query(tid))
    return out


# ── 1. Reachability: a real turn journals its turn body ─────────────────


class TestTurnJournalsTurnBody:
    """Before P0 the append-only log held only `user_message` / `error` /
    `done`. `load_session()` — the documented crash-recovery replay source —
    could therefore never reconstruct a turn, and would overwrite a live
    session's messages with a user-message-only list."""

    def test_assistant_message_is_journaled(self, tmp_path):
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("hello there")]), tmp_path)
        _run_turn(runtime, _session(ws, "j1"))

        kinds = _repo_types(repo, "j1")
        assert SessionEventType.ASSISTANT_MESSAGE.value in kinds
        assistant = [e for e in _repo_events(repo, "j1")
                     if e.event_type == SessionEventType.ASSISTANT_MESSAGE]
        assert assistant[0].payload["content"] == "hello there"

    def test_terminal_marker_is_last_and_sequences_are_unique(self, tmp_path):
        """A reader must never observe a DONE whose turn body is missing."""
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("hello there")]), tmp_path)
        _run_turn(runtime, _session(ws, "j2"))

        events = _repo_events(repo, "j2")
        seqs = [e.sequence_num for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)          # no collisions
        assert events[-1].event_type == SessionEventType.DONE
        assert events[0].event_type == SessionEventType.USER_MESSAGE


class TestToolEventJournaling:
    """Unit-level: drives `_serialize_tool_exchanges`, the single walk that
    now produces BOTH the provider-valid messages and the journaled events.

    Driven directly rather than through a live turn because this environment
    cannot execute tools at all — `jsonschema` (a declared dependency) is not
    installed, so `_validate_tool_args` refuses every call with
    SCHEMA_INVALID before a `tool_call` event can stream. See
    WISP_MIGRATION_STATUS.md finding F7.
    """

    @staticmethod
    def _field(ev, key):
        if key in ev:
            return ev[key]
        d = ev.get("data")
        return d.get(key) if isinstance(d, dict) else None

    @staticmethod
    def _call(cid, name, args):
        return {"type": "tool_call", "id": cid, "name": name,
                "arguments": args, "data": {}}

    @staticmethod
    def _reply(cid, name, result):
        return {"type": "tool_result", "tool_call_id": cid, "id": cid,
                "name": name, "result": result, "data": {}}

    def _run(self, sequence, journal=True, proposal=True):
        from wisp.core.runtime import _serialize_tool_exchanges

        calls = [ev for k, ev in sequence if k == "call"]
        replies = [ev for k, ev in sequence if k == "reply"]
        exchanges = [{"calls": calls, "replies": replies}] if (calls or replies) else []
        session: dict = {"messages": []}
        events = _serialize_tool_exchanges(
            session, exchanges, self._field, journal=journal,
            proposal=proposal)
        return session, events

    def test_events_mirror_the_messages(self):
        session, events = self._run([
            ("call", self._call("c0", "read_file", {"path": "a.txt"})),
            ("reply", self._reply("c0", "read_file", "hello")),
        ])

        # Migration P2 added the proposal boundary's two audit records. They
        # sit alongside their relatives and never touch `messages`.
        kinds = [str(e.event_type) for e in events]
        assert kinds == ["assistant_message", "tool_call", "proposal",
                         "tool_result", "outcome"]

        # The assistant event carries the same provider-valid block the
        # message carries — one walk, one pairing decision.
        assistant = events[0]
        assert assistant.payload["tool_calls"] == session["messages"][0]["tool_calls"]
        assert session["messages"][0]["tool_calls"][0]["id"] == "c0"

        # The reply event carries the pairing id the transcript uses.
        assert events[3].payload["tool_call_id"] == "c0"
        assert session["messages"][1]["tool_call_id"] == "c0"

    def test_proposal_and_outcome_records_are_produced(self):
        """Migration P2: the `ToolRequest`/`ToolResult` contracts had no
        producer. A proposal is recorded for a real call, and an outcome for
        every proposal."""
        _, events = self._run([
            ("call", self._call("c0", "read_file", {"path": "a.txt"})),
            ("reply", self._reply("c0", "read_file", "hello")),
        ])
        proposal = [e for e in events if str(e.event_type) == "proposal"][0]
        outcome = [e for e in events if str(e.event_type) == "outcome"][0]

        req = proposal.payload["request"]
        assert req["tool_call_id"] == "c0"
        assert req["name"] == "read_file"
        assert req["args"] == {"path": "a.txt"}
        assert req["idempotency_key"], "the P1 idempotency key must join them"

        res = outcome.payload["result"]
        assert res["tool_call_id"] == "c0"
        assert res["status"] == "ok"
        assert res["metadata"]["outcome_class"] == "success"

    def test_call_event_records_the_real_invocation(self):
        _, events = self._run([
            ("call", self._call("c0", "read_file", {"path": "a.txt"})),
            ("reply", self._reply("c0", "read_file", "hello")),
        ])
        call = events[1]
        assert call.payload["name"] == "read_file"
        assert call.payload["arguments"] == {"path": "a.txt"}

    def test_journal_false_returns_no_transcript_events(self):
        """The rollback flag must not build transcript event objects at all.

        `proposal=False` is passed too: the two flags are independent by
        design (ADR-0002), and this test is about the journal flag.
        """
        session, events = self._run([
            ("call", self._call("c0", "read_file", {"path": "a.txt"})),
            ("reply", self._reply("c0", "read_file", "hello")),
        ], journal=False, proposal=False)
        assert events == []
        # …and the messages are byte-for-byte what they always were.
        assert [m["role"] for m in session["messages"]] == ["assistant", "tool"]

    def test_flags_are_independent(self):
        """Turning the journal off must not silently disable the proposal
        boundary — one flag per concern is what makes partial rollback work."""
        _, events = self._run([
            ("call", self._call("c0", "read_file", {"path": "a.txt"})),
            ("reply", self._reply("c0", "read_file", "hello")),
        ], journal=False, proposal=True)
        kinds = [str(e.event_type) for e in events]
        assert "tool_call" not in kinds and "tool_result" not in kinds
        assert "proposal" in kinds and "outcome" in kinds

    def test_gate_refused_call_is_not_fabricated_as_an_invocation(self):
        """A reply-only group means the call was refused before it ever
        streamed an event. Recording a TOOL_CALL for it would fabricate
        evidence the turn never produced — and a PROPOSAL would fabricate an
        intent the turn never expressed. The OUTCOME is still recorded: a
        rejection is a first-class observable event."""
        session, events = self._run([
            ("reply", self._reply("c1", "write_file", "POLICY_DENIED")),
        ])
        kinds = [str(e.event_type) for e in events]
        assert "tool_call" not in kinds
        assert "proposal" not in kinds
        assert kinds == ["assistant_message", "tool_result", "outcome"]

    def test_missing_reply_is_journaled_as_a_flagged_placeholder(self):
        """An interrupted turn has a call but no reply. The transcript gets
        an honest placeholder — and the journal MUST record it too, or replay
        would rebuild an assistant `tool_calls` block with no following tool
        message, which strict providers reject. The `synthesized` flag keeps
        the record honest: this result was never produced by a tool."""
        session, events = self._run([
            ("call", self._call("c0", "read_file", {"path": "a.txt"})),
        ])
        kinds = [str(e.event_type) for e in events]
        assert kinds == ["assistant_message", "tool_call", "proposal",
                         "tool_result", "outcome"]

        placeholder = "[no result recorded before turn ended]"
        assert session["messages"][1]["content"] == placeholder
        result_event = [e for e in events
                        if str(e.event_type) == "tool_result"][0]
        assert result_event.payload["result"] == placeholder
        assert result_event.payload["tool_call_id"] == "c0"
        assert result_event.payload["synthesized"] is True
        # The outcome record carries the same honesty flag.
        outcome = [e for e in events if str(e.event_type) == "outcome"][0]
        assert outcome.payload["result"]["metadata"]["synthesized"] is True

    def test_interrupted_turn_replays_into_a_provider_valid_transcript(self):
        """The property that matters: every assistant `tool_calls` block in a
        replayed transcript is followed by a reply for each of its ids."""
        session, events = self._run([
            ("call", self._call("c0", "read_file", {"path": "a.txt"})),
        ])
        stamped = [
            type(e)(e.event_type, i + 1, e.payload, e.timestamp)
            for i, e in enumerate(events)
        ]
        replayed = Session(session_id="rt")
        replayed.replay(stamped)

        msgs = replayed.messages
        for i, m in enumerate(msgs):
            for call in m.get("tool_calls", []):
                following = [
                    r for r in msgs[i + 1:]
                    if r["role"] == "tool" and r.get("tool_call_id") == call["id"]
                ]
                assert following, f"call {call['id']} has no reply after replay"

    def test_parallel_calls_pair_by_id_not_position(self):
        """GH#6: a refused call streams no call event, so positional pairing
        would attach the wrong refusal to the wrong id."""
        session, events = self._run([
            ("call", self._call("cB", "read_file", {"path": "b.txt"})),
            ("reply", self._reply("cA", "read_file", "A-content")),
            ("reply", self._reply("cB", "read_file", "B-content")),
        ])
        by_id = {m.get("tool_call_id"): m["content"]
                 for m in session["messages"] if m["role"] == "tool"}
        assert by_id == {"cA": "A-content", "cB": "B-content"}
        result_events = [e for e in events
                         if str(e.event_type) == "tool_result"]
        assert {e.payload["tool_call_id"]: e.payload["result"]
                for e in result_events} == {"cA": "A-content", "cB": "B-content"}

    def test_events_round_trip_through_replay(self):
        """The strongest property: replaying the journaled events rebuilds
        the transcript the serializer wrote."""
        session, events = self._run([
            ("call", self._call("c0", "read_file", {"path": "a.txt"})),
            ("reply", self._reply("c0", "read_file", "hello")),
        ])
        stamped = [
            type(e)(e.event_type, i + 1, e.payload, e.timestamp)
            for i, e in enumerate(events)
        ]
        replayed = Session(session_id="rt")
        replayed.replay(stamped)

        assert replayed.unknown_events == 0
        assert [(m["role"], m.get("content", "")) for m in replayed.messages] == \
               [(m["role"], m.get("content", "")) for m in session["messages"]]
        assert [t["name"] for t in replayed.tool_calls] == ["read_file"]
        assert replayed.messages[1]["tool_call_id"] == "c0"


# ── 2. Replay round-trip ────────────────────────────────────────────────


class TestReplayRoundTrip:
    def test_replay_reconstructs_the_tool_transcript(self, tmp_path):
        """`load_session()` is the documented crash-recovery replay source.
        For that to be safe, replay must reproduce the conversation."""
        runtime, repo, ws = _runtime(
            _DictProvider([
                _tool_round([_read_call("a.txt", "c0")]),
                _content_only("read it"),
            ]),
            tmp_path, ws_files={"a.txt": "hello"},
        )
        session = _session(ws, "rt1")
        _run_turn(runtime, session)

        replayed = repo.load_session("rt1")
        assert replayed is not None

        live_shape = [(m["role"], m.get("content", ""))
                      for m in session["messages"]]
        replay_shape = [(m["role"], m.get("content", ""))
                        for m in replayed.messages]
        assert replay_shape == live_shape

        # The tool reply is still paired to its call after replay.
        tool_msgs = [m for m in replayed.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].get("tool_call_id") == "c0"
        assistant_with_calls = [
            m for m in replayed.messages
            if m["role"] == "assistant" and m.get("tool_calls")
        ]
        assert assistant_with_calls[0]["tool_calls"][0]["id"] == "c0"

    def test_replay_is_clean_no_unknown_events(self, tmp_path):
        runtime, repo, ws = _runtime(
            _DictProvider([
                _tool_round([_read_call("a.txt", "c0")]),
                _content_only("read it"),
            ]),
            tmp_path, ws_files={"a.txt": "hello"},
        )
        _run_turn(runtime, _session(ws, "rt2"))
        assert repo.load_session("rt2").unknown_events == 0


# ── 3. Session.apply semantics ──────────────────────────────────────────


class TestSessionApply:
    def test_tool_call_is_recorded_in_the_audit_trail(self):
        s = Session(session_id="a")
        s.apply(SessionEvent.tool_call_event(1, "read_file", {"path": "x.py"}))
        assert s.tool_calls == [{
            "name": "read_file", "arguments": {"path": "x.py"},
            "sequence_num": 1, "timestamp": s.tool_calls[0]["timestamp"],
        }]

    def test_tool_call_does_not_corrupt_the_message_transcript(self):
        """A TOOL_CALL must not append a message: the provider requires ONE
        assistant message carrying all of an iteration's tool_calls."""
        s = Session(session_id="a")
        s.apply(SessionEvent.tool_call_event(1, "read_file", {"path": "x.py"}))
        assert s.messages == []

    def test_unknown_event_type_is_counted_not_silently_dropped(self, caplog):
        s = Session(session_id="a")
        bogus = SimpleNamespace(
            event_type="some_future_kind", sequence_num=1,
            payload={}, timestamp=0.0)
        s.apply(bogus)  # type: ignore[arg-type]
        assert s.unknown_events == 1

    def test_replay_resets_the_unknown_counter(self):
        s = Session(session_id="a")
        s.unknown_events = 7
        s.replay([SessionEvent.user_message(1, "hi")])
        assert s.unknown_events == 0

    def test_tool_result_without_id_keeps_legacy_shape(self):
        """Pre-migration events carry no tool_call_id; replaying them must
        not invent one.

        The assertion names only the keys a tool reply may carry. It used to
        also pin a `name` key, which was incidental to this test's purpose —
        the docstring is about `tool_call_id` — and which the live path never
        sets. M9 removed it so replay and the live transcript agree; see
        `test_execution_view_projection.py`. What this test is *for* is
        unchanged: no fabricated pairing id.
        """
        s = Session(session_id="a")
        s.apply(SessionEvent.tool_result_event(1, "read_file", "body"))
        assert s.messages[0] == {"role": "tool", "content": "body"}
        assert "tool_call_id" not in s.messages[0]


# ── 4. Rollback flag restores the old behavior exactly ──────────────────


class TestRollbackFlag:
    def test_flag_off_writes_no_transcript_events(self, tmp_path):
        """`session_event_fidelity` gates the TRANSCRIPT journal. The
        proposal boundary is a separate concern with its own flag (ADR-0002),
        so it is switched off here too — this test is about the journal."""
        runtime, repo, ws = _runtime(
            _DictProvider([
                _tool_round([_read_call("a.txt", "c0")]),
                _content_only("read it"),
            ]),
            tmp_path, ws_files={"a.txt": "hello"},
            session_event_fidelity=False, proposal_boundary=False,
        )
        _run_turn(runtime, _session(ws, "off1"))

        kinds = set(_repo_types(repo, "off1"))
        assert kinds == {"user_message", "done"}, kinds

    def test_fidelity_off_alone_still_records_the_proposal_boundary(self, tmp_path):
        """The two flags must be independently rollable.

        Only the OUTCOME is asserted end-to-end here, and that is a property
        of this environment rather than of the code: `jsonschema` is missing
        (P0 finding F8), so every call is refused before it streams a
        `tool_call` event — and a proposal is recorded only for a call that
        actually reached dispatch. Nothing was proposed, so nothing is
        recorded as proposed. A REJECTION is still recorded, which is exactly
        what the boundary exists to make observable.
        (The proposal-record path is covered directly by
        `TestToolEventJournaling::test_proposal_and_outcome_records_are_produced`.)
        """
        runtime, repo, ws = _runtime(
            _DictProvider([
                _tool_round([_read_call("a.txt", "c0")]),
                _content_only("read it"),
            ]),
            tmp_path, ws_files={"a.txt": "hello"},
            session_event_fidelity=False,
        )
        _run_turn(runtime, _session(ws, "off1b"))

        kinds = set(_repo_types(repo, "off1b"))
        assert "outcome" in kinds
        assert "tool_call" not in kinds and "tool_result" not in kinds

    def test_flag_off_still_writes_the_transcript(self, tmp_path):
        """The flag must not change what the provider sees — only what is
        journaled."""
        runtime, repo, ws = _runtime(
            _DictProvider([
                _tool_round([_read_call("a.txt", "c0")]),
                _content_only("read it"),
            ]),
            tmp_path, ws_files={"a.txt": "hello"},
            session_event_fidelity=False,
        )
        session = _session(ws, "off2")
        _run_turn(runtime, session)

        roles = [m["role"] for m in session["messages"]]
        assert roles == ["user", "assistant", "tool", "assistant"]

    def test_flag_off_turn_still_completes(self, tmp_path):
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("ok")]),
            tmp_path, session_event_fidelity=False,
        )
        evs = _run_turn(runtime, _session(ws, "off3"))
        assert [e.get("type") for e in evs].count("done") == 1
        assert repo.was_last_turn_complete("off3") is True


# ── 5. Trace spans reach the store ──────────────────────────────────────


class TestTraceSpans:
    """`infra/tracing.new_span()` existed with no caller anywhere in the
    tree, and `SQLiteTraceStore` was constructed only by the read-only
    `trace/cli.py`, so `trace_spans` was always empty."""

    def test_a_turn_writes_a_turn_span(self, tmp_path):
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("hi")]), tmp_path,
            with_trace_store=True)
        _run_turn(runtime, _session(ws, "s1"))

        spans = _all_spans(runtime)
        assert len(spans) == 1
        assert spans[0].kind == "turn"
        assert spans[0].status.value == "ok"
        assert spans[0].attrs["session_id"] == "s1"

    def test_span_carries_the_trace_id_from_the_turn(self, tmp_path):
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("hi")]), tmp_path,
            with_trace_store=True)
        _run_turn(runtime, _session(ws, "s2"))

        trace_id = _span_trace_ids(runtime)[0]
        assert trace_id
        assert [s.trace_id for s in runtime.trace_store.query(trace_id)] == \
               [trace_id]

    def test_failed_turn_span_is_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WISP_STREAM_ATTEMPTS", "1")
        runtime, repo, ws = _runtime(
            _DictProvider([_hang()]), tmp_path, with_trace_store=True)
        _run_turn(runtime, _session(ws, "s3"))

        spans = _all_spans(runtime)
        assert spans, "a failed turn must still leave a span"
        assert spans[0].status.value == "error"

    def test_no_trace_store_writes_nothing(self, tmp_path):
        """The default (no store injected) must stay inert."""
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("hi")]), tmp_path)
        _run_turn(runtime, _session(ws, "s4"))

        assert _span_count(runtime) == 0

    def test_turn_spans_flag_off_writes_nothing(self, tmp_path):
        runtime, repo, ws = _runtime(
            _DictProvider([_content_only("hi")]), tmp_path,
            with_trace_store=True, turn_spans=False)
        _run_turn(runtime, _session(ws, "s5"))

        assert _span_count(runtime) == 0


# ── 7. The store reaches both construction sites ────────────────────────

class TestRunStoreReachability:
    def test_composition_root_injects_a_run_store(self, tmp_path):
        """The defect P0 exists to close: the manager accepted a run store
        and fully implemented persistence, but no production site passed
        one, so every `_persist_*` returned early."""
        from wisp.composition import CompositionRoot

        config = WispConfig().replace(workspace=str(tmp_path), provider="ollama")
        root = CompositionRoot(config=config)
        try:
            assert root.run_store is not None
            assert root.background_agents._run_store is root.run_store
            assert root.background_agents._scheduler is not None
        finally:
            root.shutdown()

    def test_composition_root_injects_a_trace_store(self, tmp_path):
        """Same defect, second instance: `SQLiteTraceStore` was reachable
        only from the read-only trace CLI."""
        from wisp.composition import CompositionRoot

        config = WispConfig().replace(workspace=str(tmp_path), provider="ollama")
        root = CompositionRoot(config=config)
        try:
            assert root.trace_store is not None
            assert root.runtime.trace_store is root.trace_store
        finally:
            root.shutdown()

    def test_composition_root_flag_off_yields_no_store(self, tmp_path):
        from wisp.composition import CompositionRoot

        config = WispConfig().replace(
            workspace=str(tmp_path), provider="ollama",
            durable_runs=False, turn_spans=False)
        root = CompositionRoot(config=config)
        try:
            assert root.run_store is None
            assert root.trace_store is None
            assert root.background_agents._run_store is None
            assert root.background_agents._scheduler is None
            assert root.runtime.trace_store is None
        finally:
            root.shutdown()

    def test_lazy_fallback_manager_inherits_the_store(self, tmp_path):
        """`_get_background_manager()` builds a manager when only an
        orchestrator was wired. It must not silently degrade to in-memory."""
        from wisp.multi_agent.background import BackgroundAgentManager
        from wisp.tool_executor import ToolExecutor

        store = UnifiedStore(tmp_path / "wisp.db")
        from wisp.runs.store import SQLiteRunStore
        run_store = SQLiteRunStore(store)

        executor = ToolExecutor(
            config=WispConfig().replace(workspace=str(tmp_path)),
            subagent_orchestrator=SimpleNamespace(),
            run_store=run_store,
        )
        mgr = executor._get_background_manager()
        assert isinstance(mgr, BackgroundAgentManager)
        assert mgr._run_store is run_store
        executor._tool_pool.shutdown(wait=False)
        executor._network_pool.shutdown(wait=False)

    def test_manager_persists_a_run_row_end_to_end(self, tmp_path):
        """Reachability in the strongest form: drive the real manager and
        assert the durable row exists."""
        from wisp.multi_agent.background import BackgroundAgentManager
        from wisp.multi_agent.task import SubagentContract
        from wisp.runs.store import SQLiteRunStore

        store = UnifiedStore(tmp_path / "wisp.db")
        run_store = SQLiteRunStore(store)

        class _Result:
            success = True
            output = "done"
            files_changed: list = []
            session_id = ""
            error = None
            elapsed_seconds = 0.1

        class _Orch:
            async def _run_with_retry(self, contract):
                return _Result()

        mgr = BackgroundAgentManager(_Orch(), run_store=run_store)

        async def _main():
            launch = await mgr.launch(SubagentContract(
                name="p0", role="generalist", task="do a thing",
                workspace=str(tmp_path)))
            assert launch["ok"] is True
            await mgr.result(launch["agent_id"], wait_seconds=5.0)
            return launch["agent_id"]

        agent_id = asyncio.run(_main())

        rec = run_store.get(agent_id)
        assert rec is not None
        assert rec.prompt == "do a thing"
        # Terminal state reached and journaled as a legal transition chain.
        transitions = run_store.transitions(agent_id)
        assert [t.to_state for t in transitions] == ["running", "succeeded"]
