"""Replay verifies; it does not assume — the forcing tests.

`wisp/core/replay_digest.py` records a digest of the transcript at turn end and
checks it during replay. This file is what makes that a *control* rather than a
decoration: every property has a case where the thing it prevents **would** have
happened.

The defect class is **F25**: *"The replayed transcript was not the live
transcript. `Session.apply` added a `name` key to every tool reply that
`_exchange_parts` never sets … The journal and the blob therefore disagreed on
the same session."* ADR-0029 fixed that instance and asserted equality **in a
test**. A test holds for the turn it drives and says nothing about the next one;
this is the runtime check.

The divergence tests are the load-bearing ones. A guard for a check like this is
easy to write so that it can never fail — see `TestTheCheckIsNotVacuous`.
"""
from __future__ import annotations

import asyncio

import pytest

from wisp.core.replay_digest import (
    REPLAY_DIGEST_KEY,
    ReplayDivergence,
    canonical_message,
    projection,
    projection_digest,
    verify,
)
from wisp.core.session import Session, SessionEvent, SessionEventType


# ── The projection is canonical ─────────────────────────────────────────


class TestTheProjection:
    def test_it_keeps_the_provider_visible_fields(self):
        msg = {"role": "assistant", "content": "hi", "tool_call_id": "c1"}
        assert canonical_message(msg) == msg

    def test_it_drops_keys_that_are_not_part_of_the_prompt(self):
        """A difference in a non-prompt key is not a divergence.

        Including one would make the check fire on bookkeeping, which is how a
        guard trains its reader to ignore it (F92's class).
        """
        a = canonical_message({"role": "user", "content": "x", "timestamp": 1.0})
        b = canonical_message({"role": "user", "content": "x", "timestamp": 9.9})
        assert a == b

    def test_it_keeps_tool_calls_in_order(self):
        """Two calls in a different order are a different prompt.

        Sorting them would hide a reordering — and a reorder is exactly what a
        replay that grouped exchanges differently would produce.
        """
        calls = [{"id": "a", "name": "read", "arguments": {"p": 1}},
                 {"id": "b", "name": "write", "arguments": {"p": 2}}]
        forward = projection_digest([{"role": "assistant", "content": "",
                                      "tool_calls": calls}])
        reversed_ = projection_digest([{"role": "assistant", "content": "",
                                        "tool_calls": list(reversed(calls))}])
        assert forward != reversed_, "the projection ignores tool-call order"

    def test_it_covers_the_arguments_a_call_was_made_with(self):
        """A replay that lost the arguments would reproduce a call the model
        never made — and a digest that ignored them could not tell."""
        a = projection_digest([{"role": "assistant", "content": "", "tool_calls": [
            {"id": "c", "name": "read", "arguments": {"path": "a.txt"}}]}])
        b = projection_digest([{"role": "assistant", "content": "", "tool_calls": [
            {"id": "c", "name": "read", "arguments": {"path": "b.txt"}}]}])
        assert a != b

    def test_the_digest_is_stable_across_key_order(self):
        """Dict insertion order is not meaning; the digest must not see it."""
        a = projection_digest([{"role": "user", "content": "x"}])
        b = projection_digest([{"content": "x", "role": "user"}])
        assert a == b

    def test_the_digest_changes_when_a_message_changes(self):
        assert (projection_digest([{"role": "user", "content": "a"}])
                != projection_digest([{"role": "user", "content": "b"}]))

    def test_the_digest_changes_when_a_message_is_lost(self):
        """A dropped TOOL_RESULT is the pre-M4 hazard: replay applies what
        exists and silently produces a shorter transcript."""
        full = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": "hi"}]
        assert projection_digest(full) != projection_digest(full[:1])

    def test_it_has_a_floor(self):
        """A projection over an empty transcript still digests, but the suite
        must not be able to pass by checking nothing at all."""
        assert projection([]) == []
        assert len(projection([{"role": "user", "content": "x"}])) == 1


# ── The divergence is raised, and it is the right exception ─────────────


class TestTheDivergenceIsRaised:
    def test_verify_raises_on_a_mismatch(self):
        with pytest.raises(ReplayDivergence):
            verify([{"role": "user", "content": "x"}], "0" * 32)

    def test_verify_is_silent_on_a_match(self):
        msgs = [{"role": "user", "content": "x"}]
        verify(msgs, projection_digest(msgs))  # must not raise

    def test_the_message_names_both_digests(self):
        """A failure that says only "mismatch" sends the reader to re-derive
        which side moved."""
        msgs = [{"role": "user", "content": "x"}]
        with pytest.raises(ReplayDivergence) as excinfo:
            verify(msgs, "deadbeef" * 4, session_id="s1", sequence=7)
        text = str(excinfo.value)
        assert "deadbeef" in text, "the recorded digest is not named"
        assert projection_digest(msgs) in text, "the replayed digest is not named"
        assert "s1" in text and "7" in text, "the location is not named"

    def test_it_is_a_plain_runtime_error(self):
        """The runtime skill is explicit: a divergence must ESCAPE the loop.

        The loop catches its own error type and turns it into a taxonomy row. If
        a divergence were that type it would be reported as *one more way a run
        can end* — which is the opposite of the point, because the whole reason
        to replay is to be able to trust the result.
        """
        assert issubclass(ReplayDivergence, RuntimeError)
        from wisp.core.contracts import WispError
        assert not issubclass(ReplayDivergence, WispError), (
            "ReplayDivergence is the run-level error type, so the loop will "
            "swallow it and report it as a run outcome")


# ── The check is wired into replay ──────────────────────────────────────


def _msgs():
    return [{"role": "user", "content": "go"},
            {"role": "assistant", "content": "hi"}]


