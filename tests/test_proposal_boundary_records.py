"""Migration P2 — the proposal boundary produces `ToolRequest` / `ToolResult`.

`wisp/contracts/tool.py` defined `ToolRequest` and `ToolResult` with the right
shape — statuses, block reasons, an idempotency key — and **no production
producer or consumer**. Only the package re-export and `test_contracts_tool.py`
referenced them.

These tests pin that the boundary now has both: a proposal for every tool call
that reaches dispatch, and an outcome for every proposal **including
rejections**, joinable by the P1 idempotency key.

The load-bearing property is in `TestAuditOnly`: the records must not touch
`messages`. The transcript is rebuilt from
`ASSISTANT_MESSAGE(tool_calls=…)` + `TOOL_RESULT`; a second path in would
duplicate every tool reply on replay.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from wisp.config import WispConfig
from wisp.core.proposal import (
    BLOCK_REASON,
    OUTCOME_STATUS,
    build_outcome,
    build_proposal,
    is_refusal,
)
from wisp.core.session import Session, SessionEvent, SessionEventType


# ── The records themselves ──────────────────────────────────────────────


class TestBuildProposal:
    def test_proposal_carries_the_call_identity(self):
        req = build_proposal("c1", "read_file", {"path": "a.py"},
                             action_key="k1")
        assert req.tool_call_id == "c1"
        assert req.name == "read_file"
        assert req.args == {"path": "a.py"}
        assert req.idempotency_key == "k1"

    def test_proposal_round_trips_through_the_contract(self):
        req = build_proposal("c1", "read_file", {"path": "a.py"},
                             action_key="k1")
        from wisp.contracts.tool import ToolRequest
        assert ToolRequest.from_dict(req.to_dict()) == req

    def test_non_dict_args_are_wrapped_not_dropped(self):
        req = build_proposal("c1", "t", "raw-string")
        assert req.args == {"raw": "raw-string"}

    def test_empty_ids_are_tolerated(self):
        req = build_proposal("", "", {})
        assert req.tool_call_id == "" and req.name == ""


class TestBuildOutcome:
    def test_success_maps_to_ok(self):
        out = build_outcome("c1", "read_file", '{"status": "ok"}')
        assert out.status == "ok"
        assert out.metadata["outcome_class"] == "success"
        assert out.block_reason == ""

    def test_policy_denial_maps_to_denied_with_a_block_reason(self):
        out = build_outcome(
            "c1", "write_file",
            '{"status": "POLICY_DENIED", "reason": "nope"}')
        assert out.status == "denied"
        assert out.block_reason == "permission"
        assert out.metadata["outcome_class"] == "policy_denial"

    def test_plain_error_maps_to_error(self):
        out = build_outcome("c1", "t", '{"status": "error"}')
        assert out.status == "error"
        assert out.metadata["outcome_class"] == "error"

    def test_status_always_in_the_frozen_vocabulary(self):
        """Every `OutcomeClass` must be representable, or an outcome could be
        silently un-recordable."""
        from wisp.contracts.tool import STATUSES
        from wisp.core.events import OutcomeClass
        assert set(OUTCOME_STATUS) == set(OutcomeClass)
        assert set(OUTCOME_STATUS.values()) <= set(STATUSES)

    def test_block_reason_always_in_the_frozen_vocabulary(self):
        from wisp.contracts.tool import BLOCK_REASONS
        assert set(BLOCK_REASON.values()) <= set(BLOCK_REASONS)

    def test_classification_delegates_to_the_canonical_authority(self):
        """A second classifier is what let benchmark error accounting miss
        every structured denial. Classification must go through
        `core.events.classify_result`."""
        from wisp.core.events import classify_result
        payload = '{"status": "SCHEMA_INVALID"}'
        out = build_outcome("c1", "t", payload)
        assert out.metadata["outcome_class"] == classify_result(payload).value

    def test_synthesized_placeholder_is_flagged(self):
        out = build_outcome("c1", "t", "[no result recorded before turn ended]",
                            synthesized=True)
        assert out.metadata["synthesized"] is True

    def test_outcome_round_trips_through_the_contract(self):
        out = build_outcome("c1", "t", '{"status": "ok"}')
        from wisp.contracts.tool import ToolResult
        assert ToolResult.from_dict(out.to_dict()) == out

    @pytest.mark.parametrize("payload", [
        '{"status": "POLICY_DENIED"}',
        '{"status": "USER_DENIED"}',
        '{"status": "APPROVAL_TIMEOUT"}',
        '{"status": "CANCELLED"}',
        '{"status": "SCHEMA_INVALID"}',
    ])
    def test_every_refusal_shape_is_recognised(self, payload):
        assert is_refusal(build_outcome("c1", "t", payload))

    def test_success_is_not_a_refusal(self):
        assert not is_refusal(build_outcome("c1", "t", '{"status": "ok"}'))


# ── Audit-only: the transcript is untouched ─────────────────────────────


class TestAuditOnly:
    def test_proposal_adds_no_message(self):
        s = Session(session_id="a")
        s.apply(SessionEvent.proposal_event(1, {"tool_call_id": "c1"}))
        assert s.messages == []
        assert len(s.proposals) == 1

    def test_outcome_adds_no_message(self):
        """The reply reaches the transcript through TOOL_RESULT. An OUTCOME
        that also appended would duplicate every tool reply on replay."""
        s = Session(session_id="a")
        s.apply(SessionEvent.outcome_event(
            1, {"tool_call_id": "c1", "status": "ok"}))
        assert s.messages == []
        assert len(s.outcomes) == 1

    def test_records_survive_replay(self):
        events = [
            SessionEvent.user_message(1, "go"),
            SessionEvent.proposal_event(2, {"tool_call_id": "c1",
                                            "name": "read_file"}),
            SessionEvent.assistant_message(3, "", [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}]),
            SessionEvent.tool_result_event(4, "read_file", "body",
                                           tool_call_id="c1"),
            SessionEvent.outcome_event(5, {"tool_call_id": "c1",
                                           "status": "ok"}),
        ]
        s = Session(session_id="a")
        s.replay(events)
        assert s.unknown_events == 0
        assert [p["name"] for p in s.proposals] == ["read_file"]
        assert [o["status"] for o in s.outcomes] == ["ok"]
        # …and the transcript is exactly the three conversational messages.
        assert [m["role"] for m in s.messages] == ["user", "assistant", "tool"]

    def test_replay_resets_the_records(self):
        s = Session(session_id="a")
        s.apply(SessionEvent.proposal_event(1, {"tool_call_id": "c1"}))
        s.apply(SessionEvent.outcome_event(2, {"tool_call_id": "c1"}))
        s.replay([SessionEvent.user_message(1, "hi")])
        assert s.proposals == [] and s.outcomes == []


# ── End to end through a real turn ──────────────────────────────────────


def _read_call(path: str, cid: str) -> dict:
    return {"id": cid, "type": "function",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": path})}}


class _ToolProvider:
    def __init__(self, path: str = "a.txt"):
        self._path, self.calls = path, 0

    def generate_stream_events(self, system_prompt, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            yield {"type": "tool_calls", "calls": [_read_call(self._path, "c0")]}
            yield {"type": "done", "done_reason": "tool_calls"}
            return
        yield {"type": "content", "text": "done"}
        yield {"type": "done", "done_reason": "stop"}


def _runtime(tmp_path, **cfg_kw):
    from wisp.core.engine import WispAgentCore
    from wisp.core.runtime import AgentRuntime
    from wisp.core.session_repo import SessionRepository
    from wisp.infra.extensions import ExtensionHost
    from wisp.infra.security import SecurityPolicy
    from wisp.infra.store import UnifiedStore
    from wisp.infra.telemetry import Telemetry

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "a.txt").write_text("hello")
    config = WispConfig().replace(workspace=str(ws), **cfg_kw)
    store = UnifiedStore(tmp_path / "wisp.db")
    repo = SessionRepository(store)
    provider = _ToolProvider()

    def factory():
        return WispAgentCore(config=config, provider=provider,
                             security=SecurityPolicy(), tool_executor=None)

    runtime = AgentRuntime(
        store=store, security=SecurityPolicy(), extensions=ExtensionHost(),
        telemetry=Telemetry(), core_factory=factory,
        session_repo=repo, config=config)
    return runtime, repo, str(ws)


def _turn(runtime, ws, sid="pb"):
    session = {"id": sid, "model": "mock", "workspace": ws, "messages": []}

    async def _main():
        return [ev async for ev in runtime.run_turn(session, prompt="go")]
    asyncio.run(_main())
    return session


class TestBoundaryEndToEnd:
    def test_an_outcome_is_recorded_for_every_proposal(self, tmp_path):
        """Including rejections — which is the only case this environment can
        produce end-to-end, because `jsonschema` is missing (P0 F8) so every
        call is refused before it reaches dispatch."""
        runtime, repo, ws = _runtime(tmp_path)
        _turn(runtime, ws, "pb1")

        kinds = [str(e.event_type) for e in repo.load_events("pb1")]
        assert SessionEventType.OUTCOME.value in kinds, kinds

    def test_the_outcome_records_a_refusal_as_first_class(self, tmp_path):
        runtime, repo, ws = _runtime(tmp_path)
        _turn(runtime, ws, "pb2")

        outcomes = [e for e in repo.load_events("pb2")
                    if e.event_type == SessionEventType.OUTCOME]
        assert outcomes, "no outcome recorded"
        result = outcomes[0].payload["result"]
        assert result["status"] in ("denied", "error")
        assert result["metadata"]["outcome_class"] in (
            "invalid", "policy_denial", "error")
        # A rejection names a reason, so it is diagnosable without the log.
        assert result["block_reason"] in ("permission", "")

    def test_flag_off_records_no_boundary_events(self, tmp_path):
        runtime, repo, ws = _runtime(tmp_path, proposal_boundary=False)
        _turn(runtime, ws, "pb3")

        kinds = [str(e.event_type) for e in repo.load_events("pb3")]
        assert SessionEventType.PROPOSAL.value not in kinds
        assert SessionEventType.OUTCOME.value not in kinds

    def test_boundary_records_do_not_break_replay(self, tmp_path):
        runtime, repo, ws = _runtime(tmp_path)
        session = _turn(runtime, ws, "pb4")

        replayed = repo.load_session("pb4")
        assert replayed is not None
        assert replayed.unknown_events == 0
        assert [(m["role"], m.get("content", "")) for m in replayed.messages] == \
               [(m["role"], m.get("content", "")) for m in session["messages"]]

    def test_sequence_numbers_stay_gapless_with_the_boundary_on(self, tmp_path):
        runtime, repo, ws = _runtime(tmp_path)
        _turn(runtime, ws, "pb5")

        seqs = [e.sequence_num for e in repo.load_events("pb5")]
        assert seqs == list(range(len(seqs))), seqs