class TestTheCheckRunsDuringReplay:
    def test_a_faithful_journal_replays_without_raising(self):
        msgs = _msgs()
        s = Session(session_id="ok")
        s.replay([
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "hi"),
            SessionEvent.replay_digest_event(3, projection_digest(msgs)),
        ])
        assert s.messages == msgs

    def test_a_tampered_digest_raises(self):
        """**The forcing case.** The transcript is correct and the record is
        wrong, so nothing but the digest check can catch it."""
        s = Session(session_id="tampered")
        with pytest.raises(ReplayDivergence):
            s.replay([
                SessionEvent.user_message(1, "go"),
                SessionEvent.assistant_message(2, "hi"),
                SessionEvent.replay_digest_event(3, "0" * 32),
            ])

    def test_a_diverged_transcript_raises(self):
        """The **F25** shape: the record is honest about the turn, and the
        replayed transcript is not the one the turn ran on.

        Reproduced by digesting the live transcript and then replaying events
        that produce a *different* one — an assistant message whose tool reply
        gained a key, which is literally what `Session.apply` did in F25.
        """
        live = [{"role": "user", "content": "go"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "name": "read", "arguments": {"p": "a"}}]},
                {"role": "tool", "content": "body", "tool_call_id": "c1"}]
        recorded = projection_digest(live)

        s = Session(session_id="f25")
        s.messages.append({"role": "user", "content": "go"})
        s.messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "name": "read", "arguments": {"p": "a"}}]})
        # The reply the replay produced carries an extra prompt-visible key the
        # live turn never set.
        s.messages.append({"role": "tool", "content": "body",
                           "tool_call_id": "c1", "name": "read"})

        with pytest.raises(ReplayDivergence):
            verify(s.messages, recorded, session_id="f25")

    def test_a_missing_digest_key_raises_with_its_own_diagnosis(self):
        """An unreadable record is a divergence, not a pass — a record nothing
        can check is worse than no record, because it looks like one.

        **The message is asserted, and that is the point.** `verify` would also
        reject an unreadable record (a `None` never equals a digest), so the
        *type* alone cannot tell the two checks apart — deleting this branch left
        the type-only assertion green. The two diagnoses are different and a
        reader needs to know which one fired: *"the record is unreadable"* is a
        journal defect, *"the transcript diverged"* is an `apply` defect.
        """
        s = Session(session_id="empty")
        with pytest.raises(ReplayDivergence) as excinfo:
            s.replay([
                SessionEvent.user_message(1, "go"),
                SessionEvent(SessionEventType.REPLAY_DIGEST, 2, {}),
            ])
        assert "unreadable" in str(excinfo.value), (
            "the unreadable-record branch did not fire — the failure came from "
            "`verify` instead, which reports a divergence rather than a "
            f"malformed record: {excinfo.value}")

    def test_the_digest_event_does_not_enter_the_transcript(self):
        """It is AUDIT-ONLY in the transcript sense: it contributes no message,
        so recording it cannot itself change the thing it digests."""
        msgs = _msgs()
        s = Session(session_id="audit-only")
        s.replay([
            SessionEvent.user_message(1, "go"),
            SessionEvent.assistant_message(2, "hi"),
            SessionEvent.replay_digest_event(3, projection_digest(msgs)),
        ])
        assert s.messages == msgs
        assert s.unknown_events == 0, (
            "the digest event fell through to the unknown-type wildcard — the "
            "branch that fails loud on an unrecognised event")


# ── The check is not vacuous ────────────────────────────────────────────


class TestTheCheckIsNotVacuous:
    def test_a_guard_that_never_runs_would_pass_this_suite(self):
        """The floor. If no turn journals a digest, every test above still
        passes — they construct their own events. So assert the *production*
        path writes one, end to end, or the check is dark."""
        import pathlib
        import tempfile

        from wisp.config import WispConfig
        from wisp.core.engine import WispAgentCore
        from wisp.core.runtime import AgentRuntime
        from wisp.core.session_repo import SessionRepository
        from wisp.infra.extensions import ExtensionHost
        from wisp.infra.security import SecurityPolicy
        from wisp.infra.store import UnifiedStore
        from wisp.infra.telemetry import Telemetry

        class _P:
            def generate_stream_events(self, system_prompt, messages, tools=None):
                yield {"type": "content", "text": "hi"}
                yield {"type": "done", "done_reason": "stop"}

        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            ws = tmp / "ws"
            ws.mkdir()
            config = WispConfig().replace(workspace=str(ws))
            store = UnifiedStore(tmp / "wisp.db")
            repo = SessionRepository(store)

            runtime = AgentRuntime(
                store=store, security=SecurityPolicy(),
                extensions=ExtensionHost(), telemetry=Telemetry(),
                core_factory=lambda: WispAgentCore(
                    config=config, provider=_P(),
                    security=SecurityPolicy(), tool_executor=None),
                session_repo=repo, config=config)
            session = {"id": "digest-e2e", "model": "mock",
                       "workspace": str(ws), "messages": []}

            asyncio.run(_drain(runtime, session))

            events = repo.load_events("digest-e2e")
            digests = [e for e in events
                       if e.event_type == SessionEventType.REPLAY_DIGEST]
            assert digests, (
                "a real turn journaled NO REPLAY_DIGEST — the check is dark, and "
                "every divergence test in this file would pass without it")
            assert digests[0].payload.get(REPLAY_DIGEST_KEY)

            # And the real journal replays clean: the check passes on the
            # production path, not only on hand-built events.
            out = repo.reconstruct("digest-e2e")
            assert out["_source"] == "journal"
            assert any(m["role"] == "assistant" for m in out["messages"])


async def _drain(runtime, session):
    return [ev async for ev in runtime.run_turn(session, prompt="go")]
